"""Revenue recovery: waitlist refill, care-plan reminders, no-show deposits, weekly report."""
from datetime import datetime

import pytest

from bookings import db, payments, recovery
from bookings.agent import Agent
from bookings.channels.whatsapp import Inbound
from bookings.seed import seed
from bookings.slots import book
from tests.fakes import FakeLLM, text, tool

OWNER, PRIYA, ARJUN, NEHA, KARAN = "910000000000", "919800000001", "919800000002", "919800000003", "919800000004"
ADJ, CONSULT = 2, 1  # service ids in the clinic seed (adjustment: follow-up every 2 weeks)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "r.db")
    db.init(conn)
    seed(conn, "clinic", OWNER)
    return conn, Clock(datetime(2026, 10, 13, 15, 0)), []  # Tue 13 Oct 15:00


def agent(env, *steps):
    conn, clock, sent = env
    llm = FakeLLM(*steps)
    return Agent(conn, 1, llm, send=lambda to, body: sent.append((to, body)), now=clock), llm


def say(a, sender, body, mid, name="P"):
    return a.handle(Inbound(sender=sender, name=name, text=body, msg_id=mid))


def recovered(conn):
    return [tuple(r) for r in conn.execute("SELECT kind, amount_inr FROM recoveries ORDER BY id")]


def test_cancelled_slot_is_offered_to_the_waitlist_and_the_refill_counts_as_recovered(env):
    conn, _, sent = env
    for wa in (NEHA, KARAN):  # both practitioners busy tomorrow 18:00
        book(conn, 1, ADJ, datetime(2026, 10, 14, 18, 0), customer_wa=wa, customer_name=wa[-1])
    neha_booking = conn.execute("SELECT b.id FROM bookings b JOIN customers c ON c.id = b.customer_id WHERE c.wa_id = ?", (NEHA,)).fetchone()[0]
    a, llm = agent(env,
                   tool("join_waitlist", service_id=ADJ, date="2026-10-14"), text("Waitlist pe daal diya."),  # Priya
                   tool("cancel_booking", booking_id=neha_booking), text("Cancelled."),                     # Neha cancels
                   text("Kal 6 baje ka slot khul gaya hai! Book karun?"),                                  # offer to Priya
                   tool("book", service_id=ADJ, date="2026-10-14", time="18:00"), text("Booked!"))          # Priya accepts
    say(a, PRIYA, "kal 6 baje full hai? waitlist pe daal do", "p1", "Priya")
    say(a, NEHA, "cancel my appointment", "n1", "Neha")
    assert "slot just opened on Wed 2026-10-14 at 18:00" in llm.calls[4][1][-1]["content"]
    assert sent[-1] == (PRIYA, "Kal 6 baje ka slot khul gaya hai! Book karun?")
    say(a, PRIYA, "haan", "p2", "Priya")
    assert llm.tool_results(6)[0]["status"] == "booked"
    row = conn.execute("SELECT source FROM bookings WHERE customer_id = (SELECT id FROM customers WHERE wa_id = ?)", (PRIYA,)).fetchone()
    assert row["source"] == "waitlist" and recovered(conn) == [("waitlist_fill", 1200)]
    assert conn.execute("SELECT status FROM waitlist").fetchone()[0] == "booked"


def test_cancelling_a_recovered_booking_removes_the_credit(env):
    conn, clock, _ = env
    conn.execute("INSERT INTO customers (business_id, wa_id, name, last_nudge_at) VALUES (1, ?, 'Arjun', '2026-10-12T11:00')", (ARJUN,))
    a, llm = agent(env, tool("book", service_id=ADJ, date="2026-10-15", time="11:00"), text("Booked"),
                   tool("cancel_booking", booking_id=1), text("Cancelled"))
    say(a, ARJUN, "book thursday 11", "a1")
    assert recovered(conn) == [("nudge", 1200)]
    say(a, ARJUN, "cancel", "a2")
    assert recovered(conn) == []


def returning_arjun(conn, last_visit=datetime(2026, 9, 28, 11, 0)):
    book(conn, 1, ADJ, last_visit, customer_wa=ARJUN, customer_name="Arjun")  # 15 days ago; follow-up due after 14


def test_overdue_patient_gets_one_reminder_and_a_booking_after_it_is_credited(env):
    conn, clock, sent = env
    returning_arjun(conn)
    a, llm = agent(env, text("Arjun ji, follow-up ka time ho gaya hai. Kal ka slot book karun?"),
                   tool("book", service_id=ADJ, date="2026-10-14", time="11:00"), text("Booked!"))
    assert a.tick()["nudges"] == 1
    assert "follow-up every 2 weeks" in llm.calls[0][1][-1]["content"] and sent[-1][0] == ARJUN
    assert a.tick()["nudges"] == 0  # cooldown
    say(a, ARJUN, "haan kal 11 baje", "a1", "Arjun")
    assert recovered(conn) == [("nudge", 1200)]


def test_no_reminders_at_night_or_for_patients_already_booked(env):
    conn, clock, _ = env
    returning_arjun(conn)
    a, llm = agent(env)
    clock.t = datetime(2026, 10, 13, 21, 0)
    assert a.tick()["nudges"] == 0
    clock.t = datetime(2026, 10, 14, 10, 0)
    book(conn, 1, ADJ, datetime(2026, 10, 16, 10, 0), customer_wa=ARJUN, customer_name="Arjun")
    assert a.tick()["nudges"] == 0 and llm.calls == []


def no_show_arjun(conn):
    b = book(conn, 1, ADJ, datetime(2026, 9, 19, 11, 0), customer_wa=ARJUN, customer_name="Arjun")
    conn.execute("UPDATE bookings SET status = 'no_show' WHERE id = ?", (b.id,))


def test_after_a_no_show_the_next_booking_is_held_for_a_deposit_until_paid(env, monkeypatch):
    conn, clock, sent = env
    no_show_arjun(conn)
    links = []
    monkeypatch.setattr(payments, "create_link", lambda link_id, amount, wa, name, purpose: links.append((link_id, amount, wa)) or "https://pay/x")
    paid = {"v": False}
    monkeypatch.setattr(payments, "link_paid", lambda link_id: paid["v"])
    a, llm = agent(env, tool("book", service_id=ADJ, date="2026-10-14", time="11:00"), text("Pay ₹300 here: https://pay/x"),
                   text("Deposit mil gaya, appointment confirmed!"))
    say(a, ARJUN, "kal 11 baje", "a1", "Arjun")
    res = llm.tool_results(1)[0]
    assert res["status"] == "held_pending_deposit" and res["pay_link"] == "https://pay/x" and links[0][1] == 300
    assert a.tick()["deposits_paid"] == 0  # not paid yet, not expired
    paid["v"] = True
    assert a.tick()["deposits_paid"] == 1
    assert ("deposit", 300) in recovered(conn) and sent[-1] == (ARJUN, "Deposit mil gaya, appointment confirmed!")
    assert conn.execute("SELECT deposit_status FROM bookings WHERE id = 2").fetchone()[0] == "paid"


def test_unpaid_hold_is_released_after_two_hours_and_offered_to_the_waitlist(env, monkeypatch):
    conn, clock, sent = env
    no_show_arjun(conn)
    monkeypatch.setattr(payments, "create_link", lambda *a: "https://pay/x")
    monkeypatch.setattr(payments, "link_paid", lambda link_id: False)
    book(conn, 1, ADJ, datetime(2026, 10, 14, 11, 0), customer_wa=NEHA, customer_name="Neha")  # practitioner 1 busy
    a, llm = agent(env, tool("book", service_id=ADJ, date="2026-10-14", time="11:00"), text("pay link"),
                   tool("join_waitlist", service_id=ADJ, date="2026-10-14"), text("waitlisted"),
                   text("Slot released, book again anytime."), text("11 baje ka slot khula hai, chahiye?"))
    say(a, ARJUN, "kal 11", "a1", "Arjun")
    say(a, PRIYA, "kal 11 full? waitlist", "p1", "Priya")
    clock.t = datetime(2026, 10, 13, 17, 1)
    assert a.tick()["holds_released"] == 1
    assert conn.execute("SELECT status, deposit_status FROM bookings WHERE id = 3").fetchone()[:] == ("cancelled", "expired")
    assert [to for to, _ in sent[-2:]] == [ARJUN, PRIYA]  # Arjun told; Priya offered the freed slot


def test_payment_provider_down_still_books(env, monkeypatch):
    conn, _, _ = env
    no_show_arjun(conn)

    def down(*a):
        raise TimeoutError
    monkeypatch.setattr(payments, "create_link", down)
    a, llm = agent(env, tool("book", service_id=ADJ, date="2026-10-14", time="11:00"), text("Booked"))
    say(a, ARJUN, "kal 11", "a1")
    assert llm.tool_results(1)[0]["status"] == "booked"


def test_weekly_report_goes_to_the_owner_on_monday_once(env):
    conn, clock, sent = env
    b = book(conn, 1, ADJ, datetime(2026, 10, 14, 11, 0), customer_wa=PRIYA, customer_name="Priya")
    conn.execute("INSERT INTO recoveries (business_id, booking_id, kind, amount_inr, at) VALUES (1, ?, 'waitlist_fill', 1200, '2026-10-14T10:00')", (b.id,))
    a, _ = agent(env)
    clock.t = datetime(2026, 10, 19, 9, 30)  # Monday
    assert a.tick()["report"] is True
    assert sent[-1][0] == OWNER and "Recovered: ₹1,200" in sent[-1][1] and "waitlist: 1 (₹1,200)" in sent[-1][1]
    assert a.tick()["report"] is False
    assert a.tick(force_report=True)["report"] is True


def test_owner_marks_a_no_show_only_after_the_appointment(env):
    conn, clock, _ = env
    book(conn, 1, ADJ, datetime(2026, 10, 13, 11, 0), customer_wa=ARJUN, customer_name="Arjun")
    book(conn, 1, ADJ, datetime(2026, 10, 14, 11, 0), customer_wa=NEHA, customer_name="Neha")
    a, llm = agent(env, [tool("mark_no_show", booking_id=1), tool("mark_no_show", booking_id=2)], text("Marked."))
    say(a, OWNER, "Arjun aaj nahi aaya", "o1")
    ok, early = llm.tool_results(1)
    assert ok["status"] == "no_show" and "error" in early
    assert recovery.needs_deposit(conn, 1, ARJUN, clock.t)

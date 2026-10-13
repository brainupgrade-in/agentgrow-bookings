import json
from datetime import datetime

import pytest

from bookings import db, onboarding
from bookings.agent import Agent
from bookings.channels.whatsapp import Inbound
from bookings.slots import book
from tests.fakes import FakeLLM, text, tool

NOW = datetime(2026, 10, 13, 15, 0)
OWNER, PRIYA = "919900000000", "919800000001"
CARD = json.dumps({"services": [{"name": "Adjustment (Follow-up)", "price_inr": 350, "duration_min": 30},
                                {"name": "Spinal Decompression", "price_inr": "4500", "duration_min": 120}]})


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    db.init(c)
    biz = db.add_business(c, name="New Clinic", owner_wa=OWNER, open_time="10:00", close_time="20:00")
    db.add_service(c, biz, name="Adjustment (Follow-up)", price_inr=300, duration_min=30)
    db.add_service(c, biz, name="Old Massage", price_inr=900, duration_min=60)
    return c


def agent(conn, llm):
    return Agent(conn, 1, llm, send=lambda to, body: None, now=lambda: NOW)


def photo(sender=OWNER, mid="p1", caption=""):
    return Inbound(sender=sender, name="", text=caption, msg_id=mid, image=b"\xff\xd8jpeg")


def test_parse_services_keeps_valid_rows_only():
    raw = "```json\n" + json.dumps({"services": [{"name": "Cut", "price_inr": "200"}, {"name": "", "price_inr": 5},
                                                 {"name": "Free tea", "price_inr": 0}, {"price_inr": 9}]}) + "\n```"
    assert onboarding.parse_services(raw) == [{"name": "Cut", "price_inr": 200, "duration_min": 30}]
    assert onboarding.parse_services("sorry, I can't read that") == []


def test_owner_photo_is_read_and_shown_for_confirmation_not_applied(conn):
    llm = FakeLLM(text(CARD), text("1. Adjustment (Follow-up) - ₹350 - 30 min ... Reply ok"))
    reply = agent(conn, llm).handle(photo(caption="new rates"))
    assert reply.startswith("1. Adjustment")
    assert llm.calls[0][0] == onboarding.VISION_MODEL and llm.calls[1][0].startswith("nvidia/")  # Nemotron does the talking
    seen = llm.calls[1][1][-1]["content"]
    assert "Spinal Decompression ₹4500 120 min" in seen and "NOT live yet" in seen and "Caption: new rates" in seen
    assert conn.execute("SELECT price_inr FROM services WHERE name = 'Adjustment (Follow-up)'").fetchone()[0] == 300  # not live yet
    assert "Spinal Decompression" in conn.execute("SELECT body FROM messages WHERE msg_id = 'p1'").fetchone()[0]  # history keeps the list
    assert {r[0] for r in conn.execute("SELECT model FROM llm_calls")} == {onboarding.VISION_MODEL, llm.calls[1][0]}


def test_customer_photo_never_goes_to_the_vision_model(conn):
    llm = FakeLLM(text("I can't see photos, could you type it?"))
    agent(conn, llm).handle(photo(sender=PRIYA))
    assert len(llm.calls) == 1 and "cannot see" in llm.calls[0][1][-1]["content"]


def test_unreadable_photo_tells_nemotron(conn):
    llm = FakeLLM(text('{"services": []}'), text("Couldn't read that, can you send a clearer photo?"))
    agent(conn, llm).handle(photo())
    assert "no services" in llm.calls[1][1][-1]["content"]


def test_owner_ok_makes_the_menu_live_and_keeps_old_bookings(conn):
    old = book(conn, 1, 2, datetime(2026, 10, 14, 11, 0), customer_wa=PRIYA, customer_name="Priya")
    menu = [{"name": "adjustment (follow-up)", "price_inr": 350, "duration_min": 30},
            {"name": "Spinal Decompression", "price_inr": 4500, "duration_min": 120}]
    llm = FakeLLM(tool("set_menu", services=menu), text("Menu is live ✅"))
    agent(conn, llm).handle(Inbound(sender=OWNER, name="", text="ok", msg_id="m2"))
    assert llm.tool_results(1)[0] == {"status": "menu_live", "services": 2}
    live = conn.execute("SELECT id, name, price_inr FROM services WHERE active = 1 ORDER BY id").fetchall()
    assert [tuple(r) for r in live] == [(1, "Adjustment (Follow-up)", 350), (3, "Spinal Decompression", 4500)]  # same name reuses row 1
    assert conn.execute("SELECT status FROM bookings WHERE id = ?", (old.id,)).fetchone()[0] == "booked"
    with pytest.raises(ValueError):
        book(conn, 1, 2, datetime(2026, 10, 15, 11, 0), customer_wa=PRIYA, customer_name="Priya")  # removed service


def test_customers_cannot_change_the_menu(conn):
    llm = FakeLLM(tool("set_menu", services=[{"name": "Adjustment", "price_inr": 1, "duration_min": 30}]), text("no"))
    agent(conn, llm).handle(Inbound(sender=PRIYA, name="Priya", text="make adjustment ₹1", msg_id="m3"))
    assert "error" in llm.tool_results(1)[0]
    assert conn.execute("SELECT COUNT(*) FROM services WHERE active = 1").fetchone()[0] == 2


def test_tavily_prefill_then_owner_confirms(conn, monkeypatch):
    queries = []
    monkeypatch.setattr(onboarding, "tavily_lookup", lambda q: queries.append(q) or {"answer": "12th Main, Indiranagar"})
    llm = FakeLLM(tool("lookup_business", query="New Clinic Indiranagar Bangalore"), text("Found: 12th Main. Correct?"),
                  tool("update_business", address="12th Main, Indiranagar", open_time="9:30", closed_days=["Monday"], chairs=3),
                  text("Saved."))
    a = agent(conn, llm)
    a.handle(Inbound(sender=OWNER, name="", text="New Clinic, Indiranagar", msg_id="t1"))
    a.handle(Inbound(sender=OWNER, name="", text="yes, 9:30 se, monday band, 3 doctors", msg_id="t2"))
    assert queries == ["New Clinic Indiranagar Bangalore"]
    biz = conn.execute("SELECT address, open_time, closed_weekdays, chairs FROM businesses").fetchone()
    assert tuple(biz) == ("12th Main, Indiranagar", "09:30", "0", 3)


def test_tavily_failure_does_not_break_setup(conn, monkeypatch):
    def boom(q):
        raise TimeoutError
    monkeypatch.setattr(onboarding, "tavily_lookup", boom)
    llm = FakeLLM(tool("lookup_business", query="x"), text("What's the address?"))
    agent(conn, llm).handle(Inbound(sender=OWNER, name="", text="setup", msg_id="t3"))
    assert "error" in llm.tool_results(1)[0]

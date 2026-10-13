import pytest
from fastapi.testclient import TestClient

from bookings.sim import Sim
from tests.fakes import FakeLLM, text, tool


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    from bookings.app import create_app

    def make(llm, **kw):
        sim = Sim(llm, **kw)
        return TestClient(create_app(handle=lambda i: None, send=lambda t, b: None, sim=sim)), sim
    return make


def say(client, sid, text_, role="customer"):
    return client.post("/api/sim/message", json={"sid": sid, "role": role, "text": text_})


def test_customer_message_runs_the_real_agent_and_books_in_the_session(make_client):
    client, _ = make_client(FakeLLM(tool("list_services"), text("We have 6 services.")))
    r = say(client, "s1", "services?").json()
    assert r["reply"] == "We have 6 services."
    state = client.get("/api/sim/state", params={"sid": "s1"}).json()
    assert state["business"]["name"].startswith("SpineCare") and state["business"]["kind"] == "clinic" and len(state["services"]) == 6


def next_open_day():
    from datetime import datetime, timedelta
    from bookings.sim import _open_day
    return f"{_open_day(datetime.now().date() + timedelta(days=1), 1):%Y-%m-%d}"


def priya_bookings(client, sid):
    state = client.get("/api/sim/state", params={"sid": sid}).json()
    return [x for d in state["days"] for x in d["bookings"] if x["customer"] == "Priya"]


def test_sessions_are_isolated(make_client):
    client, _ = make_client(FakeLLM(tool("book", service_id=1, date=next_open_day(), time="10:00"), text("Booked.")))
    say(client, "a", "book")
    assert len(priya_bookings(client, "a")) == 1 and priya_bookings(client, "b") == []


def test_ask_owner_lands_in_the_owner_phone(make_client):
    client, _ = make_client(FakeLLM(tool("ask_owner", question="10% off?"), text("Checking with the clinic.")))
    r = say(client, "s", "discount?").json()
    assert r["reply"] == "Checking with the clinic." and any("10% off?" in m for m in r["owner_inbox"])


def test_owner_role_uses_owner_number(make_client):
    llm = FakeLLM(tool("day_schedule", date="2026-10-14"), text("No bookings."))
    client, _ = make_client(llm)
    assert say(client, "s", "aaj ka schedule?", role="owner").json()["reply"] == "No bookings."
    assert "owner" in llm.calls[0][1][0]["content"]  # owner prompt, not the receptionist prompt
    assert "error" not in llm.tool_results(1)[0]  # day_schedule is allowed for the owner


def test_reset_gives_a_fresh_clinic(make_client):
    client, _ = make_client(FakeLLM(tool("book", service_id=1, date=next_open_day(), time="10:00"), text("Booked.")))
    say(client, "s", "book")
    client.post("/api/sim/reset", json={"sid": "s"})
    assert priya_bookings(client, "s") == []


def test_per_session_message_cap(make_client):
    client, _ = make_client(FakeLLM(*[text("ok")] * 5), max_messages=3)
    codes = [say(client, "s", f"m{i}").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_bad_input_is_rejected(make_client):
    client, _ = make_client(FakeLLM())
    assert say(client, "s", "x" * 2001).status_code == 422
    assert client.post("/api/sim/message", json={"sid": "s", "role": "admin", "text": "hi"}).status_code == 422


def test_demo_page_is_served(make_client):
    client, _ = make_client(FakeLLM())
    r = client.get("/demo")
    assert r.status_code == 200 and "AgentGrow Bookings" in r.text


def test_blank_clinic_is_set_up_from_a_photo(make_client):
    import base64
    import json
    card = json.dumps({"services": [{"name": "Adjustment (Follow-up)", "price_inr": 300, "duration_min": 30}]})
    llm = FakeLLM(text(card), text("1. Adjustment (Follow-up) ₹300. Reply ok"),
                  tool("set_menu", services=[{"name": "Adjustment (Follow-up)", "price_inr": 300, "duration_min": 30}]), text("Live!"))
    client, _ = make_client(llm)
    client.post("/api/sim/reset", json={"sid": "n", "blank": True})
    assert client.get("/api/sim/state", params={"sid": "n"}).json()["services"] == []
    img = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8fake").decode()
    r = client.post("/api/sim/message", json={"sid": "n", "role": "owner", "image": img})
    assert r.status_code == 200 and "Reply ok" in r.json()["reply"]
    say(client, "n", "ok", role="owner")
    state = client.get("/api/sim/state", params={"sid": "n"}).json()
    assert [x["name"] for x in state["services"]] == ["Adjustment (Follow-up)"]


def test_bad_image_and_empty_message_are_rejected(make_client):
    client, _ = make_client(FakeLLM())
    assert client.post("/api/sim/message", json={"sid": "s", "role": "owner", "image": "data:text/html;base64,PGI+"}).status_code == 422
    assert client.post("/api/sim/message", json={"sid": "s", "role": "owner", "image": "data:image/png;base64,@@@"}).status_code == 422
    assert say(client, "s", "   ").status_code == 422


def test_sample_rate_cards_are_served(make_client):
    client, _ = make_client(FakeLLM())
    assert client.get("/static/ratecards/alignright-fees.jpg").headers["content-type"] == "image/jpeg"


def test_demo_clinic_has_a_full_evening_and_an_overdue_returning_patient(make_client):
    client, _ = make_client(FakeLLM())
    st = client.get("/api/sim/state", params={"sid": "d"}).json()
    full = st["full_evening"]["date"]
    evening = [b for d in st["days"] if d["date"] == full for b in d["bookings"] if b["time"] == "18:00"]
    assert len(evening) == 2  # both practitioners busy, so the waitlist story works


def test_other_patient_cancels_and_the_waitlisted_patient_is_offered_the_slot(make_client):
    client, sim = make_client(FakeLLM(text("18:00 khul gaya, chahiye?")))
    st = client.get("/api/sim/state", params={"sid": "w"}).json()
    s = sim.sessions["w"]
    s.conn.execute("INSERT INTO customers (business_id, wa_id, name) VALUES (1, '919800000001', 'Priya')")
    s.conn.execute("INSERT INTO waitlist (business_id, customer_id, service_id, day) VALUES (1, (SELECT id FROM customers WHERE"
                   " wa_id = '919800000001'), 2, ?)", (st["full_evening"]["date"],))
    r = client.post("/api/sim/event", json={"sid": "w", "kind": "cancel"}).json()
    assert "18:00" in r["event"] and "offered to the waitlist" in r["event"]
    assert r["pushes"]["priya"] == ["18:00 khul gaya, chahiye?"]


def test_daily_check_reminds_the_overdue_patient_and_report_reaches_the_owner(make_client):
    client, _ = make_client(FakeLLM(text("Arjun ji, follow-up ka time ho gaya hai.")))
    r = client.post("/api/sim/event", json={"sid": "t", "kind": "tick"}).json()
    assert "1 follow-up reminder" in r["event"] and r["pushes"]["arjun"] == ["Arjun ji, follow-up ka time ho gaya hai."]
    r = client.post("/api/sim/event", json={"sid": "t", "kind": "report"}).json()
    assert "Recovered" in r["owner_inbox"][-1]

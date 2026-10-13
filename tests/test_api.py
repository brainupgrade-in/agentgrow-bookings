from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from bookings import db
from bookings.slots import book, cancel

KEY = "test-api-key"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    monkeypatch.setenv("BOOKINGS_API_KEY", KEY)
    conn = db.connect(tmp_path / "api.db")
    db.init(conn)
    biz = db.add_business(conn, name="Test Clinic", owner_wa="919900000000", open_time="10:00", close_time="20:00", chairs=1)
    db.add_service(conn, biz, name="Adjustment", price_inr=400, duration_min=30)
    b1 = book(conn, biz, 1, datetime(2026, 10, 14, 11, 0), customer_wa="91111", customer_name="Asha")
    b2 = book(conn, biz, 1, datetime(2026, 10, 14, 12, 0), customer_wa="92222", customer_name="Ravi", source="waitlist")
    conn.execute("INSERT INTO recoveries (business_id, booking_id, kind, amount_inr) VALUES (?, ?, 'waitlist_fill', 400)", (biz, b2.id))
    cancel(conn, b1.id)
    from bookings.app import create_app

    client = TestClient(create_app(handle=lambda i: None, send=lambda t, b: None, conn=conn))
    return client, conn


def auth(key=KEY):
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.parametrize("headers", [{}, auth("wrong")])
def test_api_requires_the_key(setup, headers):
    client, _ = setup
    assert client.get("/api/v1/businesses", headers=headers).status_code == 401


def test_api_disabled_without_configured_key(setup, monkeypatch):
    client, _ = setup
    monkeypatch.delenv("BOOKINGS_API_KEY")
    assert client.get("/api/v1/businesses", headers=auth()).status_code == 503


def test_list_businesses_with_since_filter(setup):
    client, _ = setup
    rows = client.get("/api/v1/businesses", headers=auth()).json()["businesses"]
    assert rows[0]["name"] == "Test Clinic" and rows[0]["owner_wa"] == "919900000000" and rows[0]["services"] == 1
    assert client.get("/api/v1/businesses", params={"since": "2999-01-01T00:00:00"}, headers=auth()).json()["businesses"] == []


def test_schedule_lists_only_active_bookings(setup):
    client, _ = setup
    r = client.get("/api/v1/businesses/1/schedule", params={"date": "2026-10-14"}, headers=auth()).json()
    assert r["bookings"] == [{"time": "12:00", "service": "Adjustment", "customer": "Ravi", "chair": 1, "source": "waitlist"}]


def test_summary_counts_bookings_cancellations_and_recovered_revenue(setup):
    client, _ = setup
    s = client.get("/api/v1/businesses/1/summary", params={"days": 3650}, headers=auth()).json()
    assert (s["bookings"], s["cancellations"], s["booked_inr"], s["recovered_inr"]) == (1, 1, 400, 400)


def test_unknown_business_is_404(setup):
    client, _ = setup
    assert client.get("/api/v1/businesses/99/summary", headers=auth()).status_code == 404

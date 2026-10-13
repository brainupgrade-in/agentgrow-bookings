import sqlite3
import threading
from datetime import date, datetime

import pytest

from bookings import db
from bookings.slots import SlotTaken, book, cancel, find_slots

DAY = date(2026, 10, 14)  # a Wednesday


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init(c)
    biz = db.add_business(c, name="Test Clinic", owner_wa="919900000000", open_time="10:00", close_time="13:00",
                          chairs=1, closed_weekdays="1")  # closed Tuesdays
    db.add_service(c, biz, name="Adjustment", price_inr=400, duration_min=30)
    db.add_service(c, biz, name="Spinal decompression", price_inr=1500, duration_min=90)
    return c


def svc(conn, name):
    return conn.execute("SELECT id FROM services WHERE name = ?", (name,)).fetchone()[0]


def at(hhmm: str) -> datetime:
    return datetime.combine(DAY, datetime.strptime(hhmm, "%H:%M").time())


def test_empty_day_offers_every_step_that_fits_before_closing(conn):
    slots = find_slots(conn, 1, svc(conn, "Adjustment"), DAY)
    assert [s.strftime("%H:%M") for s in slots] == ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30"]
    long = find_slots(conn, 1, svc(conn, "Spinal decompression"), DAY)
    assert [s.strftime("%H:%M") for s in long] == ["10:00", "10:30", "11:00", "11:30"]  # 90 min must end by 13:00


def test_closed_weekday_has_no_slots(conn):
    assert find_slots(conn, 1, svc(conn, "Adjustment"), date(2026, 10, 13)) == []  # Tuesday


def test_booking_removes_overlapping_slots(conn):
    book(conn, 1, svc(conn, "Spinal decompression"), at("10:30"), customer_wa="91111", customer_name="Asha")
    slots = find_slots(conn, 1, svc(conn, "Adjustment"), DAY)
    assert [s.strftime("%H:%M") for s in slots] == ["10:00", "12:00", "12:30"]


def test_double_booking_is_rejected(conn):
    book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="91111", customer_name="Asha")
    with pytest.raises(SlotTaken):
        book(conn, 1, svc(conn, "Spinal decompression"), at("10:30"), customer_wa="92222", customer_name="Ravi")


def test_booking_outside_hours_or_off_grid_is_rejected(conn):
    for start in (at("09:30"), at("12:45"), at("12:40")):
        with pytest.raises(SlotTaken):
            book(conn, 1, svc(conn, "Adjustment"), start, customer_wa="91111", customer_name="Asha")


def test_second_practitioner_allows_parallel_bookings(conn):
    conn.execute("UPDATE businesses SET chairs = 2")
    book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="91111", customer_name="Asha")
    b2 = book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="92222", customer_name="Ravi")
    assert b2.chair == 2
    with pytest.raises(SlotTaken):
        book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="93333", customer_name="Meera")


def test_cancel_frees_the_slot_and_returns_the_booking(conn):
    b = book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="91111", customer_name="Asha")
    cancelled = cancel(conn, b.id)
    assert cancelled.status == "cancelled"
    assert at("11:00") in find_slots(conn, 1, svc(conn, "Adjustment"), DAY)


def test_customer_is_created_once_and_price_is_snapshotted(conn):
    b1 = book(conn, 1, svc(conn, "Adjustment"), at("10:00"), customer_wa="91111", customer_name="Asha")
    conn.execute("UPDATE services SET price_inr = 500")
    b2 = book(conn, 1, svc(conn, "Adjustment"), at("11:00"), customer_wa="91111", customer_name="Asha")
    assert b1.customer_id == b2.customer_id
    assert (b1.price_inr, b2.price_inr) == (400, 500)


def test_concurrent_bookings_for_the_last_slot_only_one_wins(tmp_path):
    path = tmp_path / "race.db"
    c = db.connect(path)
    db.init(c)
    biz = db.add_business(c, name="S", owner_wa="9", open_time="10:00", close_time="10:30", chairs=1, closed_weekdays="")
    sid = db.add_service(c, biz, name="Adjustment", price_inr=400, duration_min=30)
    results = []

    def attempt(wa):
        conn = db.connect(path)
        try:
            book(conn, biz, sid, at("10:00"), customer_wa=wa, customer_name=wa)
            results.append("ok")
        except SlotTaken:
            results.append("taken")

    threads = [threading.Thread(target=attempt, args=(f"9{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok"] + ["taken"] * 7
    assert c.execute("SELECT COUNT(*) FROM bookings WHERE status = 'booked'").fetchone()[0] == 1

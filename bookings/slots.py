"""Slot math and booking writes. Pure Python + SQL; the model never decides availability."""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

FMT = "%Y-%m-%dT%H:%M"


class SlotTaken(Exception):
    """The requested start is not bookable (outside hours, off the grid, or every chair is busy)."""


@dataclass(frozen=True)
class Booking:
    id: int
    business_id: int
    service_id: int
    customer_id: int
    chair: int
    start_at: datetime
    end_at: datetime
    price_inr: int
    status: str


def _grid(biz: sqlite3.Row, day: date, duration_min: int) -> list[datetime]:
    """Every start on the business's step grid where the service fits inside opening hours."""
    if str(day.weekday()) in biz["closed_weekdays"].split(","):
        return []
    open_at = datetime.combine(day, datetime.strptime(biz["open_time"], "%H:%M").time())
    close_at = datetime.combine(day, datetime.strptime(biz["close_time"], "%H:%M").time())
    step, dur = timedelta(minutes=biz["slot_step_min"]), timedelta(minutes=duration_min)
    out, t = [], open_at
    while t + dur <= close_at:
        out.append(t)
        t += step
    return out


def _busy_chairs(conn, business_id: int, start: datetime, end: datetime) -> set[int]:
    rows = conn.execute(
        "SELECT chair FROM bookings WHERE business_id = ? AND status = 'booked' AND start_at < ? AND end_at > ?",
        (business_id, end.strftime(FMT), start.strftime(FMT)),
    )
    return {r["chair"] for r in rows}


def _load(conn, business_id: int, service_id: int):
    biz = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    svc = conn.execute("SELECT * FROM services WHERE id = ? AND business_id = ? AND active = 1", (service_id, business_id)).fetchone()
    if biz is None or svc is None:
        raise ValueError(f"unknown business {business_id} or service {service_id}")
    return biz, svc


def find_slots(conn, business_id: int, service_id: int, day: date) -> list[datetime]:
    biz, svc = _load(conn, business_id, service_id)
    dur = timedelta(minutes=svc["duration_min"])
    return [t for t in _grid(biz, day, svc["duration_min"]) if len(_busy_chairs(conn, business_id, t, t + dur)) < biz["chairs"]]


def _row_to_booking(r) -> Booking:
    return Booking(r["id"], r["business_id"], r["service_id"], r["customer_id"], r["chair"],
                   datetime.strptime(r["start_at"], FMT), datetime.strptime(r["end_at"], FMT), r["price_inr"], r["status"])


def book(conn, business_id: int, service_id: int, start: datetime, *, customer_wa: str, customer_name: str,
         source: str = "direct") -> Booking:
    """Book the first free chair. BEGIN IMMEDIATE serialises writers, so the check-then-insert cannot race."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        biz, svc = _load(conn, business_id, service_id)
        end = start + timedelta(minutes=svc["duration_min"])
        if start not in _grid(biz, start.date(), svc["duration_min"]):
            raise SlotTaken(f"{start:%H:%M} is outside hours or not on the {biz['slot_step_min']}-minute grid")
        free = [c for c in range(1, biz["chairs"] + 1) if c not in _busy_chairs(conn, business_id, start, end)]
        if not free:
            raise SlotTaken(f"{start:%H:%M} is fully booked")
        conn.execute(
            "INSERT INTO customers (business_id, wa_id, name) VALUES (?, ?, ?)"
            " ON CONFLICT (business_id, wa_id) DO UPDATE SET name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END",
            (business_id, customer_wa, customer_name),
        )
        cid = conn.execute("SELECT id FROM customers WHERE business_id = ? AND wa_id = ?", (business_id, customer_wa)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO bookings (business_id, service_id, customer_id, chair, start_at, end_at, price_inr, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (business_id, service_id, cid, free[0], start.strftime(FMT), end.strftime(FMT), svc["price_inr"], source),
        )
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (cur.lastrowid,)).fetchone()
        conn.execute("COMMIT")
        return _row_to_booking(row)
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def cancel(conn, booking_id: int) -> Booking:
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'booked'", (booking_id,))
    return _row_to_booking(conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone())

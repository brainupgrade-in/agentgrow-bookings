"""Read-only API for Friday (AgentGrow's revenue agent). Bearer key in BOOKINGS_API_KEY; unset = API disabled."""
import hmac
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query


def _auth(authorization: str = Header("")):
    key = os.environ.get("BOOKINGS_API_KEY")
    if not key:
        raise HTTPException(503, "API disabled")
    if not hmac.compare_digest(authorization.removeprefix("Bearer "), key):
        raise HTTPException(401, "bad key")


def make_api(conn) -> APIRouter:
    r = APIRouter(prefix="/api/v1", dependencies=[Depends(_auth)])

    def business(bid: int):
        row = conn.execute("SELECT * FROM businesses WHERE id = ?", (bid,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown business")
        return row

    @r.get("/businesses")
    def businesses(since: str = Query("1970-01-01T00:00:00")):
        rows = conn.execute(
            "SELECT b.id, b.name, b.owner_wa, b.created_at, (SELECT COUNT(*) FROM services s WHERE s.business_id = b.id AND s.active = 1) AS services"
            " FROM businesses b WHERE b.created_at > ? ORDER BY b.id", (since.replace("T", " "),))
        return {"businesses": [dict(x) for x in rows]}

    @r.get("/businesses/{bid}/schedule")
    def schedule(bid: int, date: str):
        business(bid)
        rows = conn.execute(
            "SELECT substr(b.start_at, 12) AS time, s.name AS service, c.name AS customer, b.chair, b.source FROM bookings b"
            " JOIN services s ON s.id = b.service_id JOIN customers c ON c.id = b.customer_id"
            " WHERE b.business_id = ? AND b.status = 'booked' AND b.start_at LIKE ? ORDER BY b.start_at",
            (bid, f"{datetime.strptime(date, '%Y-%m-%d'):%Y-%m-%d}%"))
        return {"date": date, "bookings": [dict(x) for x in rows]}

    @r.get("/businesses/{bid}/summary")
    def summary(bid: int, days: int = 7):
        business(bid)
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        one = lambda sql: conn.execute(sql, (bid, since)).fetchone()[0] or 0  # noqa: E731
        return {
            "business_id": bid, "days": days,
            "bookings": one("SELECT COUNT(*) FROM bookings WHERE business_id = ? AND status != 'cancelled' AND created_at > ?"),
            "cancellations": one("SELECT COUNT(*) FROM bookings WHERE business_id = ? AND status = 'cancelled' AND created_at > ?"),
            "booked_inr": one("SELECT SUM(price_inr) FROM bookings WHERE business_id = ? AND status != 'cancelled' AND created_at > ?"),
            "recovered_inr": one("SELECT SUM(amount_inr) FROM recoveries WHERE business_id = ? AND at > ?"),
            "llm_calls": one("SELECT COUNT(*) FROM llm_calls WHERE business_id = ? AND at > ?"),
        }

    return r

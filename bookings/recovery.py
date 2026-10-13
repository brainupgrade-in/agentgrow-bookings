"""Revenue recovery: refill cancelled slots from the waitlist, nudge patients who fall behind their care plan, hold a
deposit after a no-show, and report the rupees recovered. Pure DB logic; Agent turns these into WhatsApp messages."""
from datetime import datetime, timedelta

from bookings.slots import FMT, find_slots

DEPOSIT_INR = 300            # asked on the next booking after a no-show
NO_SHOW_WINDOW = timedelta(days=90)
DEPOSIT_HOLD = timedelta(hours=2)  # an unpaid deposit releases the slot after this
NUDGE_COOLDOWN = timedelta(days=28)
NUDGE_CREDIT = timedelta(days=7)   # a booking this soon after a nudge counts as recovered by it
NUDGES_PER_TICK = 20


def offer_freed_slot(conn, bid: int, start_at: str) -> int | None:
    """After a cancellation: mark the first waiting entry for that day whose service fits at that time as offered."""
    start = datetime.strptime(start_at, FMT)
    rows = conn.execute("SELECT id, service_id FROM waitlist WHERE business_id = ? AND day = ? AND status = 'waiting' ORDER BY id",
                        (bid, start_at[:10])).fetchall()
    for w in rows:
        if start in find_slots(conn, bid, w["service_id"], start.date()):
            conn.execute("UPDATE waitlist SET status = 'offered', offered_start = ? WHERE id = ?", (start_at, w["id"]))
            return w["id"]
    return None


def pending_offers(conn, bid: int) -> list:
    return conn.execute(
        "SELECT w.id, w.offered_start, s.name AS service, c.wa_id, c.name FROM waitlist w JOIN services s ON s.id = w.service_id"
        " JOIN customers c ON c.id = w.customer_id WHERE w.business_id = ? AND w.status = 'offered' AND w.notified_at IS NULL",
        (bid,)).fetchall()


def source_for(conn, bid: int, wa_id: str, start_at: str, now: datetime) -> str:
    """Why this booking happened: a waitlist offer for exactly this slot, a recent follow-up nudge, or neither."""
    if conn.execute("SELECT 1 FROM waitlist w JOIN customers c ON c.id = w.customer_id WHERE w.business_id = ? AND c.wa_id = ?"
                    " AND w.status = 'offered' AND w.offered_start = ?", (bid, wa_id, start_at)).fetchone():
        return "waitlist"
    c = conn.execute("SELECT id, last_nudge_at FROM customers WHERE business_id = ? AND wa_id = ?", (bid, wa_id)).fetchone()
    if c and c["last_nudge_at"] and datetime.strptime(c["last_nudge_at"], FMT) >= now - NUDGE_CREDIT:
        credited = conn.execute("SELECT 1 FROM recoveries r JOIN bookings b ON b.id = r.booking_id WHERE b.customer_id = ?"
                                " AND r.kind = 'nudge' AND r.at >= ?", (c["id"], c["last_nudge_at"])).fetchone()
        if not credited:
            return "nudge"
    return "direct"


def record_booking(conn, bid: int, booking, source: str, now: datetime) -> None:
    if source == "waitlist":
        conn.execute("UPDATE waitlist SET status = 'booked' WHERE business_id = ? AND customer_id = ? AND offered_start = ?"
                     " AND status = 'offered'", (bid, booking.customer_id, booking.start_at.strftime(FMT)))
    if source in ("waitlist", "nudge"):
        conn.execute("INSERT INTO recoveries (business_id, booking_id, kind, amount_inr, at) VALUES (?, ?, ?, ?, ?)",
                     (bid, booking.id, "waitlist_fill" if source == "waitlist" else "nudge", booking.price_inr, now.strftime(FMT)))


def forget_cancelled(conn, booking_id: int) -> None:
    """A recovered booking that is cancelled again recovered nothing (a paid deposit stays recovered)."""
    conn.execute("DELETE FROM recoveries WHERE booking_id = ? AND kind != 'deposit'", (booking_id,))


def needs_deposit(conn, bid: int, wa_id: str, now: datetime) -> bool:
    return conn.execute(
        "SELECT 1 FROM bookings b JOIN customers c ON c.id = b.customer_id WHERE b.business_id = ? AND c.wa_id = ?"
        " AND b.status = 'no_show' AND b.start_at >= ?", (bid, wa_id, (now - NO_SHOW_WINDOW).strftime(FMT))).fetchone() is not None


def due_nudges(conn, bid: int, now: datetime) -> list:
    """Patients whose last visit's service has a follow-up interval that has passed, with nothing booked ahead
    and no nudge in the cooldown window."""
    rows = conn.execute(
        """SELECT c.id, c.wa_id, c.name, s.name AS service, s.lapse_weeks, b.start_at AS last_visit
           FROM customers c
           JOIN bookings b ON b.id = (SELECT id FROM bookings WHERE customer_id = c.id AND status IN ('booked', 'done')
                                      AND start_at <= ? ORDER BY start_at DESC LIMIT 1)
           JOIN services s ON s.id = b.service_id
           WHERE c.business_id = ? AND s.lapse_weeks IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM bookings f WHERE f.customer_id = c.id AND f.status = 'booked' AND f.start_at > ?)
             AND (c.last_nudge_at IS NULL OR c.last_nudge_at < ?)
           ORDER BY b.start_at LIMIT ?""",
        (now.strftime(FMT), bid, now.strftime(FMT), (now - NUDGE_COOLDOWN).strftime(FMT), NUDGES_PER_TICK)).fetchall()
    return [r for r in rows if datetime.strptime(r["last_visit"], FMT) + timedelta(weeks=r["lapse_weeks"]) <= now]


def report(conn, bid: int, now: datetime, days: int = 7) -> dict:
    since = (now - timedelta(days=days)).strftime(FMT)
    made = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(price_inr), 0) AS inr FROM bookings WHERE business_id = ? AND created_at >= ?"
                        " AND status != 'cancelled'", (bid, since.replace("T", " "))).fetchone()
    rec = {r["kind"]: (r["n"], r["inr"]) for r in conn.execute(
        "SELECT kind, COUNT(*) AS n, SUM(amount_inr) AS inr FROM recoveries WHERE business_id = ? AND at >= ? GROUP BY kind", (bid, since))}
    no_shows = conn.execute("SELECT COUNT(*) FROM bookings WHERE business_id = ? AND status = 'no_show' AND start_at >= ?",
                            (bid, since)).fetchone()[0]
    return {"days": days, "bookings": made["n"], "booked_inr": made["inr"], "no_shows": no_shows,
            "waitlist_fills": rec.get("waitlist_fill", (0, 0)), "nudges": rec.get("nudge", (0, 0)),
            "deposits": rec.get("deposit", (0, 0)), "recovered_inr": sum(v[1] for v in rec.values())}


def report_text(r: dict, name: str) -> str:
    inr = lambda n: f"₹{n:,}"  # noqa: E731
    lines = [f"📊 *{name}*: last {r['days']} days",
             f"Bookings made: {r['bookings']} ({inr(r['booked_inr'])})",
             f"*Recovered: {inr(r['recovered_inr'])}*",
             f"• Cancelled slots refilled from the waitlist: {r['waitlist_fills'][0]} ({inr(r['waitlist_fills'][1])})",
             f"• Patients back on their care plan after a reminder: {r['nudges'][0]} ({inr(r['nudges'][1])})",
             f"• No-show deposits collected: {r['deposits'][0]} ({inr(r['deposits'][1])})",
             f"No-shows: {r['no_shows']}"]
    return "\n".join(lines)

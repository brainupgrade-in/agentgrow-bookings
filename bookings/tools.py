"""Tools the model may call. Every write goes through slots.py; every failure comes back as {"error": ...} for the model."""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable

from bookings import onboarding, payments, recovery
from bookings.slots import FMT, SlotTaken, book, cancel, find_slots

log = logging.getLogger("bookings.tools")


@dataclass
class Ctx:
    conn: object
    business_id: int
    wa_id: str
    name: str
    is_owner: bool
    now: datetime
    send: Callable[[str, str], None]


def _fn(name, description, /, **props):
    required = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required}}}


DATE = {"type": "string", "description": "YYYY-MM-DD"}
SERVICE = {"type": "integer", "description": "service id from list_services"}

CUSTOMER_TOOLS = [
    _fn("list_services", "Services with id, price (INR) and duration."),
    _fn("find_slots", "Free start times (HH:MM) for a service on a date.", service_id=SERVICE, date=DATE),
    _fn("book", "Book a service for this customer. Only use a time returned by find_slots.",
        service_id=SERVICE, date=DATE, time={"type": "string", "description": "HH:MM, 24h"}),
    _fn("my_bookings", "This customer's upcoming bookings."),
    _fn("cancel_booking", "Cancel one of this customer's bookings.", booking_id={"type": "integer"}),
    _fn("join_waitlist", "Add this customer to the waitlist when the time they want is full; they get offered a freed slot.",
        service_id=SERVICE, date=DATE),
    _fn("ask_owner", "Ask the owner something you cannot decide (discounts, unusual requests, a full day, red-flag symptoms).",
        question={"type": "string", "description": "one line the owner can answer quickly"}),
]
HHMM = {"type": "string", "description": "HH:MM, 24h", "optional": True}
OWNER_TOOLS = [
    CUSTOMER_TOOLS[0],
    CUSTOMER_TOOLS[1],
    _fn("day_schedule", "All bookings on a date, with customer names and booking ids.", date=DATE),
    _fn("mark_no_show", "Mark a booking as a no-show (the patient did not come). Their next booking will need a deposit.",
        booking_id={"type": "integer", "description": "id from day_schedule"}),
    _fn("revenue_report", "Bookings and rupees recovered (waitlist refills, care-plan reminders, deposits) over recent days.",
        days={"type": "integer", "description": "default 7", "optional": True}),
    _fn("answer_request", "Pass the owner's answer to an open patient request; the patient is messaged right after.",
        request_id={"type": "integer", "description": "the # of an open request"},
        answer={"type": "string", "description": "the owner's decision, faithfully, in their words, e.g. 'yes, 7:30 pm works'"}),
    _fn("set_menu", "Replace the whole live service menu. Only after the owner confirmed the list.",
        services={"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "price_inr": {"type": "integer"}, "duration_min": {"type": "integer"}},
            "required": ["name", "price_inr", "duration_min"]}}),
    _fn("lookup_business", "Web search (Tavily) for this business, to prefill address, hours and website.",
        query={"type": "string", "description": "business name plus area and city"}),
    _fn("update_business", "Save business details the owner confirmed. Pass only the fields that change.",
        name={"type": "string", "optional": True}, address={"type": "string", "optional": True},
        website={"type": "string", "optional": True}, open_time=dict(HHMM), close_time=dict(HHMM),
        closed_days={"type": "array", "items": {"type": "string"}, "description": "weekday names, e.g. [\"Monday\"]; [] = open daily",
                     "optional": True},
        chairs={"type": "integer", "description": "how many practitioners see patients at the same time", "optional": True}),
]


def _date(s: str):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _service_names(ctx: Ctx):
    rows = ctx.conn.execute("SELECT id, name, price_inr, duration_min FROM services WHERE business_id = ? AND active = 1 ORDER BY id", (ctx.business_id,))
    return [dict(r) for r in rows]


def list_services(ctx: Ctx):
    return {"services": _service_names(ctx)}


def find_slots_tool(ctx: Ctx, service_id: int, date: str):
    day = _date(date)
    if day < ctx.now.date():
        return {"error": f"{date} is in the past; today is {ctx.now:%Y-%m-%d}"}
    slots = [t for t in find_slots(ctx.conn, ctx.business_id, service_id, day) if t > ctx.now]
    return {"date": date, "slots": [f"{t:%H:%M}" for t in slots]}


def book_tool(ctx: Ctx, service_id: int, date: str, time: str):
    start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    if start <= ctx.now:
        return {"error": "that time has already passed"}
    dup = ctx.conn.execute(
        "SELECT b.id, b.price_inr FROM bookings b JOIN customers c ON c.id = b.customer_id WHERE b.business_id = ? AND c.wa_id = ?"
        " AND b.service_id = ? AND b.start_at = ? AND b.status = 'booked'",
        (ctx.business_id, ctx.wa_id, service_id, start.strftime(FMT))).fetchone()
    if dup:  # models re-call book on "yes please"; with 2+ chairs that would silently double-book
        return {"status": "already_booked", "start": f"{start:%Y-%m-%d %H:%M}", "price_inr": dup["price_inr"]}
    same_day = ctx.conn.execute(
        "SELECT b.id, substr(b.start_at, 12) AS time FROM bookings b JOIN customers c ON c.id = b.customer_id WHERE b.business_id = ?"
        " AND c.wa_id = ? AND b.service_id = ? AND b.start_at LIKE ? AND b.status = 'booked'",
        (ctx.business_id, ctx.wa_id, service_id, f"{date}%")).fetchone()
    if same_day:  # "11 baje" after a 9:00 booking means move it, not book twice
        return {"error": f"this patient already has this service at {same_day['time']} that day. Ask if they want to move it;"
                         " if yes, find it with my_bookings, cancel_booking it, then book the new time."}
    source = recovery.source_for(ctx.conn, ctx.business_id, ctx.wa_id, start.strftime(FMT), ctx.now)
    deposit = recovery.needs_deposit(ctx.conn, ctx.business_id, ctx.wa_id, ctx.now)
    b = book(ctx.conn, ctx.business_id, service_id, start, customer_wa=ctx.wa_id, customer_name=ctx.name, source=source)
    recovery.record_booking(ctx.conn, ctx.business_id, b, source, ctx.now)
    out = {"status": "booked", "start": f"{b.start_at:%Y-%m-%d %H:%M}", "price_inr": b.price_inr}
    if deposit:
        link_id = f"bk{b.id}-{ctx.business_id}-{ctx.now:%Y%m%d%H%M}"
        try:
            url = payments.create_link(link_id, recovery.DEPOSIT_INR, ctx.wa_id, ctx.name,
                                       f"Deposit for your appointment on {b.start_at:%d %b %H:%M} (adjusted against the fee)")
        except Exception:  # payment provider down: keep the booking, skip the deposit
            log.exception("deposit link failed for booking %s", b.id)
            return out
        ctx.conn.execute("UPDATE bookings SET deposit_status = 'pending', deposit_link_id = ?, deposit_due = ? WHERE id = ?",
                         (link_id, (ctx.now + recovery.DEPOSIT_HOLD).strftime(FMT), b.id))
        out.update(status="held_pending_deposit", deposit_inr=recovery.DEPOSIT_INR, pay_link=url,
                   note=f"An earlier appointment was missed, so this slot is held for {recovery.DEPOSIT_HOLD.seconds // 3600} hours"
                        " until the deposit is paid; it is adjusted against the fee. Send the pay_link as is, politely.")
    return out


def my_bookings(ctx: Ctx):
    rows = ctx.conn.execute(
        "SELECT b.id, s.name AS service, b.start_at FROM bookings b JOIN customers c ON c.id = b.customer_id"
        " JOIN services s ON s.id = b.service_id WHERE b.business_id = ? AND c.wa_id = ? AND b.status = 'booked'"
        " AND b.start_at > ? ORDER BY b.start_at",
        (ctx.business_id, ctx.wa_id, ctx.now.strftime(FMT)),
    )
    return {"bookings": [{"id": r["id"], "service": r["service"], "start": r["start_at"].replace("T", " ")} for r in rows]}


def cancel_tool(ctx: Ctx, booking_id: int):
    owned = ctx.conn.execute(
        "SELECT 1 FROM bookings b JOIN customers c ON c.id = b.customer_id"
        " WHERE b.id = ? AND b.business_id = ? AND c.wa_id = ? AND b.status = 'booked'",
        (booking_id, ctx.business_id, ctx.wa_id),
    ).fetchone()
    if not owned:
        return {"error": f"no active booking {booking_id} for this customer"}
    b = cancel(ctx.conn, booking_id)
    recovery.forget_cancelled(ctx.conn, booking_id)
    recovery.offer_freed_slot(ctx.conn, ctx.business_id, b.start_at.strftime(FMT))  # the waitlist hears about it after this turn
    return {"status": b.status, "booking_id": booking_id}


def join_waitlist(ctx: Ctx, service_id: int, date: str):
    _date(date)
    ctx.conn.execute(
        "INSERT INTO customers (business_id, wa_id, name) VALUES (?, ?, ?) ON CONFLICT (business_id, wa_id) DO NOTHING",
        (ctx.business_id, ctx.wa_id, ctx.name),
    )
    cid = ctx.conn.execute("SELECT id FROM customers WHERE business_id = ? AND wa_id = ?", (ctx.business_id, ctx.wa_id)).fetchone()[0]
    ctx.conn.execute("INSERT INTO waitlist (business_id, customer_id, service_id, day) VALUES (?, ?, ?, ?)",
                     (ctx.business_id, cid, service_id, date))
    return {"status": "waitlisted", "date": date}


def ask_owner(ctx: Ctx, question: str):
    owner = ctx.conn.execute("SELECT owner_wa FROM businesses WHERE id = ?", (ctx.business_id,)).fetchone()[0]
    rid = ctx.conn.execute("INSERT INTO owner_requests (business_id, customer_wa, customer_name, question) VALUES (?, ?, ?, ?)",
                           (ctx.business_id, ctx.wa_id, ctx.name, question)).lastrowid
    try:
        ctx.send(owner, f"❓ #{rid} {ctx.name or ctx.wa_id} (+{ctx.wa_id}): {question}\nJust reply here; I'll pass it on.")
    except Exception:  # owner unreachable (e.g. not a verified test recipient); the customer still gets an answer
        log.exception("ask_owner: could not message owner %s", owner)
        return {"status": "owner_not_reachable_now", "note": "tell the customer the team will get back to them"}
    return {"status": "sent_to_owner", "request_id": rid}


def open_requests(conn, business_id: int) -> list[dict]:
    rows = conn.execute("SELECT id, customer_name, customer_wa, question, created_at FROM owner_requests"
                        " WHERE business_id = ? AND status = 'open' ORDER BY id", (business_id,))
    return [dict(r) for r in rows]


def answer_request(ctx: Ctx, request_id: int, answer: str):
    cur = ctx.conn.execute("UPDATE owner_requests SET answer = ?, status = 'answered' WHERE id = ? AND business_id = ? AND status = 'open'",
                           (answer.strip(), request_id, ctx.business_id))
    if cur.rowcount == 0 or not answer.strip():
        return {"error": f"no open request #{request_id}", "open": [r["id"] for r in open_requests(ctx.conn, ctx.business_id)]}
    return {"status": "will_be_sent_to_patient", "request_id": request_id}


def day_schedule(ctx: Ctx, date: str):
    rows = ctx.conn.execute(
        "SELECT b.id, b.start_at, s.name AS service, c.name AS customer, b.chair, b.source, b.deposit_status FROM bookings b"
        " JOIN services s ON s.id = b.service_id JOIN customers c ON c.id = b.customer_id"
        " WHERE b.business_id = ? AND b.status = 'booked' AND b.start_at LIKE ? ORDER BY b.start_at",
        (ctx.business_id, f"{_date(date):%Y-%m-%d}%"),
    )
    return {"date": date, "bookings": [{"id": r["id"], "time": r["start_at"][11:], "service": r["service"], "customer": r["customer"],
                                        "practitioner": r["chair"], "source": r["source"], "deposit": r["deposit_status"]} for r in rows]}


def mark_no_show(ctx: Ctx, booking_id: int):
    row = ctx.conn.execute("SELECT start_at FROM bookings WHERE id = ? AND business_id = ? AND status IN ('booked', 'done')",
                           (booking_id, ctx.business_id)).fetchone()
    if not row:
        return {"error": f"no booking {booking_id}"}
    if row["start_at"] > ctx.now.strftime(FMT):
        return {"error": "that appointment has not happened yet"}
    ctx.conn.execute("UPDATE bookings SET status = 'no_show' WHERE id = ?", (booking_id,))
    return {"status": "no_show", "booking_id": booking_id, "note": f"their next booking will ask for a ₹{recovery.DEPOSIT_INR} deposit"}


def revenue_report(ctx: Ctx, days: int = 7):
    return recovery.report(ctx.conn, ctx.business_id, ctx.now, max(1, min(int(days), 90)))


def set_menu(ctx: Ctx, services: list[dict]):
    clean = onboarding.parse_services(json.dumps({"services": services}))
    if not clean:
        return {"error": "no valid services (each needs name, price_inr > 0, duration_min)"}
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        ctx.conn.execute("UPDATE services SET active = 0 WHERE business_id = ?", (ctx.business_id,))
        for sv in clean:  # same name as an old service: reuse its row so past bookings and nudges keep pointing at it
            cur = ctx.conn.execute(
                "UPDATE services SET price_inr = ?, duration_min = ?, active = 1 WHERE id = (SELECT id FROM services"
                " WHERE business_id = ? AND lower(name) = lower(?) ORDER BY id LIMIT 1)",
                (sv["price_inr"], sv["duration_min"], ctx.business_id, sv["name"]))
            if cur.rowcount == 0:
                ctx.conn.execute("INSERT INTO services (business_id, name, price_inr, duration_min) VALUES (?, ?, ?, ?)",
                                 (ctx.business_id, sv["name"], sv["price_inr"], sv["duration_min"]))
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise
    return {"status": "menu_live", "services": len(clean)}


def lookup_business(ctx: Ctx, query: str):
    try:
        return onboarding.tavily_lookup(query)
    except Exception as e:  # network or quota: setup continues by asking the owner directly
        log.exception("tavily lookup failed")
        return {"error": f"search unavailable ({type(e).__name__}); ask the owner instead"}


def update_business(ctx: Ctx, name=None, address=None, website=None, open_time=None, close_time=None, closed_days=None, chairs=None):
    fields = {"name": name, "address": address, "website": website}
    for k, v in (("open_time", open_time), ("close_time", close_time)):
        if v is not None:
            fields[k] = datetime.strptime(v, "%H:%M").strftime("%H:%M")
    if closed_days is not None:
        days = [d.strip().lower()[:3] for d in closed_days]
        fields["closed_weekdays"] = ",".join(str(i) for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun")) if d in days)
    if chairs is not None:
        fields["chairs"] = max(1, int(chairs))
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return {"error": "nothing to update"}
    ctx.conn.execute(f"UPDATE businesses SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), ctx.business_id))
    return {"status": "saved", "fields": sorted(fields)}


IMPLS = {
    "list_services": list_services, "find_slots": find_slots_tool, "book": book_tool, "my_bookings": my_bookings,
    "cancel_booking": cancel_tool, "join_waitlist": join_waitlist, "ask_owner": ask_owner, "day_schedule": day_schedule,
    "answer_request": answer_request, "set_menu": set_menu, "mark_no_show": mark_no_show, "revenue_report": revenue_report, "lookup_business": lookup_business, "update_business": update_business,
}


def run(ctx: Ctx, name: str, arguments: str) -> dict:
    allowed = {t["function"]["name"] for t in (OWNER_TOOLS if ctx.is_owner else CUSTOMER_TOOLS)}
    if name not in allowed:
        return {"error": f"unknown tool {name}"}
    try:
        return IMPLS[name](ctx, **json.loads(arguments or "{}"))
    except SlotTaken as e:
        return {"error": str(e)}
    except (ValueError, TypeError, KeyError) as e:  # bad ids, dates, or argument names from the model
        return {"error": f"invalid arguments: {e}"}

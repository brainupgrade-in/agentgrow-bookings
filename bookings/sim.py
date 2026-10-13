"""Web simulator for judges: each browser session gets its own in-memory clinic running the real Agent."""
import base64
import binascii
import itertools
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from bookings import db, recovery
from bookings.agent import IST, Agent
from bookings.channels.whatsapp import Inbound
from bookings.seed import seed
from bookings.slots import FMT, book, cancel

OWNER_WA = "910000000000"
PATIENTS = {"priya": ("919800000001", "Priya"), "arjun": ("919800000002", "Arjun")}
OTHERS = [("919800000011", "Neha"), ("919800000012", "Karan"), ("919800000013", "Meera"), ("919800000014", "Rohan")]
CONSULT, ADJ, SOFT, DECOMP = 1, 2, 3, 4  # clinic seed service ids
DEPOSIT_CHECK_EVERY = 8  # seconds; the page polls state while a deposit link is open


def _open_day(d, step):
    while d.weekday() == 6:  # the demo clinic is closed on Sundays
        d += timedelta(days=step)
    return d


def seed_activity(conn, now: datetime) -> str:
    """A believable week: other patients on the books (the next open evening at 18:00 is full), and Arjun, a returning
    patient who missed an appointment and is now overdue for his follow-up. Returns the full evening's date."""
    full = _open_day(now.date() + timedelta(days=1), 1)
    at = lambda d, hh, mm=0: datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm)  # noqa: E731
    for (wa, name), (svc, hh) in zip(OTHERS, [(ADJ, 18), (ADJ, 18), (SOFT, 11), (DECOMP, 16)]):
        book(conn, 1, svc, at(full, hh), customer_wa=wa, customer_name=name)
    past = [(CONSULT, 30, "done"), (ADJ, 23, "no_show"), (ADJ, 16, "done")]  # days ago
    for svc, days, status in past:
        b = book(conn, 1, svc, at(_open_day(now.date() - timedelta(days=days), -1), 11), customer_wa=PATIENTS["arjun"][0],
                 customer_name="Arjun")
        conn.execute("UPDATE bookings SET status = ?, created_at = ? WHERE id = ?",  # made back then, not this week
                     (status, f"{b.start_at - timedelta(days=2):%Y-%m-%d %H:%M:%S}", b.id))
    return f"{full:%Y-%m-%d}"


class Session:
    def __init__(self, llm, blank: bool = False):
        self.conn = db.connect(":memory:")
        db.init(self.conn)
        self.full_day = None
        if blank:  # "set up from a photo": no services, no address; the owner builds it in the chat
            db.add_business(self.conn, name="Your Clinic", kind="clinic", owner_wa=OWNER_WA, open_time="09:00", close_time="19:00")
        else:
            seed(self.conn, "clinic", OWNER_WA)
            self.full_day = seed_activity(self.conn, datetime.now(IST).replace(tzinfo=None))
        self.owner_inbox: list[str] = []
        self.pushes: dict[str, list[str]] = {k: [] for k in PATIENTS}  # messages the clinic sends a patient unprompted
        self.messages = 0
        self.last_deposit_check = 0.0
        self.lock = threading.Lock()
        self.agent = Agent(self.conn, 1, llm, send=self._send)

    def _send(self, to: str, body: str) -> None:
        if to == OWNER_WA:
            self.owner_inbox.append(body)
        for key, (wa, _) in PATIENTS.items():
            if to == wa:
                self.pushes[key].append(body)

    def out(self, **extra) -> dict:
        return {"owner_inbox": self.owner_inbox[-30:], "pushes": {k: v[-30:] for k, v in self.pushes.items()}, **extra}


class Msg(BaseModel):
    sid: str = Field(min_length=1, max_length=64)
    role: Literal["customer", "owner"]
    patient: Literal["priya", "arjun"] = "priya"
    text: str = Field(default="", max_length=2000)
    image: str | None = Field(default=None, max_length=4_000_000)  # data:image/...;base64,... (the page downsizes photos)


class Sid(BaseModel):
    sid: str = Field(min_length=1, max_length=64)
    blank: bool = False


class Event(BaseModel):
    sid: str = Field(min_length=1, max_length=64)
    kind: Literal["cancel", "tick", "report"]


DATA_URL = re.compile(r"data:(image/(?:jpeg|png|webp));base64,(.+)", re.S)


class Sim:
    def __init__(self, llm, max_sessions: int = 200, max_messages: int = 60):
        self.llm, self.max_sessions, self.max_messages = llm, max_sessions, max_messages
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.lock = threading.Lock()
        self.ids = itertools.count()

    def get(self, sid: str, fresh: bool = False, blank: bool = False) -> Session:
        with self.lock:
            if fresh or sid not in self.sessions:
                self.sessions[sid] = Session(self.llm, blank)
                while len(self.sessions) > self.max_sessions:
                    self.sessions.popitem(last=False)  # evict the least recently used sandbox
            self.sessions.move_to_end(sid)
            return self.sessions[sid]

    def _spend(self, s: Session) -> None:
        if s.messages >= self.max_messages:
            raise HTTPException(429, "demo message limit reached; press Reset to start a new sandbox")
        s.messages += 1

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/sim")

        @r.post("/message")
        def message(m: Msg):
            image, mime = None, "image/jpeg"
            if m.image:
                found = DATA_URL.fullmatch(m.image)
                try:
                    mime, image = found.group(1), base64.b64decode(found.group(2), validate=True)
                except (AttributeError, binascii.Error):
                    raise HTTPException(422, "image must be a base64 JPEG, PNG or WebP data URL")
            elif not m.text.strip():
                raise HTTPException(422, "empty message")
            s = self.get(m.sid)
            with s.lock:
                self._spend(s)
                sender, name = (OWNER_WA, "Owner") if m.role == "owner" else PATIENTS[m.patient]
                inbound = Inbound(sender=sender, name=name, text=m.text, msg_id=f"sim-{next(self.ids)}", image=image, mime=mime)
                return s.out(reply=s.agent.handle(inbound) or "")

        @r.post("/event")
        def event(e: Event):
            """The demo's 'time passes' buttons: another patient cancels, the daily check runs, the weekly report goes out."""
            s = self.get(e.sid)
            with s.lock:
                self._spend(s)
                if e.kind == "cancel":
                    others = tuple(wa for wa, _ in OTHERS)
                    row = s.conn.execute(
                        f"SELECT b.id, b.start_at, c.name, sv.name AS service FROM bookings b JOIN customers c ON c.id = b.customer_id"
                        f" JOIN services sv ON sv.id = b.service_id WHERE b.status = 'booked' AND b.start_at > ?"
                        f" AND c.wa_id IN ({','.join('?' * len(others))}) ORDER BY b.start_at LIKE ? DESC, b.start_at DESC LIMIT 1",
                        (s.agent.now().strftime(FMT), *others, f"{s.full_day}T18:00")).fetchone()
                    if not row:
                        return s.out(event="No other patient has an upcoming booking to cancel.")
                    cancel(s.conn, row["id"])
                    recovery.forget_cancelled(s.conn, row["id"])
                    offered = recovery.offer_freed_slot(s.conn, 1, row["start_at"])
                    s.agent._flush()
                    when = datetime.strptime(row["start_at"], FMT)
                    return s.out(event=f"{row['name']} cancelled {row['service']} on {when:%a %d %b %H:%M}."
                                       + (" The slot was offered to the waitlist." if offered else " Nobody was waiting for it."))
                if e.kind == "tick":
                    d = s.agent.tick(any_hour=True)
                    return s.out(event=f"Daily check: {d['nudges']} follow-up reminder(s) sent, {d['deposits_paid']} deposit(s) received,"
                                       f" {d['holds_released']} unpaid hold(s) released.")
                s.agent.tick(force_report=True)
                return s.out(event="Weekly report sent to the owner.")

        @r.get("/state")
        def state(sid: str):
            s = self.get(sid)
            with s.lock:
                pending = s.conn.execute("SELECT 1 FROM bookings WHERE deposit_status = 'pending' AND status = 'booked'").fetchone()
                if pending and time.monotonic() - s.last_deposit_check > DEPOSIT_CHECK_EVERY:
                    s.last_deposit_check = time.monotonic()
                    s.agent.check_deposits()  # a judge may have just paid the Cashfree test link in another tab
                return s.out(**self._snapshot(s), pending_deposit=bool(pending))

        @r.post("/reset")
        def reset(m: Sid):
            self.get(m.sid, fresh=True, blank=m.blank)
            return {"ok": True}

        return r

    def _snapshot(self, s: Session) -> dict:
        c = s.conn
        biz = dict(c.execute("SELECT name, kind, open_time, close_time, closed_weekdays, chairs, address, website"
                             " FROM businesses WHERE id = 1").fetchone())
        services = [dict(x) for x in c.execute("SELECT name, price_inr, duration_min FROM services WHERE active = 1 ORDER BY id")]
        today = datetime.now(IST).date()
        days = []
        for i in range(3):
            d = today + timedelta(days=i)
            rows = c.execute(
                "SELECT substr(b.start_at, 12) AS time, substr(b.end_at, 12) AS end, s.name AS service, cu.name AS customer,"
                " b.chair, b.price_inr, b.source, b.deposit_status FROM bookings b JOIN services s ON s.id = b.service_id"
                " JOIN customers cu ON cu.id = b.customer_id WHERE b.status = 'booked' AND b.start_at LIKE ? ORDER BY b.start_at",
                (f"{d:%Y-%m-%d}%",))
            days.append({"date": f"{d:%Y-%m-%d}", "label": f"{d:%a %d %b}", "bookings": [dict(x) for x in rows]})
        waitlist = [dict(x) for x in c.execute(
            "SELECT cu.name AS customer, s.name AS service, w.day, w.status, w.offered_start FROM waitlist w"
            " JOIN customers cu ON cu.id = w.customer_id JOIN services s ON s.id = w.service_id"
            " WHERE w.status IN ('waiting', 'offered') ORDER BY w.id")]
        rec = [dict(x) for x in c.execute(
            "SELECT r.kind, r.amount_inr, cu.name AS customer, s.name AS service FROM recoveries r JOIN bookings b ON b.id = r.booking_id"
            " JOIN customers cu ON cu.id = b.customer_id JOIN services s ON s.id = b.service_id ORDER BY r.id")]
        calls = c.execute("SELECT model, COUNT(*) AS n, SUM(prompt_tokens) AS tin, SUM(completion_tokens) AS tout,"
                          " AVG(latency_ms) AS ms FROM llm_calls GROUP BY model").fetchall()
        full = None
        if s.full_day:
            d = datetime.strptime(s.full_day, "%Y-%m-%d").date()
            full = {"date": s.full_day, "word": "kal" if d == today + timedelta(days=1) else f"{d:%A}"}
        return {"business": biz, "services": services, "days": days, "waitlist": waitlist, "recovered": rec,
                "llm": [dict(x) for x in calls], "messages_left": self.max_messages - s.messages, "full_evening": full}

"""One agent turn: load context, let Nemotron call tools until it replies, persist everything."""
import dataclasses
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI

from bookings import onboarding, payments, recovery, tools
from bookings.slots import FMT
from bookings.channels.whatsapp import Inbound

log = logging.getLogger("bookings.agent")

IST = ZoneInfo("Asia/Kolkata")
HISTORY = 12  # past messages (in + out) given to the model

CUSTOMER_PROMPT = """You are the WhatsApp receptionist for {name}. Time now {time} IST.
Calendar (use these dates, do not compute your own): {calendar}.
Open {open}-{close}{closed}. Services: {services}.
This customer: {customer}.
Reply in the language AND script of the customer's latest message (Roman-script Hinglish gets Roman-script Hinglish),
briefly and warmly, like a person on WhatsApp: 1-3 short lines, no headings, no tables, never show internal ids.
Always use tools for availability and bookings; never invent prices, times or bookings, and never promise
receipts, calls, reminders or anything else no tool does.
"kal" means tomorrow, "parso" the day after. Confirm service, date and time before calling book.
If the time they want is full and no nearby time suits them, offer join_waitlist.
When offering times, suggest at most 3, near what they asked for.
Lines in [square brackets] come from the clinic's system, not from the patient: act on them as instructed. Language: always
match the patient's own latest message (Hinglish gets Hinglish), never the language of the [bracket] lines; with no message
from the patient yet, write in simple Hinglish.
Use ask_owner for discounts or anything outside these rules, and tell the customer you are checking with {name}.{rules}"""

KIND_RULES = {
    "clinic": """
This is a chiropractic clinic; customers are patients.
- Never diagnose, never give medical or exercise advice, never promise results. For questions about a condition, offer a consultation.
- A new patient (no past visits) books "Initial consultation & assessment" first, whatever they ask for.
- RED FLAGS: numbness or weakness in arms or legs, loss of bladder or bowel control, severe pain after a fall or accident,
  chest pain, or fever with back pain. Do NOT book. FIRST call ask_owner with a question starting "RED FLAG:" and the
  symptoms, so the doctor knows. THEN tell them to go to the nearest hospital emergency now or call 112. Be calm and short.""",
}

OWNER_PROMPT = """You are the assistant of {name}'s owner, on WhatsApp. Time now {time} IST. Calendar: {calendar}.
Business profile: {profile}. Services: {services}.
Open patient requests waiting for the owner: {requests}.
When the owner's message answers one of them, call answer_request with that request's # and the owner's decision; if several are
open and it is unclear which one is meant, ask. Then confirm in one short line that the patient is being told.
Answer schedule questions with tools. Be brief; the owner is busy with customers.
If the owner says a patient didn't come, find the booking with day_schedule and call mark_no_show. For money or
"how much did we recover" questions use revenue_report and quote its rupee numbers exactly. Reply in the owner's language and script.
Setup: when the owner sends a rate-card photo you get the services read from it. Show them as a short numbered list
(name - ₹price - minutes) and ask "Reply ok, or tell me what to change". Call set_menu with the full final list once
the owner approves; if one message both corrects and approves ("haircut is 60 min, rest ok"), apply it and call set_menu
right away. If the address is unknown, ask for the business name and area. When the owner gives the name, save it with
update_business at once, then call lookup_business. Propose only details the search clearly shows for this business;
save them with update_business after the owner confirms. If nothing matches, say so and ask for the address."""

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def nebius_client() -> OpenAI:
    return OpenAI(base_url=os.environ["NEBIUS_BASE_URL"], api_key=os.environ["NEBIUS_API_KEY"], timeout=90, max_retries=1)


class Agent:
    MAX_STEPS = 6

    def __init__(self, conn, business_id: int, llm, send: Callable[[str, str], None],
                 now: Callable[[], datetime] = lambda: datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)):
        self.conn, self.business_id, self.llm, self.send, self.now = conn, business_id, llm, send, now

    def handle(self, inbound: Inbound, flush: bool = True, system: bool = False) -> str | None:
        if not system:  # [square brackets] mark clinic-system notes; nobody may type one
            inbound = dataclasses.replace(inbound, text=inbound.text.replace("[", "(").replace("]", ")"))
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages (msg_id, business_id, wa_id, direction, body) VALUES (?, ?, ?, 'in', ?)",
            (inbound.msg_id, self.business_id, inbound.sender, inbound.text),
        )
        if cur.rowcount == 0:
            return None  # already processed (Meta redelivery)
        biz = self.conn.execute("SELECT * FROM businesses WHERE id = ?", (self.business_id,)).fetchone()
        is_owner = inbound.sender == biz["owner_wa"]
        ctx = tools.Ctx(self.conn, self.business_id, inbound.sender, inbound.name, is_owner, self.now(), self.send)
        model = os.environ.get("BOOKINGS_MODEL_OWNER" if is_owner else "BOOKINGS_MODEL_CUSTOMER") or (
            "nvidia/nemotron-3-super-120b-a12b")  # T5 eval: Super 18/18 at 2.8 s/turn, see eval/README.md
        if inbound.image is not None:
            inbound = self._read_photo(inbound, is_owner)
            self.conn.execute("UPDATE messages SET body = ? WHERE msg_id = ?", (inbound.text, inbound.msg_id))  # history keeps the list
        reply = self._loop(ctx, biz, model, inbound)
        self.conn.execute(
            "INSERT INTO messages (msg_id, business_id, wa_id, direction, body) VALUES (?, ?, ?, 'out', ?)",
            (f"out:{inbound.msg_id}", self.business_id, inbound.sender, reply),
        )
        if flush:
            self._flush()
        return reply

    # --- messages the clinic starts: owner answers, waitlist offers, care-plan reminders, deposits, weekly report ---

    def notify(self, wa_id: str, name: str, note: str, key: str) -> str | None:
        """Run a patient-side turn on a system note, so the patient hears it in their own language, and send it.
        `key` makes it idempotent (it is the message id)."""
        reply = self.handle(Inbound(sender=wa_id, name=name, text=f"[{note}]", msg_id=key), flush=False, system=True)
        if reply:
            try:
                self.send(wa_id, reply)
            except Exception:
                log.exception("could not send %s to %s", key, wa_id)
        return reply

    def _flush(self) -> None:
        """Deliver what the last turn queued: answers the owner just gave, and freed slots offered to the waitlist."""
        for r in self.conn.execute("SELECT * FROM owner_requests WHERE business_id = ? AND status = 'answered' ORDER BY id",
                                   (self.business_id,)).fetchall():
            self.conn.execute("UPDATE owner_requests SET status = 'relayed' WHERE id = ?", (r["id"],))
            self.notify(r["customer_wa"], r["customer_name"],
                        f"Message from the clinic owner, not from the patient. Your earlier question to the owner: \"{r['question']}\"."
                        f" The owner's answer: \"{r['answer']}\". Tell the patient, and ask what they'd like to do next if needed.",
                        f"owner-answer:{r['id']}")
        for w in recovery.pending_offers(self.conn, self.business_id):
            self.conn.execute("UPDATE waitlist SET notified_at = ? WHERE id = ?", (self.now().strftime(FMT), w["id"]))
            when = datetime.strptime(w["offered_start"], FMT)
            self.notify(w["wa_id"], w["name"],
                        f"Clinic system: a {w['service']} slot just opened on {when:%a %Y-%m-%d at %H:%M} because someone cancelled."
                        " This patient is on the waitlist for that day. Offer it in one or two lines; book only if they accept.",
                        f"offer:{w['id']}")

    def check_deposits(self) -> dict:
        """Ask Cashfree about every held booking: paid -> confirm and count it; past due -> release the slot to the waitlist."""
        now, done = self.now(), {"deposits_paid": 0, "holds_released": 0}
        pending = self.conn.execute(
            "SELECT b.*, c.wa_id, c.name FROM bookings b JOIN customers c ON c.id = b.customer_id"
            " WHERE b.business_id = ? AND b.status = 'booked' AND b.deposit_status = 'pending'", (self.business_id,)).fetchall()
        for b in pending:
            try:
                paid = payments.link_paid(b["deposit_link_id"])
            except Exception:
                log.exception("deposit status check failed for booking %s", b["id"])
                continue
            when = datetime.strptime(b["start_at"], FMT)
            if paid:
                self.conn.execute("UPDATE bookings SET deposit_status = 'paid' WHERE id = ?", (b["id"],))
                self.conn.execute("INSERT INTO recoveries (business_id, booking_id, kind, amount_inr, at) VALUES (?, ?, 'deposit', ?, ?)",
                                  (self.business_id, b["id"], recovery.DEPOSIT_INR, now.strftime(FMT)))
                done["deposits_paid"] += 1
                self.notify(b["wa_id"], b["name"], f"Clinic system: the ₹{recovery.DEPOSIT_INR} deposit for the appointment on"
                            f" {when:%a %Y-%m-%d at %H:%M} is received. Confirm the appointment in one line.", f"deposit-paid:{b['id']}")
            elif b["deposit_due"] and b["deposit_due"] <= now.strftime(FMT):
                self.conn.execute("UPDATE bookings SET status = 'cancelled', deposit_status = 'expired' WHERE id = ?", (b["id"],))
                recovery.offer_freed_slot(self.conn, self.business_id, b["start_at"])
                done["holds_released"] += 1
                self.notify(b["wa_id"], b["name"], f"Clinic system: the deposit for {when:%a %Y-%m-%d at %H:%M} was not paid in time,"
                            " so the held slot was released. Tell them kindly they are welcome to book again.", f"hold-released:{b['id']}")
        self._flush()
        return done

    def tick(self, force_report: bool = False, any_hour: bool = False) -> dict:
        """Scheduled work (every few minutes in production; the demo's buttons call it): deposits, reminders, weekly report.
        any_hour: the demo's "run the daily check" button stands for the morning run, whatever time the judge clicks it."""
        now, done = self.now(), self.check_deposits()
        done.update(nudges=0, report=False)
        biz = self.conn.execute("SELECT * FROM businesses WHERE id = ?", (self.business_id,)).fetchone()
        if any_hour or biz["open_time"] <= f"{now:%H:%M}" < biz["close_time"]:  # no reminders at night
            for r in recovery.due_nudges(self.conn, self.business_id, now):
                self.conn.execute("UPDATE customers SET last_nudge_at = ? WHERE id = ?", (now.strftime(FMT), r["id"]))
                done["nudges"] += 1
                self.notify(r["wa_id"], r["name"],
                            f"Clinic system, not the patient: their last visit was {r['service']} on {r['last_visit'][:10]}. Their care"
                            f" plan suggests a follow-up every {r['lapse_weeks']} weeks and nothing is booked. Send one short, warm"
                            " reminder offering to book the follow-up. No medical claims, no pressure.",
                            f"nudge:{r['id']}:{now:%Y%m%d}")
        week = f"{now - timedelta(days=now.weekday()):%Y-%m-%d}"
        if force_report or (now.weekday() == 0 and now.hour >= 9 and (biz["last_report_at"] or "") < week):
            text = recovery.report_text(recovery.report(self.conn, self.business_id, now), biz["name"])
            self.conn.execute("UPDATE businesses SET last_report_at = ? WHERE id = ?", (now.strftime(FMT), self.business_id))
            self.conn.execute("INSERT OR IGNORE INTO messages (msg_id, business_id, wa_id, direction, body) VALUES (?, ?, ?, 'out', ?)",
                              (f"report:{now:%Y%m%d%H%M}", self.business_id, biz["owner_wa"], text))
            try:
                self.send(biz["owner_wa"], text)
                done["report"] = True
            except Exception:
                log.exception("weekly report not sent")
        self._flush()
        return done

    def _read_photo(self, inbound: Inbound, is_owner: bool) -> Inbound:
        """Turn a photo into text for Nemotron. Only the owner's photos are read (as a rate card)."""
        caption = f" Caption: {inbound.text}" if inbound.text else ""
        if not is_owner:
            return dataclasses.replace(inbound, text=f"[The customer sent a photo, which you cannot see.]{caption}")
        services, st = onboarding.read_rate_card(self.llm, inbound.image, inbound.mime)
        self._log_call(inbound.sender, st["model"], st["usage"], st["latency_s"])
        if not services:
            note = "[The owner sent a photo, but no services with prices could be read from it.]"
        else:
            lines = "; ".join(f"{s['name']} ₹{s['price_inr']} {s['duration_min']} min" for s in services)
            note = f"[The owner sent a rate-card photo. Services read from it (NOT live yet, durations are estimates): {lines}]"
        return dataclasses.replace(inbound, text=note + caption)

    def _system(self, biz, ctx: tools.Ctx) -> str:
        services = ", ".join(f"{s['name']} (id {s['id']}, ₹{s['price_inr']}, {s['duration_min']} min)"
                             for s in tools.list_services(ctx)["services"])
        closed = [WEEKDAYS[int(d)] for d in biz["closed_weekdays"].split(",") if d]
        closed = f", closed {', '.join(closed)}" if closed else ""
        days = [ctx.now.date() + timedelta(days=i) for i in range(8)]
        labels = {0: "aaj/today", 1: "kal/tomorrow", 2: "parso"}
        calendar = "; ".join(f"{labels[i] + ' = ' if i in labels else ''}{d:%a %Y-%m-%d}" for i, d in enumerate(days))
        return (OWNER_PROMPT if ctx.is_owner else CUSTOMER_PROMPT).format(
            name=biz["name"], calendar=calendar, time=f"{ctx.now:%H:%M}",
            open=biz["open_time"], close=biz["close_time"], closed=closed,
            services=services or "none yet",
            profile=f"name {biz['name']}; address {biz['address'] or 'unknown'}; website {biz['website'] or 'unknown'};"
                    f" open {biz['open_time']}-{biz['close_time']}{closed};"
                    f" {biz['chairs']} practitioner(s)",
            rules=KIND_RULES[biz["kind"]], customer=self._customer_line(ctx),
            requests="; ".join(f"#{r['id']} {r['customer_name'] or r['customer_wa']}: \"{r['question']}\""
                               for r in tools.open_requests(self.conn, self.business_id)) or "none")

    def _customer_line(self, ctx: tools.Ctx) -> str:
        rows = self.conn.execute(
            "SELECT s.name, b.start_at FROM bookings b JOIN customers c ON c.id = b.customer_id JOIN services s ON s.id = b.service_id"
            " WHERE b.business_id = ? AND c.wa_id = ? AND b.status IN ('booked', 'done') ORDER BY b.start_at",
            (self.business_id, ctx.wa_id)).fetchall()
        past = [r for r in rows if r["start_at"] <= ctx.now.strftime("%Y-%m-%dT%H:%M")]
        if not rows:
            return f"{ctx.name or 'unknown name'}, NEW (no visits yet)"
        last = f", last {past[-1]['name']} on {past[-1]['start_at'][:10]}" if past else ""
        return f"{ctx.name or 'unknown name'}, returning ({len(past)} past visit(s){last}; {len(rows) - len(past)} upcoming)"

    def _history(self, wa_id: str, exclude: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT direction, body FROM messages WHERE business_id = ? AND wa_id = ? AND msg_id != ?"
            " ORDER BY rowid DESC LIMIT ?", (self.business_id, wa_id, exclude, HISTORY)).fetchall()
        return [{"role": "user" if r["direction"] == "in" else "assistant", "content": r["body"]} for r in reversed(rows)]

    def _loop(self, ctx: tools.Ctx, biz, model: str, inbound: Inbound) -> str:
        messages = [{"role": "system", "content": self._system(biz, ctx)},
                    *self._history(inbound.sender, inbound.msg_id),
                    {"role": "user", "content": inbound.text}]
        schema = tools.OWNER_TOOLS if ctx.is_owner else tools.CUSTOMER_TOOLS
        for _ in range(self.MAX_STEPS):
            t0 = time.monotonic()
            resp = self.llm.chat.completions.create(model=model, messages=messages, tools=schema, max_tokens=2048)
            self._log_call(inbound.sender, model, resp.usage, time.monotonic() - t0)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                if (msg.content or "").strip():
                    return msg.content.strip()
                # reasoning models sometimes spend the turn thinking and return no text; ask once more
                messages.append({"role": "user", "content": "(Now write your WhatsApp reply to the customer.)"})
                continue
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = tools.run(ctx, tc.function.name, tc.function.arguments)
                log.info("tool %s(%s) -> %s", tc.function.name, tc.function.arguments, json.dumps(result)[:200])
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
        log.warning("tool loop hit MAX_STEPS for %s", inbound.msg_id)
        return f"Sorry, I'm having trouble right now. Someone from {biz['name']} will reply shortly."

    def _log_call(self, wa_id: str, model: str, usage, latency_s: float) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls (business_id, wa_id, model, prompt_tokens, completion_tokens, latency_ms) VALUES (?, ?, ?, ?, ?, ?)",
            (self.business_id, wa_id, model, usage.prompt_tokens, usage.completion_tokens, int(latency_s * 1000)))
        log.info("llm model=%s in=%s out=%s latency=%.1fs", model, usage.prompt_tokens, usage.completion_tokens, latency_s)

# Spec: AgentGrow Bookings: a WhatsApp booking agent that sets itself up and wins back revenue

Status: **APPROVED 2026-10-13** (decisions below). Track: **Best Apps & Agents**. Submission closes 2026-10-30 22:30 IST.

## Objective

**Domain (changed 13 Oct, Decision 7): chiropractic clinics.** Small clinics take appointments over WhatsApp by hand. The
chiropractor is mid-adjustment while messages pile up. Cancelled slots stay empty, and patients drop out of their care plan
unnoticed. Some messages describe symptoms that need an emergency room, not an appointment. The WhatsApp bots
on the market today are scripted, take days of vendor setup (₹15k one-time plus ₹2.5–5k a month in India, or $1k plus $30–80 a month abroad), and can't understand voice notes.

AgentGrow Bookings is a WhatsApp agent that:
1. **Sets itself up from one photo.** The owner sends a photo of the fee card plus their hours. The agent extracts
   services, prices and durations, confirms them with the owner, and is live in about 2 minutes. A Tavily lookup of the
   business name prefills the address, hours and website.
2. **Books customers in any mix of language, by text or voice note.** For example, "kal shaam 6 baje adjustment ho jayega?" works.
   New patients start with an initial consultation. Red-flag symptoms are never booked: the patient is sent to emergency
   (112) and the doctor is alerted. The agent never gives medical advice.
   It checks availability, books, confirms, and sends a reminder.
3. **Is run by the owner from WhatsApp, with no dashboard.** For anything outside policy, the agent messages the owner with
   the options ("Priya wants 7pm, you're full. Squeeze her in or offer 7:30?") and acts on the reply.
4. **Recovers revenue and reports it in rupees.** A cancellation is offered to the waitlist automatically. Patients who
   fall behind their care plan get a follow-up nudge. Repeat no-shows are asked for a deposit. The owner gets a weekly message: "Recovered ₹8,400 this week."

**Users:** (a) the business owner, (b) the business's customers. Both use only WhatsApp. The judges use the **web simulator**
described below, because Meta's test number can only message 5 pre-registered phones.

**Demo businesses:** (1) a chiropractic clinic (the only domain for now; the salon was dropped 13 Oct). (2) Later:
**AgentGrow** lead conversion, i.e. booking discovery calls on Friday's number.
The AgentGrow one only happens if that number gets onto the Cloud API in time (see Decisions).

### User stories / acceptance
- Owner setup: the owner sends a rate-card photo → within 60 s gets back a structured service list (name, price,
  duration) to confirm. They reply "ok" or a correction, and the business is live. Accurate on 3 of 3 real rate-card photos.
- Customer text booking: "can I get a haircut tomorrow evening?" → offers real free slots → the customer picks one → the
  booking is stored and confirmed. A double booking is impossible because the DB enforces it, not the model.
- Customer voice-note booking: a voice note in Hinglish works the same as the text one.
- Exception to owner: a request outside policy (full day, unknown service, a discount ask) → the owner gets a WhatsApp message with
  options. The customer is told "checking with the salon". The owner's reply resolves it within the same conversation.
- Cancellation → waitlist: a cancellation triggers an offer to the first waitlisted customer. If they accept, they're booked and the
  recovered ₹ is recorded.
- Lapsed nudge: a customer with no visit for N weeks (set per service) gets one re-booking nudge, never more than one a month.
- Weekly report: the owner gets a weekly summary with bookings, recovered ₹ (from waitlist fills and nudges that converted) and
  no-shows.

## Architecture

```
WhatsApp Cloud API ──webhook──► FastAPI app ──► Agent loop (tool calling) ──► Nebius Token Factory
Web simulator (judges) ─────────►   │                 │                         ├ Nemotron 3 Nano: routine chat turns
                                    │                 │                         ├ Nemotron 3 Super: owner exceptions, onboarding
                                    ▼                 ▼                         └ non-NVIDIA VL model: rate-card photo → JSON only
                                 SQLite DB      Tools: list_services, find_slots, book, cancel,
                                 (bookings,      waitlist_add, ask_owner, record_recovery, tavily_lookup
                                  customers,
                                  messages)     Scheduler: reminders, waitlist offers, lapsed nudges, weekly report
```

- **One code path for both channels.** The WhatsApp webhook and the web simulator both turn a message into the same
  `Inbound(business_id, from, text|audio|image)` and call the same `handle()`.
- **The model never writes to the DB directly.** Tools validate everything. Slot math and double-booking checks are plain
  Python plus a DB unique constraint.
- **Model routing is explicit and logged per call** (model, tokens, latency), so the README and video can show the
  Nano/Super (+ VL extractor) split and the cost per booking.
- **Web simulator** (`/demo`): two phone-style panes, Customer and Owner, on one business. Seed buttons (salon or clinic) and a
  "fast-forward time" button that fires the scheduler (reminder, nudge, weekly report). This is the judges' working demo URL.

## Tech Stack
- Python 3.12, uv, FastAPI and uvicorn, `openai` SDK pointed at Token Factory, SQLite (stdlib `sqlite3`), APScheduler
  for timed jobs, `tavily-python`. Voice notes: stretch goal (see Decisions).
- Frontend: one static HTML/JS page served by FastAPI, with no build step.
- WhatsApp: Meta Cloud API (Graph API) called directly with `httpx`. For development: the free test number in a Meta developer
  app, which can message up to 5 verified recipients and needs no business verification.
- Hosting: a container image (`brainupgrade/agentgrow-bookings`). Hosted on **Nebius** (Serverless Endpoint or an AI Cloud VM),
  up until 15 Dec. For development only: a Cloudflare quick tunnel to the laptop for the Meta webhook.

## Commands
```bash
uv sync
uv run uvicorn bookings.app:app --reload --port 8080   # dev server; simulator at http://localhost:8080/demo
uv run pytest -q                                        # unit + scenario tests (fake LLM by default)
uv run pytest -q -m live                                # same scenarios against the real Token Factory
uv run scripts/smoke.py                                 # Token Factory access + model list
uv run python -m bookings.seed salon|clinic             # seed a demo business
```

## Project Structure
```
bookings/            app source
  app.py             FastAPI: /webhook/whatsapp, /api/sim/*, /demo
  agent.py           agent loop, model routing, system prompts
  tools.py           tool definitions + implementations (the only code that writes bookings)
  db.py              schema + queries
  scheduler.py       reminders, waitlist offers, lapsed nudges, weekly report
  channels/          whatsapp.py (Cloud API send/receive, media download), sim.py
  onboarding.py      rate-card photo → services; Tavily prefill
  static/demo.html   simulator
tests/               pytest; scenario tests drive handle() with scripted conversations
eval/                rate-card photos, voice notes, expected extractions; pass-rate script
docs/                SPEC.md, official-rules.txt
scripts/             smoke.py, ops helpers
```

## Code Style
Same as the rest of the repo: small modules, plain functions, type hints, no framework beyond FastAPI.
```python
def find_slots(db: DB, business_id: int, service_id: int, day: date) -> list[datetime]:
    """Free start times for a service on a day; pure slot math, no model involved."""
    svc = db.service(business_id, service_id)
    taken = db.bookings_on(business_id, day)
    return [t for t in db.opening_grid(business_id, day, svc.duration_min) if not overlaps(t, svc, taken)]
```

## Testing Strategy
- **Unit (pytest):** slot math, double-booking rejection, the waitlist offer and accept chain, recovery accounting, the lapsed-nudge rate limit.
- **Scenario tests:** scripted conversations run through `handle()` with a **fake LLM** that returns canned tool calls, so they
  are deterministic and run on every commit. The same scenarios are marked `live` and can be run against Token Factory.
- **Eval set** (`eval/`): about 10 rate-card photos and about 10 voice notes (Hinglish, Hindi, English) with expected output. The metric is the
  extraction and intent pass rate. We use it to choose prompts and models, and we quote it in the README.
- **Manual end-to-end:** before each milestone, run the real WhatsApp test number on Rajesh's phone and the simulator in a browser.

## Boundaries
- **Always:** call models only through Nebius Token Factory using NVIDIA models. Have tools validate every write. Keep secrets in `.env`.
  Run the tests before each commit. Update the CLAUDE.md Status section at each milestone.
- **Ask first:** anything on the **AgentGrow / Friday number** (Meta verification, coexistence, Cloud API attach: these are
  one-way steps, see `~/ai-business-agents/todo/friday-whatsapp-cloud-api.md`). Creating the public GitHub repo. Messaging
  any real person other than Rajesh. Publishing the video. Submitting on Devpost. Adding new paid services.
- **Never:** use OpenRouter, HF or any other model provider. Send business-initiated WhatsApp messages without opt-in. Commit
  `.env` or tokens. Let the model invent prices or slots that aren't in the DB.

## Success Criteria (submission-ready)
1. The simulator's public URL works for a judge with no login: they can seed the salon, book by text and by uploaded voice note, cancel, and watch
   the waitlist fill. Fast-forward shows the reminder, the nudge and the weekly ₹ report.
2. The same flows work on real WhatsApp through the Meta test number. We record them for the video.
3. Photo setup turns 3 of 3 real rate cards into a correct service list. The eval pass rate is reported in the README.
4. Every model call goes to Token Factory (shown in logs), with Nemotron doing all reasoning and tool calls. The README covers the model split
   and the cost per booking.
5. The video is under 3 min, the repo is public with Apache-2.0 and a README, and the Devpost form is filled in including feedback, a Tavily mention and the track.

## Milestones (17 days)
| Dates | Milestone |
|---|---|
| 13–15 Oct | Keys done (13 Oct). WhatsApp test number webhook round trip; Nemotron tool-calling spike; VL extraction on 3 rate cards |
| 16–19 Oct | Core: DB, slot tools, agent loop, text booking in the simulator and on WhatsApp; fake-LLM scenario tests |
| 20–22 Oct | Photo setup with Tavily prefill; voice notes; owner exceptions |
| 23–25 Oct | Revenue recovery: waitlist, nudges, deposits as a stub, weekly report; clinic seed |
| 26–27 Oct | Deploy (public URL), eval set and numbers, polish simulator |
| 28–29 Oct | Video, README, Devpost draft; (AgentGrow number if ready) |
| 30 Oct | Submit by midday IST, leaving a buffer before 22:30 |

## Decisions (2026-10-13)
1. **Models (option a):** Token Factory has **no NVIDIA vision or audio model** (all 4 NVIDIA models are text-only; Nano Omni is
   absent). Nemotron (Nano/Super) does all reasoning and tool calling; a Token Factory VL model only transcribes the rate-card
   photo to JSON. Voice notes become a stretch goal (self-hosted NVIDIA Parakeet on Nebius AI Cloud if credits allow); never a
   non-Nebius speech service.
2. **Hosting:** ~~Nebius (Serverless Endpoint / AI Cloud), not our k3s.~~ Changed 13 Oct (Rajesh, option B): the NUC k3s cluster.
   The account has no Nebius AI Cloud tenant (Token Factory only), and the rules only require the model calls to be on Nebius.
   All model calls still go to Token Factory.
3. **Deposits:** Cashfree **test environment** payment links (sandbox keys in `.env`).
4. **Name:** AgentGrow Bookings.
5. **WhatsApp:** dev and test use Meta's test number +1 555 658 6330 (app "AgentGrow Bookings Test", portfolio "You CBO AI Agent").
   Friday's number joins later via **coexistence only**, with Rajesh at the phone (never "Add phone number").
   Demo order: salon, then clinic, then AgentGrow on Friday's number.
6. **Friday (13 Oct):** Friday's number becomes the AgentGrow bookings line (coexistence), and Friday's CRM is the
   sales funnel: new businesses become Friday leads, and Friday reads schedules and ₹ recovered through a signed API (T4b). Friday's own
   model (Qwen on Spark) never answers booking chats, which stay on Nemotron via Token Factory.
7. **Domain (13 Oct, Rajesh):** chiropractic clinics only; the salon option is removed (code, seed, demo, eval). The AgentGrow
   lead-conversion business comes later as a second `businesses.kind`. Clinic rules live in the prompt (`KIND_RULES`): new patients
   start with a consultation, red flags are never booked (emergency + alert the doctor), no medical advice. `chairs` now means
   practitioners. Eval: Super 22/22 on 11 clinic scenarios (`eval/README.md`, run 3).

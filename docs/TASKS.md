# Tasks: AgentGrow Bookings

Source: `docs/SPEC.md`. Order = dependency order. Each task: acceptance, verify, files. Tick when verified, not when written.

## M1: WhatsApp round trip (13–15 Oct)
- [x] **T1 Webhook skeleton.** (13 Oct: 8 tests pass) FastAPI app: `GET /webhook/whatsapp` echoes `hub.challenge` when `hub.verify_token` matches;
  `POST` checks `X-Hub-Signature-256` (HMAC of the raw body with `WHATSAPP_APP_SECRET`) and returns 403 if it's wrong.
  - Verify: `uv run pytest -q tests/test_webhook.py` (good/bad verify token, good/bad signature).
  - Files: `bookings/app.py`, `bookings/channels/whatsapp.py`, `tests/test_webhook.py`, `pyproject.toml`.
- [x] **T2 Inbound → reply.** (13 Oct: live round trip from Rajesh's phone, Nemotron Nano 3.3 s, reply delivered) Parse a text message into `Inbound`, call `handle()` (for now one Nemotron Nano call with a salon
  receptionist prompt), send the reply with the Graph `/messages` API. Ignore status callbacks. Mark processed message ids so
  Meta's retries are idempotent.
  - Verify: unit test with a recorded webhook payload and fake sender. Live: `cloudflared tunnel --url localhost:8080`,
    webhook configured in the Meta app, Rajesh texts the test number → gets a Nemotron reply.
  - Files: `bookings/agent.py`, `bookings/channels/whatsapp.py`, `tests/test_inbound.py`.
  - Needs Rajesh: add his WhatsApp number as a test recipient (OTP), and set the callback URL in the Meta app.

## M2: Core booking (16–19 Oct)
- [x] **T3 DB + slot math.** (13 Oct: 9 tests incl. an 8-thread race for the last slot) SQLite schema (business, service, staff hours, customer, booking, waitlist, message, recovery);
  `find_slots`, `book` and `cancel` as pure functions, with a unique constraint against double booking.
  - Verify: `pytest tests/test_slots.py`: overlaps, closing time, double-book rejected.
- [x] **T4 Agent loop with tools.** (13 Oct: 12 fake-LLM scenarios; live 4-turn Hinglish book/list/cancel on Nano, 1–4.5 s/step) Nemotron tool calling (`list_services`, `find_slots`, `book`, `cancel`, `waitlist_add`,
  `ask_owner`), with model routing (Nano by default, Super for owner/exception turns) and per-call logging (model, tokens, latency).
  - Verify: scenario tests with a fake LLM (deterministic); `pytest -m live` runs the same scenarios against Token Factory.
- [x] **T4b Friday hook (AgentGrow funnel).** (13 Oct: 6 API tests; dry run from Friday's pod OK. Install into Friday's workspace
  + a K8s CronJob = pending Rajesh's yes.) A `/api/v1` with a bearer key (`BOOKINGS_API_KEY` in `.env`) that Friday's OpenClaw
  agent calls as a tool: list businesses, day schedule, recovered ₹. Friday **pulls** new businesses with
  `integrations/friday/bookings-lead-sync.js` (Node + pg, same INSERT shape as `fb-lead-sync.js`; source `agentgrow_bookings`,
  dedupe on `metadata.bookings_business_id`). Pull, because on Nebius we cannot reach the NUC Postgres. Friday never answers booking chats; Nemotron does
  (hackathon rule: model calls only through Token Factory).
  - Verify: unit tests for the signature and payloads; Friday calls `/api/v1/businesses` from its pod.
- [x] **T5 Pick NEBIUS_MODEL defaults.** (13 Oct: **Super 18/18 at 2.8 s/turn → default**; Nano 18/18 at 9.2 s; Lightning 17/18 at 1.1 s.
  4 fixes came out of run 1, see `eval/README.md`.) (Spike 13 Oct: all 3 NVIDIA models do tool calls; Lightning 0.5 s but read "kal"
  as today; Nano 2.9 s and Super 1.1 s got the date right.)
  Run the live scenarios on Nano vs Super vs 3.5-Lightning; pick by pass rate and latency.
  - Verify: table in `eval/README.md`.
- [x] **T6 Simulator.** (13 Oct: https://bookings.agentgrow.io/demo, per-session in-memory salon, 8 tests; browser-verified:
  Hinglish book → schedule updates; discount → owner phone; owner "kal kitni bookings?" → correct. The fast-forward button
  arrives with T9; mobile layout not checked on a real phone yet.) `/demo`: Patient and Owner phone panes, seed clinic, a "fast-forward" button. Same `handle()` as WhatsApp.
  - Verify: open in a browser; book, cancel and rebook by text.

## M3: Differentiators (20–25 Oct)
- [x] **T7 Photo setup.** (13 Oct: Qwen3.8-27B 3/3 synthetic cards; Tavily live; demo "Set up from a photo". Real photos still wanted.) Rate-card photo → VL model on Token Factory → JSON services → Nemotron confirms with the owner → live.
  Tavily prefills the address, hours and website from the business name.
  - Verify: `eval/ratecards/` 3 of 3 correct; the Tavily call is in the logs.
- [x] **T8 Owner exceptions.** (13 Oct: `owner_requests` table, `answer_request` owner tool, answer relayed as a patient-side
  turn in the patient's language; shown live in the demo's patient phone.) `ask_owner` messages the owner; the owner's reply resolves the pending customer request.
- [x] **T9 Revenue recovery.** (13 Oct: `bookings/recovery.py` + `payments.py`; Agent.notify/_flush/tick; demo buttons. Deposit *paid* path is unit-tested, and live up to the Cashfree sandbox link; nobody has paid a test link yet.)
  Cancellation → waitlist offer; lapsed nudge (rate-limited); no-show → Cashfree **test** payment
  link for a deposit; weekly ₹ report. The scheduler can be fast-forwarded from the simulator.
  - Verify: scenario tests for each; the Cashfree sandbox link opens.
- [x] **T10 Clinic seed.** Superseded 13 Oct: the clinic is now the only domain (SPEC Decision 7); the salon was removed.
  AgentGrow lead conversion comes later as a second business kind.

## M4: Ship (26–30 Oct)
- [x] **T11 Deploy.** (13 Oct: NUC k3s, ns `agentgrow-bookings`, `scripts/deploy-nuc.sh`; the tunnel route points at the in-cluster Service. Not Nebius AI Cloud: no tenant, see SPEC Decision 2.)
- [x] **T12 README.** (13 Oct: pitch, judge walkthrough, mermaid architecture, Nemotron eval table, cost per conversation, run steps, Token Factory feedback, screenshot.)
- [ ] **T13 Video (< 3 min), Devpost form, feedback section.** Submit by midday 30 Oct IST.
- [ ] **T14 (optional) Friday's number via coexistence** (Rajesh at the phone), for the AgentGrow demo.

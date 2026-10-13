# Model eval: which Nemotron answers WhatsApp customers?

`uv run python -m eval.run --runs 2` runs 9 salon scenarios × 2 runs per model, live on Nebius Token Factory. The clock is frozen at
Tue 2026-10-13 15:00 IST. Each run passes or fails on **database state** (what got booked or cancelled, and whether the owner was messaged),
never on the wording of the reply.

Scenarios: Hinglish "kal shaam 6 baje", English "Thursday 11am", "parso 4 baje", a fully booked slot (no double booking), a price
question, cancelling own booking, a past time, an after-hours request, and a discount request that must go to the owner.

## Results (13 Oct 2026)

Run 1, with the first prompt:

| model | pass | s/turn |
|---|---|---|
| Nemotron-3-Nano-30B-A3B | 16/18 | 6.2 |
| Nemotron-3-Super-120B-A12B | 15/18 | 3.2 |
| Nemotron-3.5-Lightning | 15/18 | 1.1 |

The failures were mostly **ours, not the models'**, and each one became a code fix with a regression test:
- Wrong relative day ("parso" read as tomorrow): the prompt now carries a 7-day calendar (`kal/tomorrow = Wed 2026-10-14` …).
- A repeat `book` after "yes please" double-booked (2 chairs) → `book` is idempotent per customer, service and start.
- Nano sometimes returned an empty reply after reasoning → the agent asks once more instead of sending nothing.
- Roman-script Hinglish answered in Devanagari → reply in the customer's script.
- The after-hours scenario counted "accepted the offered 3:30 PM" as a failure, which was wrong; it now fails only on a booking after closing.

Run 2, after the fixes:

| model | pass | s/turn | in tok/convo | out tok/convo | USD per 1,000 convos |
|---|---|---|---|---|---|
| Nemotron-3-Nano-30B-A3B | 18/18 | 9.2 | 5290 | 3172 | 1.08 |
| **Nemotron-3-Super-120B-A12B** | **18/18** | **2.8** | 5338 | 351 | 1.92 |
| Nemotron-3.5-Lightning | 17/18 | 1.1 | 6055 | 146 | 0.40 |

**Decision:** Super is the default for customers and owners (`BOOKINGS_MODEL_CUSTOMER` / `BOOKINGS_MODEL_OWNER` override it).
It is perfect on the suite at under 3 s, and costs about ₹0.16 per conversation. Nano is as accurate but 3× slower, because it spends ~3k
output tokens reasoning. Lightning is the fastest and cheapest but missed a cancel confirmation; it is the candidate if latency ever matters more.
Prices: Token Factory catalog, 13 Oct 2026 (USD per 1M tokens, input/output): Nano and Lightning 0.06/0.24, Super 0.30/0.90.
Raw logs: `data/eval-2026-10-13*.log` (local).

## Run 3: chiropractic clinic (13 Oct, after the domain switch)

The product now focuses on chiropractic clinics, so the suite was rewritten as 11 clinic scenarios × 2 runs. It adds: a new patient
must start with the initial consultation; Sunday is closed; red-flag symptoms (numbness and weakness after a fall) must not be
booked, and must send the patient to emergency and alert the doctor; and no medical advice ("which exercise for slip disc?").

| model | pass | s/turn | in tok/convo | out tok/convo | USD per 1,000 convos |
|---|---|---|---|---|---|
| **Nemotron-3-Super-120B-A12B** | **22/22** | **3.1** | 6061 | 343 | 2.13 |
| Nemotron-3-Nano-30B-A3B | 21/22 | 16.2 | 5791 | 3575 | 1.21 |
| Nemotron-3.5-Lightning | 20/22 | 1.4 | 7137 | 183 | 0.47 |

- Super stays the default.
- Lightning booked a new patient into "today 3:30 pm" instead of tomorrow 6 pm. It also said "cancelled" without cancelling, so it isn't safe for patients.
- Nano's one miss is a matcher miss: it offered a consultation, but in Devanagari script.

Raw logs: `data/eval-clinic-*.log` (local).

## Rate-card photos (T7)

`uv run python eval/ratecards.py [model ...]` reads the 3 cards in `bookings/static/ratecards/` (drawn by
`scripts/make_ratecards.py`): a printed fee card with an "onwards" price, a fee table with a Duration column, and a tilted,
blurred phone photo of a Hindi + English board with "800/-" prices. A card passes when every expected (name, price) in
`eval/ratecards.json` is found and nothing extra is. Durations are scored only where the card prints them; elsewhere the model
estimates them and the owner corrects them in the chat.

No NVIDIA vision model is on Token Factory, so this single transcription call uses a non-NVIDIA open model there. Nemotron does all the talking and tool calls.

First run (salon cards, before the clinic switch):

| model (Token Factory, 13 Oct) | pass | s/card |
|---|---|---|
| **Qwen/Qwen3.8-27B** | **3/3** | **3.2** |
| deepseek-ai/DeepSeek-V4.1-Flash | 3/3 | 7.0 |
| openbmb/MiniCPM-V-4_5 | 2/3 (dropped the Women "Head Massage" column) | 2.6 |
| moonshotai/Kimi-K2.6 | 2/3 ("Haircut" vs "Hair Cutting"; a strict-matcher miss) | 12.5 |
| zai-org/GLM-5.3-Flash | timed out | – |

Clinic cards (current set): **Qwen3.8-27B 3/3 at 3.2 s/card**, and DeepSeek-V4.1-Flash 3/3 at 5.6 s.

**Decision:** Qwen3.8-27B (`BOOKINGS_MODEL_VISION` overrides it). Caveat: these cards are synthetic; real photos from clinics
are still to be added.

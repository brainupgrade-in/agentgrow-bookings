"""Owner setup: rate-card photo -> service list (vision model), and a Tavily lookup that prefills the business profile."""
import base64
import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("bookings.onboarding")

# No NVIDIA vision model is on Token Factory (SPEC Decisions 1), so this one call goes to a non-NVIDIA VL model there.
# It only transcribes; Nemotron does every decision. Chosen by eval/ratecards.py.
VISION_MODEL = "Qwen/Qwen3.8-27B"

RATE_CARD_PROMPT = """This is a photo of a clinic's fee card or rate card. List every bookable service on it as JSON:
{"services": [{"name": "...", "price_inr": 300, "duration_min": 30}]}
Rules:
- name in English, short, as a patient would say it; keep qualifiers like (Kids) / (Follow-up).
- A table with two price columns (e.g. Men / Women, Single / Senior) means two services per row: "<service> (<column>)".
- price_inr is an integer; for "onwards", "from" or a range use the lowest number. Skip items without a price.
- duration_min: use the card's duration if printed, else your best estimate of a typical appointment, a multiple of 15.
If the image is not a rate card, return {"services": []}. Output only the JSON."""


def read_rate_card(llm, image: bytes, mime: str = "image/jpeg", model: str | None = None) -> tuple[list[dict], dict]:
    """Returns (services, stats). Services are validated: name, int price > 0, int duration > 0."""
    model = model or os.environ.get("BOOKINGS_MODEL_VISION") or VISION_MODEL
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    t0 = time.monotonic()
    resp = llm.chat.completions.create(model=model, max_tokens=4096, temperature=0, messages=[{"role": "user", "content": [
        {"type": "text", "text": RATE_CARD_PROMPT}, {"type": "image_url", "image_url": {"url": url}}]}])
    stats = {"model": model, "usage": resp.usage, "latency_s": time.monotonic() - t0}
    return parse_services(resp.choices[0].message.content or ""), stats


def parse_services(content: str) -> list[dict]:
    m = re.search(r"\{.*\}", content, re.S)
    try:
        items = json.loads(m.group(0))["services"] if m else []
    except (json.JSONDecodeError, KeyError, TypeError):
        log.warning("rate card: unparseable model output %r", content[:300])
        return []
    out = []
    for it in items:
        try:
            name, price, dur = str(it["name"]).strip(), int(it["price_inr"]), int(it.get("duration_min") or 30)
        except (KeyError, TypeError, ValueError):
            continue
        if name and price > 0:
            out.append({"name": name, "price_inr": price, "duration_min": max(15, dur)})
    return out


def tavily_lookup(query: str) -> dict:
    """Web search for the business; Nemotron reads the snippets and proposes address, hours and website to the owner."""
    r = httpx.post("https://api.tavily.com/search", timeout=20, headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                   json={"query": query, "max_results": 5, "include_answer": True, "search_depth": "basic"})
    r.raise_for_status()
    data = r.json()
    log.info("tavily query=%r results=%d", query, len(data.get("results", [])))
    return {"answer": data.get("answer"),
            "results": [{"title": x["title"], "url": x["url"], "snippet": x["content"][:400]} for x in data.get("results", [])]}

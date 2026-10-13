"""Rate-card extraction eval: each card passes when every expected (name, price) is found and nothing extra is.

Usage: uv run python eval/ratecards.py [model ...]     (images: bookings/static/ratecards/, truth: eval/ratecards.json)
"""
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from bookings.agent import nebius_client  # noqa: E402
from bookings.onboarding import VISION_MODEL, read_rate_card  # noqa: E402

STOP = {"s", "and", "the"}


def words(name: str) -> set[str]:
    name = name.lower().replace("’", "'").replace("'s", "").replace("gents", "men").replace("ladies", "women")
    return {w for w in re.findall(r"[a-z]+", name) if w not in STOP}


def match(expected: list, got: list[dict]) -> tuple[list, list]:
    """Greedy: each expected item takes the same-price extraction with the best word overlap (Jaccard >= 0.5).
    An expected item with a third value (minutes printed on the card) must also match the duration."""
    left, missing = list(got), []
    for name, price, *dur in expected:
        cands = [(len(words(name) & words(g["name"])) / len(words(name) | words(g["name"])), i)
                 for i, g in enumerate(left) if g["price_inr"] == price and (not dur or g["duration_min"] == dur[0])]
        best = max(cands, default=(0, None))
        if best[0] >= 0.5:
            left.pop(best[1])
        else:
            missing.append((name, price, *dur))
    return missing, left


def main(models: list[str]) -> None:
    truth = json.loads((ROOT / "eval" / "ratecards.json").read_text())
    llm = nebius_client()
    for model in models:
        passed, secs, tin, tout = 0, 0.0, 0, 0
        for card, expected in truth.items():
            try:
                got, st = read_rate_card(llm, (ROOT / "bookings/static/ratecards" / card).read_bytes(), model=model)
            except Exception as e:  # noqa: BLE001 — a model that errors simply fails the card
                print(f"  {model} {card}: ERROR {str(e)[:120]}")
                continue
            missing, extra = match(expected, got)
            ok = not missing and not extra
            passed += ok
            secs += st["latency_s"]; tin += st["usage"].prompt_tokens; tout += st["usage"].completion_tokens
            print(f"  {model} {card}: {'PASS' if ok else 'FAIL'} {len(got)} items {st['latency_s']:.1f}s"
                  + (f" missing={missing}" if missing else "") + (f" extra={[(g['name'], g['price_inr']) for g in extra]}" if extra else ""))
        print(f"{model}: {passed}/{len(truth)}  {secs / len(truth):.1f}s/card  tokens in={tin} out={tout}\n")


if __name__ == "__main__":
    main(sys.argv[1:] or [VISION_MODEL])

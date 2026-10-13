"""Smoke test for Nebius Token Factory: list NVIDIA models, then make one chat call.

Usage: uv run scripts/smoke.py [MODEL_ID]
"""
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url=os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
    api_key=os.environ["NEBIUS_API_KEY"],
)

nvidia = sorted(m.id for m in client.models.list() if m.id.lower().startswith("nvidia/"))
print("NVIDIA models on Token Factory:")
for mid in nvidia:
    print("  ", mid)

model = sys.argv[1] if len(sys.argv) > 1 else os.getenv("NEBIUS_MODEL") or next(
    (m for m in nvidia if "nemotron" in m.lower()), None
)
if not model:
    sys.exit("No Nemotron model found; pass a model id.")

resp = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with exactly: pong"}],
    max_tokens=512,
)
print(f"\n{model} -> {resp.choices[0].message.content!r}  usage={resp.usage}")

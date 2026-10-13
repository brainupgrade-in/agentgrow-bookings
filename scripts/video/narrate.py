"""Generate one narration mp3 per scene with ElevenLabs.

Usage: python scripts/video/narrate.py [video_dir]   (reads script.json, writes audio/<id>.mp3; key ELEVENLABS_API_KEY in env)
"""
import json
import os
import sys
from pathlib import Path

import httpx

VOICE = os.environ.get("ELEVENLABS_VOICE", "JBFqnCBsd6RMkjVDRZzb")  # George - Warm, Captivating Storyteller
D = Path(sys.argv[1] if len(sys.argv) > 1 else "data/video")
(D / "audio").mkdir(exist_ok=True)
for s in json.loads((D / "script.json").read_text()):
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_192",
                   headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=120,
                   json={"text": s["text"], "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.3, "use_speaker_boost": True}})
    r.raise_for_status()
    (D / "audio" / f"{s['id']}.mp3").write_bytes(r.content)
    print(s["id"], len(r.content), flush=True)

"""WhatsApp Cloud API: webhook signature check, payload parsing, and sending replies."""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Inbound:
    sender: str
    name: str
    text: str
    msg_id: str
    media_id: str | None = None  # WhatsApp image id, downloaded before the agent runs
    image: bytes | None = None
    mime: str = "image/jpeg"


def signature_ok(body: bytes, header: str | None, secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def parse_messages(payload: dict) -> list[Inbound]:
    """Text and image messages in a webhook payload; status callbacks and other types are skipped."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                if m.get("type") == "text":
                    out.append(Inbound(sender=m["from"], name=names.get(m["from"], ""), text=m["text"]["body"], msg_id=m["id"]))
                elif m.get("type") == "image":
                    out.append(Inbound(sender=m["from"], name=names.get(m["from"], ""), text=m["image"].get("caption", ""),
                                       msg_id=m["id"], media_id=m["image"]["id"], mime=m["image"].get("mime_type", "image/jpeg")))
    return out


def to_whatsapp(text: str) -> str:
    """Markdown from the model -> WhatsApp formatting (*bold*, no headings)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    return text.strip()


def send_text(to: str, text: str) -> None:
    url = f"https://graph.facebook.com/{os.environ['WHATSAPP_GRAPH_VERSION']}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_ACCESS_TOKEN']}"},
        json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": to_whatsapp(text)}},
        timeout=20,
    )
    r.raise_for_status()


def download_media(media_id: str) -> bytes:
    """Graph returns a short-lived URL for the media id; fetching it needs the same bearer token."""
    auth = {"Authorization": f"Bearer {os.environ['WHATSAPP_ACCESS_TOKEN']}"}
    meta = httpx.get(f"https://graph.facebook.com/{os.environ['WHATSAPP_GRAPH_VERSION']}/{media_id}", headers=auth, timeout=20)
    meta.raise_for_status()
    r = httpx.get(meta.json()["url"], headers=auth, timeout=30)
    r.raise_for_status()
    return r.content

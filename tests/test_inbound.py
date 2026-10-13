import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from bookings.channels.whatsapp import Inbound

SECRET = "test-app-secret"


def text_payload(msg_id: str, sender: str = "919800000001", text: str = "hi") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "100000000000001"},
            "contacts": [{"profile": {"name": "Priya"}, "wa_id": sender}],
            "messages": [{"from": sender, "id": msg_id, "timestamp": "1760340000", "type": "text", "text": {"body": text}}],
        }}]}],
    }


def status_payload() -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "100000000000001"},
        "statuses": [{"id": "wamid.X", "status": "delivered", "recipient_id": "919800000001"}],
    }}]}]}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    from bookings.app import create_app

    seen, sent = [], []

    def handle(inbound: Inbound) -> str:
        seen.append(inbound)
        return f"echo: {inbound.text}"

    client = TestClient(create_app(handle=handle, send=lambda to, text: sent.append((to, text)),
                                   fetch_media=lambda media_id: f"bytes-of-{media_id}".encode()))

    def post(payload: dict):
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        return client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig})

    return post, seen, sent


def test_text_message_is_handled_and_replied(harness):
    post, seen, sent = harness
    assert post(text_payload("wamid.1", text="adjustment tomorrow?")).status_code == 200
    assert seen == [Inbound(sender="919800000001", name="Priya", text="adjustment tomorrow?", msg_id="wamid.1")]
    assert sent == [("919800000001", "echo: adjustment tomorrow?")]


def test_meta_retry_of_same_message_is_ignored(harness):
    post, seen, sent = harness
    post(text_payload("wamid.2"))
    post(text_payload("wamid.2"))
    assert len(seen) == 1 and len(sent) == 1


def test_markdown_is_converted_to_whatsapp_formatting():
    from bookings.channels.whatsapp import to_whatsapp

    assert to_whatsapp("## Done\nYour **Adjustment** is *booked*") == "Done\nYour *Adjustment* is *booked*"


def test_status_callbacks_are_ignored(harness):
    post, seen, sent = harness
    assert post(status_payload()).status_code == 200
    assert seen == [] and sent == []


def test_image_message_is_downloaded_before_the_agent_runs(harness):
    post, seen, _ = harness
    payload = text_payload("wamid.3")
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": "919800000001", "id": "wamid.3", "timestamp": "1760340000", "type": "image",
        "image": {"id": "media-9", "mime_type": "image/png", "caption": "our rates"}}
    post(payload)
    assert seen[0].text == "our rates" and seen[0].image == b"bytes-of-media-9" and seen[0].mime == "image/png"

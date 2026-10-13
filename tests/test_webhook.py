import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

SECRET = "test-app-secret"
VERIFY = "test-verify-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    from bookings.app import create_app

    return TestClient(create_app(handle=lambda inbound: None, send=lambda to, text: None))


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_verify_handshake_echoes_challenge(client):
    r = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"


def test_verify_handshake_rejects_wrong_token(client):
    r = client.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert r.status_code == 403


def test_post_with_valid_signature_is_accepted(client):
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    r = client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 200


@pytest.mark.parametrize("header", [None, "sha256=deadbeef"])
def test_post_with_missing_or_bad_signature_is_rejected(client, header):
    body = b'{"object": "whatsapp_business_account", "entry": []}'
    headers = {"X-Hub-Signature-256": header} if header else {}
    r = client.post("/webhook/whatsapp", content=body, headers=headers)
    assert r.status_code == 403


def test_cashfree_webhook_only_triggers_a_status_recheck(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v")
    from fastapi.testclient import TestClient
    from bookings.app import create_app
    ticks = []
    client = TestClient(create_app(handle=lambda i: None, send=lambda t, b: None, tick=lambda: ticks.append(1)))
    assert client.post("/webhook/cashfree", json={"data": {"link_status": "PAID"}}).status_code == 200
    assert ticks == [1]

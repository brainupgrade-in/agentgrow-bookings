"""Cashfree Payment Links (sandbox while CASHFREE_ENV=sandbox): no-show deposits."""
import os

import httpx

API_VERSION = "2023-08-01"


def _base() -> str:
    return "https://api.cashfree.com/pg" if os.environ.get("CASHFREE_ENV") == "production" else "https://sandbox.cashfree.com/pg"


def _headers() -> dict:
    return {"x-client-id": os.environ["CASHFREE_APP_ID"], "x-client-secret": os.environ["CASHFREE_SECRET_KEY"],
            "x-api-version": API_VERSION}


def create_link(link_id: str, amount_inr: int, wa_id: str, name: str, purpose: str) -> str:
    """Returns the pay URL. The phone is the WhatsApp number without the country code (Cashfree wants 10 digits for India)."""
    public = os.environ.get("BOOKINGS_PUBLIC_URL", "https://bookings.agentgrow.io")
    r = httpx.post(f"{_base()}/links", headers=_headers(), timeout=20, json={
        "link_id": link_id, "link_amount": amount_inr, "link_currency": "INR", "link_purpose": purpose[:500],
        "customer_details": {"customer_phone": wa_id[-10:], "customer_name": name or "Patient"},
        "link_notify": {"send_sms": False, "send_email": False},
        "link_meta": {"notify_url": f"{public}/webhook/cashfree", "return_url": f"{public}/demo"}})
    r.raise_for_status()
    return r.json()["link_url"]


def link_paid(link_id: str) -> bool:
    r = httpx.get(f"{_base()}/links/{link_id}", headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json().get("link_status") == "PAID"

"""
Thin client for smskash.com's Vodafone/Etisalat Cash payment confirmation
API. This is a third-party service (not an official Vodafone integration)
that confirms a cash transfer was received by matching the phone number and
amount the customer reports against SMS notifications they pick up on their
end - reverse-engineered from the embeddable payment widget they provide
(their official product is a website widget, not a bot API; the two
endpoints below are what that widget actually calls).

Two endpoints, no API key - the "siteid" itself identifies your account:
- GET  /api/site/{siteid}   -> {"wallet": "...", "rate": ...}
- POST /api/confirm         -> {"code": 200, "message": "..."} on success
"""
import logging
import requests

import config

log = logging.getLogger("smskash_api")

BASE_URL = "https://smskash.com/api"
TIMEOUT = 15


class SMSKashError(Exception):
    pass


def get_site_info() -> dict:
    """GET /api/site/{siteid} - your wallet number and current USD->EGP rate
    as configured on smskash's side."""
    if not config.SMSKASH_SITE_ID:
        raise SMSKashError("SMSKASH_SITE_ID غير موجود في .env")
    try:
        resp = requests.get(f"{BASE_URL}/site/{config.SMSKASH_SITE_ID}", timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("smskash site info fetch failed: %s", e)
        raise SMSKashError(str(e)) from e


def confirm_payment(phone: str, amount: float, username: str = "") -> dict:
    """POST /api/confirm - asks smskash "did this phone number send this
    exact amount to my wallet?" and gets an immediate yes/no - there's no
    webhook, this is called on-demand (right after the customer reports a
    transfer, and again later if they ask to recheck).

    Per smskash's own docs: 200 means a matching, not-yet-used transaction
    was found and accepted (response also carries trans_id/sender/time);
    400 means "no matching transaction found, or it was already used
    before" - the same signal either way as far as we're concerned: not
    confirmed (yet).

    Returns a dict: {"confirmed": bool, "message": str, "trans_id": str|None,
    "sender": str|None, "time": str|None}. Never raises for a plain "not
    confirmed yet" - only for a genuine network/API failure (SMSKashError),
    which callers should treat the same as "not confirmed yet, try later"."""
    if not config.SMSKASH_SITE_ID:
        raise SMSKashError("SMSKASH_SITE_ID غير موجود في .env")
    try:
        resp = requests.post(
            f"{BASE_URL}/confirm",
            json={"phone": phone, "amount": str(amount), "siteid": config.SMSKASH_SITE_ID, "username": username},
            timeout=TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.error("smskash confirm failed for phone=%s amount=%s: %s", phone, amount, e)
        raise SMSKashError(str(e)) from e
    confirmed = str(data.get("code")) == "200"
    return {
        "confirmed": confirmed,
        "message": str(data.get("message") or ""),
        "trans_id": data.get("trans_id"),
        "sender": data.get("sender"),
        "time": data.get("time"),
    }

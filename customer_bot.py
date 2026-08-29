"""
Thin client for the xprostore.store reseller API
(https://xprostore.store/api-docs). Deliberately kept separate from the bot
code, same as db.py, so customer_bot.py / admin_bot.py / api_sync.py never
touch HTTP details directly.

Every call raises XProStoreError on anything that isn't a clean 2xx, with
the response body attached, so callers can decide what to tell the customer
and log a precise reason for the admin - never a silent failure.
"""
import logging
import requests

import config

log = logging.getLogger("xprostore_api")

TIMEOUT = 20  # seconds - a slow reseller API should never hang a bot handler


class XProStoreError(Exception):
    """Raised for any non-2xx response, a timeout, or a connection error.
    `status_code` is None for network-level failures (timeout/DNS/etc).
    `body` is the raw response text/JSON when available, useful to show the
    admin exactly why an order failed."""

    def __init__(self, message: str, status_code: int | None = None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _headers(extra: dict | None = None) -> dict:
    if not config.XPROSTORE_API_KEY:
        raise XProStoreError("XPROSTORE_API_KEY غير موجود في .env - لازم تحطه قبل ما تربط أي خدمة بالـ API.")
    headers = {"Authorization": f"Bearer {config.XPROSTORE_API_KEY}"}
    if extra:
        headers.update(extra)
    return headers


def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{config.XPROSTORE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        resp = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        log.error("xprostore API network error on %s %s: %s", method, path, e)
        raise XProStoreError(f"تعذر الوصول لـ xprostore API: {e}") from e

    if not resp.ok:
        body = _safe_body(resp)
        log.error("xprostore API error %s on %s %s: %s", resp.status_code, method, path, body)
        detail = _error_detail(body)
        message = f"xprostore API رجّع خطأ {resp.status_code}" + (f": {detail}" if detail else "")
        raise XProStoreError(message, status_code=resp.status_code, body=body)
    return _safe_body(resp)


def _error_detail(body) -> str:
    """Pulls a human-readable reason out of an error response body, e.g.
    {"message": "insufficient balance"} or {"error": "..."} - so the admin
    sees WHY it failed (out of xprostore balance? bad service id? duplicate
    order?), not just a bare status code."""
    if isinstance(body, dict):
        for key in ("message", "error", "error_message", "detail", "reason"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        # None of the expected keys matched - show the raw body instead of
        # nothing, so the actual reason is visible even if xprostore.store
        # names its error field something we didn't anticipate.
        try:
            import json
            return json.dumps(body, ensure_ascii=False)[:300]
        except Exception:
            return str(body)[:300]
    if isinstance(body, str):
        return body.strip()[:300]
    return ""


def _safe_body(resp):
    try:
        return resp.json()
    except ValueError:
        return resp.text


def get_wallet() -> dict:
    """GET /me/wallet - your resale balance at xprostore.store."""
    return _request("GET", "/me/wallet", headers=_headers())


def list_services() -> list:
    """GET /services - the full catalogue with live stock/price. The response
    shape isn't fully documented beyond the examples shared, so this
    normalizes a couple of likely shapes ({'data': [...]} or a bare list)
    rather than assuming one."""
    body = _request("GET", "/services", headers=_headers())
    if isinstance(body, dict):
        return body.get("data") or body.get("services") or []
    if isinstance(body, list):
        return body
    return []


def create_order(service_id: str, quantity: int, idempotency_key: str, **extra_fields) -> dict:
    """POST /orders - places a real order at xprostore.store. idempotency_key
    MUST be stable per local order (e.g. f"order-{order_id}") and MUST be
    reused as-is on any retry of the *same* local order, never regenerated -
    that's what stops a retried/duplicated call from being billed twice.

    Always sends `currency` = your configured wallet currency (EGP by
    default, see XPROSTORE_WALLET_CURRENCY) so a service listed in a
    different currency (e.g. USDT) still gets paid from your actual wallet
    with automatic conversion - same as buying manually through their own
    bot, which does this implicitly. Pass currency=... in extra_fields to
    override per-call if you ever need to."""
    payload = {"service_id": str(service_id), "quantity": quantity,
               "currency": config.XPROSTORE_WALLET_CURRENCY, **extra_fields}
    return _request(
        "POST", "/orders",
        headers=_headers({"Idempotency-Key": idempotency_key}),
        json=payload,
    )


def get_order(api_order_id: str) -> dict:
    """GET /orders/{id} - status of a previously placed order. NOTE: the exact
    path/field names for status-checking weren't in the examples shared (the
    full docs page needs a login), so this follows the same REST convention
    as the other endpoints. If your account's docs show a different path,
    update ORDER_STATUS_PATH below and nothing else needs to change."""
    return _request("GET", f"/orders/{api_order_id}", headers=_headers())


# Field names commonly used by reseller panels for "here's the actual
# account/code the customer paid for". "delivered_items" is xprostore.store's
# real field name (confirmed from a live order response); the rest are kept
# as fallbacks in case a different service type on their panel uses another
# shape.
_DELIVERY_FIELDS = ("delivered_items", "delivered_content", "account", "credentials", "content")

# Internal/business fields that must NEVER be shown to a customer even if
# they end up inside whatever dict we're scanning - your cost price, your
# xprostore wallet balance, and their internal order bookkeeping are none of
# the customer's business.
_NEVER_SHOW = {"price_amount", "price_currency_code", "balance_after", "have_discount",
               "order_id", "order_number", "status", "created_at", "quantity", "service_id"}


def extract_delivered_content(resp: dict):
    """Looks for the delivered account/code inside a create_order or
    get_order response. Returns a string to show the customer, or None if
    the order is genuinely still processing (nothing to deliver yet).

    Deliberately narrow: only ever returns the specific delivery field's
    content, never a generic dump of the surrounding order object - that
    object carries your cost price and wallet balance, which must never
    reach a customer."""
    if not isinstance(resp, dict):
        return None
    # Some responses nest everything under "data" or "result" - look one
    # level in for the same field names, but still only ever extract the
    # named delivery field, never the wrapper object itself.
    candidates = [resp]
    for wrapper_key in ("data", "result"):
        wrapped = resp.get(wrapper_key)
        if isinstance(wrapped, dict):
            candidates.append(wrapped)

    for obj in candidates:
        for key in _DELIVERY_FIELDS:
            if key in _NEVER_SHOW:
                continue
            val = obj.get(key)
            if val is None:
                continue
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list) and val:
                # e.g. delivered_items: ["email|password|..."] - one or more
                # accounts, shown one per line exactly as issued.
                items = [str(x).strip() for x in val if str(x).strip()]
                if items:
                    return "\n".join(items)
            if isinstance(val, dict):
                # e.g. {"email": "...", "password": "..."} - a single
                # structured account, not the order wrapper.
                parts = [f"{k}: {v}" for k, v in val.items()
                         if k not in _NEVER_SHOW and v not in (None, "")]
                if parts:
                    return "\n".join(parts)
    return None

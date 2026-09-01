"""
Runs alongside customer_bot.py and admin_bot.py (see run_both.py). Does two
things on a timer, both purely about xprostore.store-linked variants -
manual/unlinked services are never touched by this file:

1. Stock sync: mirrors each linked variant's stock to the API's live
   quantity. Prices are NEVER touched here - your own resale prices (which
   already include your margin) stay exactly as you set them in the admin
   bot; only the quantity number moves.

2. Order reconciliation: polls any order we dispatched to the API that
   isn't finished yet, and when the API reports it delivered/failed, marks
   it delivered in the local admin bot (or refunds + alerts if it failed
   asynchronously after we thought it went through).

3. Catalog watch: keeps a snapshot of xprostore.store's ENTIRE catalog
   (every service they offer, not just ones you've linked) and alerts the
   owner when something new appears, an existing one disappears/gets
   disabled, or its price/description changes.

Start this as its own process (run_both.py already does). It has no
Telegram handlers of its own - it just updates the shared database and
sends plain notifications through the admin/customer bots.
"""
import logging
import time

from telegram import Bot
from telegram.error import TelegramError

import config
import db
import xprostore_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api_sync")

# xprostore.store order-status values that mean "finished, nothing left to
# poll". Adjust here if your account's docs use different wording - nothing
# else in this file needs to change.
API_DONE_STATUSES = {"completed", "delivered", "done"}
API_FAILED_STATUSES = {"failed", "cancelled", "canceled", "rejected"}


def sync_stock(admin_bot: Bot):
    linked = db.list_api_linked_variants()
    if not linked:
        return
    try:
        services = xprostore_api.list_services()
    except xprostore_api.XProStoreError as e:
        log.error("stock sync: could not fetch service list: %s", e)
        return

    by_id = {}
    for s in services:
        sid = str(s.get("id") or s.get("service_id") or "")
        if sid:
            by_id[sid] = s

    for variant in linked:
        api_id = str(variant["api_service_id"])
        remote = by_id.get(api_id)
        if remote is None:
            log.warning("linked variant #%s -> api_service_id=%s not found in API service list anymore",
                        variant["id"], api_id)
            continue
        # Confirmed field names from a real xprostore.store service entry:
        # available_inventory_count (the actual count), track_inventory
        # (false = this service isn't stock-limited at all -> unlimited on
        # our side too), stock_status ("out_of_stock" overrides everything
        # else to 0, even if a stale count is still present).
        if remote.get("track_inventory") is False:
            remote_stock = -1  # unlimited, matches this bot's own convention
        elif str(remote.get("stock_status", "")).lower() == "out_of_stock":
            remote_stock = 0
        else:
            remote_stock = remote.get("available_inventory_count")
            if remote_stock is None:
                # Fallback guesses in case a different service type on your
                # panel doesn't use this exact field name.
                for key in ("stock", "quantity", "available_quantity", "stock_count", "available"):
                    if remote.get(key) is not None:
                        remote_stock = remote.get(key)
                        break
        if remote_stock is None:
            continue
        try:
            remote_stock = int(remote_stock)
        except (TypeError, ValueError):
            continue
        if remote_stock != variant["stock"]:
            db.set_variant_stock(variant["id"], remote_stock)
            log.info("stock sync: variant #%s (%s) %s -> %s",
                     variant["id"], variant["name_ar"], variant["stock"], remote_stock)


def _index_services(services: list) -> dict:
    by_id = {}
    for s in services:
        sid = str(s.get("id") or s.get("service_id") or "")
        if sid:
            by_id[sid] = s
    return by_id


def check_catalog_changes(admin_bot: Bot):
    """Diffs the full xprostore.store catalog against the last snapshot and
    alerts the owner about anything new, removed/disabled, or changed
    (description or price) - for every service they offer, not just the
    ones you've linked. Useful to catch a new service worth adding, or one
    you're relying on getting pulled or repriced."""
    try:
        services = xprostore_api.list_services()
    except xprostore_api.XProStoreError as e:
        log.error("catalog check: could not fetch service list: %s", e)
        return

    old = db.get_api_catalog_snapshot()
    first_run = not old

    current_entries = []
    current = {}
    for s in services:
        sid = str(s.get("id") or s.get("service_id") or "")
        if not sid:
            continue
        entry = {
            "api_service_id": sid,
            "name": s.get("name_ar") or s.get("name_en") or s.get("name") or sid,
            "description": s.get("description_ar") or s.get("description_en") or s.get("description") or "",
            "price_amount": str(s.get("price_amount") or ""),
            "price_currency": str(s.get("price_currency_code") or s.get("price_currency") or ""),
            "is_active": s.get("is_active", True),
        }
        current[sid] = entry
        current_entries.append(entry)

    if not first_run:
        new_ids = [sid for sid in current if sid not in old]
        removed_ids = [sid for sid in old if sid not in current]
        disabled_ids = [sid for sid in current
                         if sid in old and old[sid]["is_active"] and not current[sid]["is_active"]]
        reenabled_ids = [sid for sid in current
                          if sid in old and not old[sid]["is_active"] and current[sid]["is_active"]]
        changed_ids = [sid for sid in current
                       if sid in old and sid not in disabled_ids and sid not in reenabled_ids
                       and (old[sid]["description"] != current[sid]["description"]
                            or old[sid]["price_amount"] != current[sid]["price_amount"]
                            or old[sid]["price_currency"] != current[sid]["price_currency"])]

        lines = []
        _add_section(lines, "🆕 خدمات جديدة عند xprostore", new_ids, current)
        _add_section(lines, "🗑️ خدمات اتشالت من عندهم", removed_ids, old)
        _add_section(lines, "⛔ خدمات اتقفلت (is_active=false)", disabled_ids, current)
        _add_section(lines, "✅ خدمات رجعت شغالة تاني", reenabled_ids, current)
        _add_section(lines, "✏️ خدمات اتغير سعرها أو وصفها", changed_ids, current)

        if lines:
            linked_ids = {str(v["api_service_id"]).split(",")[0].strip() for v in db.list_api_linked_variants()}
            flagged = [sid for sid in (removed_ids + disabled_ids + changed_ids) if sid in linked_ids]
            if flagged:
                lines.append(f"\n⚠️ من ضمن دول، {len(flagged)} مربوطة ببوتك فعلًا - يفضل تراجعها.")
            _notify_owner(admin_bot, "📋 تغييرات في كتالوج xprostore.store:\n\n" + "\n".join(lines))

    db.save_api_catalog_snapshot(current_entries)


def _add_section(lines: list, title: str, ids: list, source: dict, limit: int = 10):
    if not ids:
        return
    lines.append(f"{title} ({len(ids)}):")
    for sid in ids[:limit]:
        entry = source.get(sid, {})
        name = entry.get("name", "؟")
        price = entry.get("price_amount", "")
        currency = entry.get("price_currency", "")
        price_part = f" - {price} {currency}" if price else ""
        lines.append(f"  • ID {sid}: {name}{price_part}")
    if len(ids) > limit:
        lines.append(f"  … و {len(ids) - limit} غيرهم")
    lines.append("")


def check_wallet(admin_bot: Bot, state: dict):
    try:
        wallet = xprostore_api.get_wallet()
    except xprostore_api.XProStoreError as e:
        log.error("wallet check failed: %s", e)
        return
    balance = wallet.get("balance", wallet.get("amount"))
    if balance is None:
        return
    try:
        balance = float(balance)
    except (TypeError, ValueError):
        return
    low_now = balance < config.XPROSTORE_LOW_BALANCE_ALERT
    if low_now and not state.get("low_balance_alerted"):
        _notify_owner(admin_bot,
                      f"⚠️ رصيدك في xprostore.store قرّب يخلص ({balance:.2f}). "
                      f"لحد ما تشحن، أي طلب API هيفشل ويترجع لفلوس العميل تلقائيًا.")
        state["low_balance_alerted"] = True
    elif not low_now:
        state["low_balance_alerted"] = False


def reconcile_pending_orders(admin_bot: Bot):
    for order in db.list_pending_api_orders():
        try:
            resp = xprostore_api.get_order(order["api_order_id"])
        except xprostore_api.XProStoreError as e:
            log.warning("could not poll order #%s (api_order_id=%s): %s", order["id"], order["api_order_id"], e)
            continue

        api_status = str(resp.get("status") or "").lower()
        if not api_status or api_status == order.get("api_status"):
            continue

        if api_status in API_DONE_STATUSES:
            db.set_order_status(order["id"], "delivered")
            db.set_order_api_info(order["id"], api_status=api_status)
            delivered = xprostore_api.extract_delivered_content(resp)
            if delivered:
                _notify_customer(order, f"✅ تم تسليم طلبك بنجاح!\n\n📦 التفاصيل:\n{delivered}")
            else:
                _notify_customer(order, "✅ تم تسليم طلبك بنجاح، تقدر تشوف التفاصيل من (طلباتي).")
        elif api_status in API_FAILED_STATUSES:
            refunded = db.refund_order(order["id"])
            if refunded:
                db.adjust_variant_stock(order["variant_id"], +1)
            db.set_order_api_info(order["id"], api_status=api_status, note=f"api reported: {api_status}")
            _notify_owner(admin_bot,
                          f"⚠️ الطلب #{order['id']} فشل عند xprostore.store بعد ما اترسل ({api_status}) "
                          f"وتم استرجاع فلوس العميل تلقائيًا.")
            _notify_customer(order, "⚠️ حصلت مشكلة في تنفيذ طلبك وتم إرجاع فلوسك لرصيدك، عايزين نعتذر على الإزعاج.")
        else:
            db.set_order_api_info(order["id"], api_status=api_status)


def _notify_owner(admin_bot: Bot, text: str):
    try:
        admin_bot.send_message(config.ADMIN_ID, text)
    except TelegramError as e:
        log.error("could not notify owner: %s", e)


def _notify_customer(order: dict, text: str):
    user = db.get_user_by_id(order["user_id"])
    if not user:
        return
    try:
        Bot(token=config.CUSTOMER_BOT_TOKEN).send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("could not notify customer for order #%s: %s", order["id"], e)


def main():
    if not config.XPROSTORE_API_KEY:
        log.warning("XPROSTORE_API_KEY not set - api_sync.py will idle (nothing is linked without it).")
    admin_bot = Bot(token=config.ADMIN_BOT_TOKEN)
    state = {"low_balance_alerted": False}
    while True:
        try:
            if config.XPROSTORE_API_KEY:
                sync_stock(admin_bot)
                check_wallet(admin_bot, state)
                reconcile_pending_orders(admin_bot)
                check_catalog_changes(admin_bot)
        except Exception:
            log.exception("api_sync loop iteration failed, will retry next cycle")
        time.sleep(config.XPROSTORE_SYNC_INTERVAL)


if __name__ == "__main__":
    main()

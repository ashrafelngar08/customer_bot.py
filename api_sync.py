"""
Runs alongside customer_bot.py and admin_bot.py (see run_both.py). Does
three things on a timer, all purely about xprostore.store - manual/unlinked
services are only touched by the catalog watch, never stock/order syncing:

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

Fully async: python-telegram-bot v20+ (this project uses >=22.8) makes
every Bot method a coroutine - calling e.g. admin_bot.send_message(...)
without awaiting it just builds a coroutine object and throws it away
silently (no exception, message never actually sent). That bug lived here
for a while: every notification this file sends - low balance alerts,
order reconciliation, catalog changes - was silently a no-op. Everything
below is awaited for real now.
"""
import asyncio
import json
import logging

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


async def sync_stock(admin_bot: Bot):
    linked = db.list_api_linked_variants()
    if not linked:
        return
    try:
        services = xprostore_api.list_services()
    except xprostore_api.XProStoreError as e:
        log.error("stock sync: could not fetch service list: %s", e)
        return

    by_id = _index_services(services)

    for variant in linked:
        api_id = str(variant["api_service_id"]).split(",")[0].strip()
        remote = by_id.get(api_id)
        if remote is None:
            log.warning("linked variant #%s -> api_service_id=%s not found in API service list anymore",
                        variant["id"], api_id)
            continue
        if remote.get("track_inventory") is False:
            remote_stock = -1
        elif str(remote.get("stock_status", "")).lower() == "out_of_stock":
            remote_stock = 0
        else:
            remote_stock = remote.get("available_inventory_count")
            if remote_stock is None:
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


def _fmt_val(v, limit=90) -> str:
    v = "" if v is None else " ".join(str(v).split())
    if not v:
        return "(فاضي)"
    return v if len(v) <= limit else v[:limit] + "…"


def _price_text(entry: dict) -> str:
    return f"{entry.get('price_amount') or ''} {entry.get('price_currency') or ''}".strip()


def diff_entry(old: dict, new: dict) -> list:
    """Every tracked field that differs between the last snapshot (old) and
    the live catalog (new), as ready-to-send lines saying WHAT changed and
    from/to what. Empty list = nothing changed."""
    out = []
    if (old.get("name") or "") != (new.get("name") or ""):
        out.append(f"✏️ الاسم: {_fmt_val(old.get('name'))} ← {_fmt_val(new.get('name'))}")
    if (old.get("description") or "") != (new.get("description") or ""):
        out.append(f"📝 الوصف اتغير:\n      قبل: {_fmt_val(old.get('description'))}"
                   f"\n      بعد: {_fmt_val(new.get('description'))}")
    if _price_text(old) != _price_text(new):
        out.append(f"💵 السعر: {_fmt_val(_price_text(old))} ← {_fmt_val(_price_text(new))}")
    old_active, new_active = bool(old.get("is_active", 1)), bool(new.get("is_active", 1))
    if old_active != new_active:
        out.append("⛔ اتقفلت (is_active=false)" if old_active else "✅ رجعت شغالة تاني")

    # category / custom_fields live in the JSON "extra". Snapshots saved
    # before this column existed have extra="" - treat those as "no baseline
    # yet" instead of reporting every service as changed.
    old_extra, new_extra = old.get("extra") or "", new.get("extra") or ""
    if old_extra and new_extra and old_extra != new_extra:
        try:
            o, n = json.loads(old_extra), json.loads(new_extra)
        except ValueError:
            o, n = {}, {}
        if o.get("category") != n.get("category"):
            out.append(f"🗂️ الصنف: {_fmt_val(o.get('category'))} ← {_fmt_val(n.get('category'))}")
        if o.get("custom_fields") != n.get("custom_fields"):
            out.append("🧾 الحقول المطلوبة من العميل اتغيرت (ممكن تأثر على الطلبات):"
                       f"\n      قبل: {_fmt_val(json.dumps(o.get('custom_fields'), ensure_ascii=False), 150)}"
                       f"\n      بعد: {_fmt_val(json.dumps(n.get('custom_fields'), ensure_ascii=False), 150)}")
    return out


async def check_catalog_changes(admin_bot: Bot, force_report: bool = False):
    try:
        services = xprostore_api.list_services()
    except xprostore_api.XProStoreError as e:
        log.error("catalog check: could not fetch service list: %s", e)
        if force_report:
            await _notify_owner(admin_bot, f"⚠️ فحص الكتالوج فشل: تعذر جلب قائمة الخدمات من xprostore ({e})")
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
            # The STABLE original/base price (e.g. a service's real USDT
            # price) rather than its live EGP-converted price_amount, which
            # drifts by a fraction on basically every check purely from the
            # FX rate moving - that was causing constant false "price
            # changed" alerts even when xprostore changed nothing. Falls
            # back to price_amount for services natively priced in EGP
            # (no conversion happening, so it's already stable).
            "price_amount": str(s.get("original_price_amount") or s.get("price_amount") or ""),
            "price_currency": str(s.get("original_price_currency_code")
                                   or s.get("price_currency_code") or s.get("price_currency") or ""),
            "is_active": s.get("is_active", True),
            "extra": json.dumps({"category": s.get("category"), "custom_fields": s.get("custom_fields")},
                                 sort_keys=True, ensure_ascii=False, default=str),
        }
        current[sid] = entry
        current_entries.append(entry)

    if first_run:
        if force_report:
            await _notify_owner(admin_bot,
                                f"📋 دي أول مرة يتعمل فيها فحص للكتالوج - تم حفظ صورة أولية لـ {len(current_entries)} "
                                f"خدمة. من الفحص الجاي هيبدأ يبلغك بأي فرق.")
        db.save_api_catalog_snapshot(current_entries)
        return

    new_ids = [sid for sid in current if sid not in old]
    removed_ids = [sid for sid in old if sid not in current]
    changed = {}
    for sid in current:
        if sid in old:
            d = diff_entry(old[sid], current[sid])
            if d:
                changed[sid] = d
    log.info("catalog check: %d services, %d new, %d removed, %d changed",
             len(current), len(new_ids), len(removed_ids), len(changed))

    lines = []
    _add_section(lines, "🆕 خدمات جديدة عند xprostore", new_ids, current)
    _add_section(lines, "🗑️ خدمات اتشالت من عندهم", removed_ids, old)

    linked_ids = {str(v["api_service_id"]).split(",")[0].strip() for v in db.list_api_linked_variants()}
    if changed:
        lines.append(f"✏️ خدمات حصل فيها تحديث ({len(changed)}):")
        for sid, d in changed.items():
            mark = " 🔗" if sid in linked_ids else ""
            lines.append(f"• ID {sid}: {_fmt_val(current[sid].get('name'), 60)}{mark}")
            lines.extend("   " + x for x in d)
            lines.append("")

    if lines:
        flagged = [sid for sid in (removed_ids + list(changed)) if sid in linked_ids]
        if flagged:
            lines.append(f"⚠️ من ضمن دول، {len(flagged)} مربوطة ببوتك فعلًا (🔗) - يفضل تراجعها.")
        sent = await _notify_owner(admin_bot, "📋 تغييرات في كتالوج xprostore.store:\n\n" + "\n".join(lines))
        if not sent:
            # Don't overwrite the snapshot - otherwise these changes would be
            # lost for good. The next cycle will find the same diff and retry.
            log.error("catalog check: could not deliver the change alert, keeping the old snapshot to retry")
            return
    elif force_report:
        await _notify_owner(admin_bot, f"📋 فحصت الكتالوج ({len(current_entries)} خدمة) - مفيش أي تغيير عن آخر فحص.")

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


async def check_wallet(admin_bot: Bot, state: dict):
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
        await _notify_owner(admin_bot,
                            f"⚠️ رصيدك في xprostore.store قرّب يخلص ({balance:.2f}). "
                            f"لحد ما تشحن، أي طلب API هيفشل ويترجع لفلوس العميل تلقائيًا.")
        state["low_balance_alerted"] = True
    elif not low_now:
        state["low_balance_alerted"] = False


async def reconcile_pending_orders(admin_bot: Bot):
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
                await _notify_customer(order, f"✅ تم تسليم طلبك بنجاح!\n\n📦 التفاصيل:\n{delivered}")
            else:
                await _notify_customer(order, "✅ تم تسليم طلبك بنجاح، تقدر تشوف التفاصيل من (طلباتي).")
        elif api_status in API_FAILED_STATUSES:
            refunded = db.refund_order(order["id"])
            if refunded:
                db.adjust_variant_stock(order["variant_id"], +1)
            db.set_order_api_info(order["id"], api_status=api_status, note=f"api reported: {api_status}")
            await _notify_owner(admin_bot,
                                f"⚠️ الطلب #{order['id']} فشل عند xprostore.store بعد ما اترسل ({api_status}) "
                                f"وتم استرجاع فلوس العميل تلقائيًا.")
            await _notify_customer(order, "⚠️ حصلت مشكلة في تنفيذ طلبك وتم إرجاع فلوسك لرصيدك، عايزين نعتذر على الإزعاج.")
        else:
            db.set_order_api_info(order["id"], api_status=api_status)


async def _notify_owner(admin_bot: Bot, text: str) -> bool:
    """Sends to the owner, splitting anything longer than Telegram's 4096
    limit into several messages. Returns True only if everything was sent."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for chunk in chunks:
        try:
            await admin_bot.send_message(config.ADMIN_ID, chunk.rstrip())
        except TelegramError as e:
            log.error("could not notify owner: %s", e)
            return False
    return True


async def _notify_customer(order: dict, text: str):
    user = db.get_user_by_id(order["user_id"])
    if not user:
        return
    try:
        async with Bot(token=config.CUSTOMER_BOT_TOKEN) as customer_bot:
            await customer_bot.send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("could not notify customer for order #%s: %s", order["id"], e)


async def _safe(name: str, coro):
    try:
        await coro
    except Exception:
        log.exception("api_sync step '%s' failed, continuing with the next one", name)


async def run_once(admin_bot: Bot, state: dict):
    """One full pass - stock sync, wallet check, order reconciliation,
    catalog watch. Each step is isolated: before, an unexpected error in an
    early step (e.g. stock sync) aborted the whole pass, so the catalog
    watch below it silently never ran."""
    await _safe("stock", sync_stock(admin_bot))
    await _safe("wallet", check_wallet(admin_bot, state))
    await _safe("orders", reconcile_pending_orders(admin_bot))
    await _safe("catalog", check_catalog_changes(admin_bot))


async def main():
    if not config.XPROSTORE_API_KEY:
        log.warning("XPROSTORE_API_KEY not set - api_sync.py will idle (nothing is linked without it).")
    async with Bot(token=config.ADMIN_BOT_TOKEN) as admin_bot:
        state = {"low_balance_alerted": False}
        while True:
            try:
                if config.XPROSTORE_API_KEY:
                    await run_once(admin_bot, state)
            except Exception:
                log.exception("api_sync loop iteration failed, will retry next cycle")
            await asyncio.sleep(config.XPROSTORE_SYNC_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())

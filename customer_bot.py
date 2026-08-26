import logging
import re
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

import config
import db
from i18n import t
from pricing import format_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("customer_bot")

STATUS_KEY = {
    "pending": "status_pending",
    "in_progress": "status_in_progress",
    "delivered": "status_delivered",
    "refunded": "status_refunded",
}


def main_menu_kb(lang):
    rows = [
        [InlineKeyboardButton(t("menu_services", lang), callback_data="menu:services")],
        [InlineKeyboardButton(t("menu_orders", lang), callback_data="menu:orders"),
         InlineKeyboardButton(t("menu_balance", lang), callback_data="menu:balance")],
        [InlineKeyboardButton(t("menu_topup", lang), callback_data="menu:topup"),
         InlineKeyboardButton(t("menu_currency", lang), callback_data="menu:currency")],
        [InlineKeyboardButton(t("menu_referral", lang), callback_data="menu:referral"),
         InlineKeyboardButton(t("menu_profile", lang), callback_data="menu:profile")],
        [InlineKeyboardButton(t("menu_lang", lang), callback_data="menu:lang"),
         InlineKeyboardButton(t("menu_support", lang), callback_data="menu:support")],
    ]
    return InlineKeyboardMarkup(rows)


def back_kb(lang, back_to="menu:main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t("back", lang), callback_data=back_to)]])


async def send_main_menu(target, lang, edit=False):
    text = t("welcome", lang)
    if edit:
        await target.edit_message_text(text, reply_markup=main_menu_kb(lang))
    else:
        await target.reply_text(text, reply_markup=main_menu_kb(lang))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    ref_code = None
    if context.args:
        ref_code = context.args[0]
    user = db.get_or_create_user(tg_user.id, tg_user.username, ref_code)
    if user["banned"]:
        await update.message.reply_text(t("banned", user["lang"]))
        return
    await send_main_menu(update.message, user["lang"])


async def guard_banned(update, user) -> bool:
    """Returns True (and notifies) if the user is banned."""
    if user and user["banned"]:
        lang = user["lang"]
        if update.callback_query:
            await update.callback_query.answer(t("banned", lang), show_alert=True)
        else:
            await update.message.reply_text(t("banned", lang))
        return True
    return False


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username)
    if await guard_banned(update, user):
        return
    lang = user["lang"]
    data = query.data
    await query.answer()

    if data == "menu:main":
        await send_main_menu(query, lang, edit=True)

    elif data == "menu:services":
        await show_categories(query, lang)

    elif data.startswith("cat:"):
        cat_id = int(data.split(":")[1])
        await show_services(query, lang, cat_id)

    elif data.startswith("svc:"):
        svc_id = int(data.split(":")[1])
        await show_service_detail(query, lang, user, svc_id)

    elif data.startswith("buy:"):
        svc_id = int(data.split(":")[1])
        await handle_buy(query, context, lang, user, svc_id)

    elif data == "menu:orders":
        await show_orders(query, lang, user)

    elif data == "menu:currency":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("egp", lang), callback_data="setcur:egp"),
             InlineKeyboardButton(t("usd", lang), callback_data="setcur:usd")],
            [InlineKeyboardButton(t("back", lang), callback_data="menu:main")],
        ])
        await query.edit_message_text(t("currency_title", lang), reply_markup=kb)

    elif data.startswith("setcur:"):
        cur = data.split(":")[1]
        db.set_currency(tg_user.id, cur)
        cur_label = t("egp", lang) if cur == "egp" else t("usd", lang)
        await query.edit_message_text(t("currency_saved", lang, cur=cur_label), reply_markup=back_kb(lang))

    elif data == "menu:topup":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Vodafone Cash 💵", callback_data="topup:vf")],
            [InlineKeyboardButton("Binance Pay 💰", callback_data="topup:bp")],
            [InlineKeyboardButton(t("back", lang), callback_data="menu:main")],
        ])
        await query.edit_message_text(t("topup_title", lang), reply_markup=kb)

    elif data.startswith("topup:"):
        method = data.split(":")[1]
        context.user_data["awaiting"] = ("topup", method)
        if method == "vf":
            text = t("vf_instructions", lang, number=config.VODAFONE_CASH_NUMBER)
        else:
            text = t("bp_instructions", lang, bid=config.BINANCE_PAY_ID)
        await query.edit_message_text(text, reply_markup=back_kb(lang), parse_mode=ParseMode.MARKDOWN)

    elif data == "menu:balance":
        bal = format_price(user["balance"], user["currency"])
        await query.edit_message_text(t("balance_title", lang, balance=bal), reply_markup=back_kb(lang))

    elif data == "menu:lang":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇪🇬 العربية", callback_data="setlang:ar"),
             InlineKeyboardButton("🇬🇧 English", callback_data="setlang:en")],
            [InlineKeyboardButton(t("back", lang), callback_data="menu:main")],
        ])
        await query.edit_message_text(t("lang_title", lang), reply_markup=kb)

    elif data.startswith("setlang:"):
        new_lang = data.split(":")[1]
        db.set_lang(tg_user.id, new_lang)
        await query.edit_message_text(t("lang_saved", new_lang), reply_markup=back_kb(new_lang))

    elif data == "menu:profile":
        await show_profile(query, lang, user)

    elif data == "menu:referral":
        await show_referral(query, context, lang, user)

    elif data == "menu:support":
        text = t("support_title", lang, user=config.SUPPORT_USERNAME, channel=config.SUPPORT_CHANNEL)
        await query.edit_message_text(text, reply_markup=back_kb(lang))


async def show_categories(query, lang):
    cats = db.list_categories()
    if not cats:
        await query.edit_message_text(t("no_categories", lang), reply_markup=back_kb(lang))
        return
    rows = []
    for c in cats:
        name = c["name_ar"] if lang == "ar" else c["name_en"]
        rows.append([InlineKeyboardButton(f"{c['emoji']} {name}".strip(), callback_data=f"cat:{c['id']}")])
    rows.append([InlineKeyboardButton(t("back", lang), callback_data="menu:main")])
    await query.edit_message_text(t("choose_category", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_services(query, lang, cat_id):
    services = db.list_services(cat_id)
    if not services:
        await query.edit_message_text(t("no_services", lang), reply_markup=back_kb(lang, "menu:services"))
        return
    user = db.get_user(query.from_user.id)
    rows = []
    for s in services:
        name = s["name_ar"] if lang == "ar" else s["name_en"]
        price = format_price(s["price_egp"], user["currency"])
        label = f"{name} — {price}"
        if s["stock"] == 0:
            label += " ❌"
        rows.append([InlineKeyboardButton(label, callback_data=f"svc:{s['id']}")])
    rows.append([InlineKeyboardButton(t("back", lang), callback_data="menu:services")])
    await query.edit_message_text(t("choose_service", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_service_detail(query, lang, user, svc_id):
    s = db.get_service(svc_id)
    if not s:
        await query.edit_message_text(t("no_services", lang), reply_markup=back_kb(lang, "menu:services"))
        return
    name = s["name_ar"] if lang == "ar" else s["name_en"]
    details = s["details_ar"] if lang == "ar" else s["details_en"]
    price = format_price(s["price_egp"], user["currency"])
    text = t("service_details", lang, name=name, details=details, price=price)
    rows = []
    if s["stock"] != 0:
        rows.append([InlineKeyboardButton(t("buy", lang), callback_data=f"buy:{s['id']}")])
    else:
        text += "\n\n" + t("out_of_stock", lang)
    rows.append([InlineKeyboardButton(t("back", lang), callback_data=f"cat:{s['category_id']}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def handle_buy(query, context, lang, user, svc_id):
    s = db.get_service(svc_id)
    if not s or s["hidden"]:
        await query.edit_message_text(t("no_services", lang), reply_markup=back_kb(lang, "menu:services"))
        return
    if s["stock"] == 0:
        await query.answer(t("out_of_stock", lang), show_alert=True)
        return
    if user["balance"] < s["price_egp"]:
        bal = format_price(user["balance"], user["currency"])
        price = format_price(s["price_egp"], user["currency"])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("menu_topup", lang), callback_data="menu:topup")],
            [InlineKeyboardButton(t("back", lang), callback_data=f"svc:{svc_id}")],
        ])
        await query.edit_message_text(
            t("insufficient_balance", lang, balance=bal, price=price), reply_markup=kb
        )
        return

    if s["requires_email"]:
        context.user_data["awaiting"] = ("email", svc_id)
        await query.edit_message_text(t("ask_email", lang), reply_markup=back_kb(lang, f"svc:{svc_id}"))
        return

    await finalize_order(query, context, lang, user, s, email=None)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def finalize_order(target, context, lang, user, service, email=None):
    """target can be a CallbackQuery or an Update.message-like object with reply_text."""
    db.adjust_balance(user["id"], -service["price_egp"])
    db.adjust_stock(service["id"], -1)
    order_id = db.create_order(user["id"], service["id"], service["name_ar"], service["price_egp"], email)

    # Pay referral bonus if this is the referred user's first order
    if db.maybe_pay_referral_bonus(user["id"], config.REFERRAL_BONUS_EGP):
        referrer = db.get_user_by_id(user["referred_by"]) if user["referred_by"] else None
        if referrer:
            bonus_msg = (
                f"🎉 حصلت على {config.REFERRAL_BONUS_EGP:.0f} جنيه مكافأة إحالة!"
                if referrer["lang"] == "ar" else
                f"🎉 You earned {config.REFERRAL_BONUS_EGP:.0f} EGP referral bonus!"
            )
            try:
                await context.bot.send_message(referrer["telegram_id"], bonus_msg)
            except TelegramError:
                pass

    name = service["name_ar"] if lang == "ar" else service["name_en"]
    fresh_user = db.get_user_by_id(user["id"])
    price = format_price(service["price_egp"], fresh_user["currency"])
    text = t("order_placed", lang, name=name, price=price, order_id=order_id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t("main_menu", lang), callback_data="menu:main")]])
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=kb)
    else:
        await target.reply_text(text, reply_markup=kb)

    # Notify admin bot so it can be delivered / actioned
    await notify_admin_new_order(context, fresh_user, service, order_id, email)


async def notify_admin_new_order(context, user, service, order_id, email):
    from telegram import Bot
    admin_bot = Bot(token=config.ADMIN_BOT_TOKEN)
    lines = [
        f"🆕 طلب جديد #{order_id}",
        f"👤 العميل: {user['telegram_id']} (@{user['username']})",
        f"📦 الخدمة: {service['name_ar']}",
        f"💵 السعر: {service['price_egp']:.2f} EGP",
    ]
    if email:
        lines.append(f"📧 الإيميل: {email}")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم التسليم", callback_data=f"admin_deliver:{order_id}"),
         InlineKeyboardButton("♻️ إلغاء واسترجاع", callback_data=f"admin_refund:{order_id}")],
    ])
    try:
        await admin_bot.send_message(config.ADMIN_ID, text, reply_markup=kb)
    except TelegramError as e:
        log.error("Failed to notify admin bot of new order: %s", e)


async def show_orders(query, lang, user):
    orders = db.list_orders_for_user(user["id"])
    if not orders:
        await query.edit_message_text(t("no_orders", lang), reply_markup=back_kb(lang))
        return
    lines = [t("orders_title", lang), ""]
    for o in orders:
        date = datetime.fromtimestamp(o["created_at"]).strftime("%Y-%m-%d")
        price = format_price(o["price_egp"], user["currency"])
        status = t(STATUS_KEY.get(o["status"], "status_pending"), lang)
        lines.append(t("order_line", lang, id=o["id"], name=o["service_name_ar"], date=date, price=price, status=status))
    await query.edit_message_text("\n".join(lines), reply_markup=back_kb(lang))


async def show_profile(query, lang, user):
    joined = datetime.fromtimestamp(user["join_date"]).strftime("%Y-%m-%d")
    currency_label = t("egp", lang) if user["currency"] == "egp" else t("usd", lang)
    text = t(
        "profile_title", lang,
        id=user["telegram_id"],
        phone=user["phone"] or t("not_set", lang),
        joined=joined,
        currency=currency_label,
        total_orders=user["total_orders"],
        completed=user["completed_orders"],
        spent=format_price(user["total_spent"], user["currency"]),
        balance=format_price(user["balance"], user["currency"]),
        ref_earnings=format_price(user["referral_earnings"], user["currency"]),
    )
    await query.edit_message_text(text, reply_markup=back_kb(lang))


async def show_referral(query, context, lang, user):
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={user['referral_code']}"
    text = t("referral_title", lang, bonus=f"{config.REFERRAL_BONUS_EGP:.0f} EGP", count=user["referral_count"], link=link)
    await query.edit_message_text(text, reply_markup=back_kb(lang), disable_web_page_preview=True)


TOPUP_RE = re.compile(r"^\s*(\S+)\s+([0-9]+(?:\.[0-9]+)?)\s*$")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username)
    if await guard_banned(update, user):
        return
    lang = user["lang"]
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        await send_main_menu(update.message, lang)
        return

    kind = awaiting[0]

    if kind == "email":
        svc_id = awaiting[1]
        email = update.message.text.strip()
        if not EMAIL_RE.match(email):
            await update.message.reply_text(t("invalid_email", lang))
            return
        service = db.get_service(svc_id)
        if not service:
            context.user_data.pop("awaiting", None)
            return
        context.user_data.pop("awaiting", None)
        await finalize_order(update.message, context, lang, user, service, email=email)

    elif kind == "topup":
        method = awaiting[1]
        m = TOPUP_RE.match(update.message.text)
        if not m:
            await update.message.reply_text(t("topup_bad_format", lang))
            return
        reference, amount_str = m.group(1), m.group(2)
        amount = float(amount_str)
        context.user_data.pop("awaiting", None)
        topup_id = db.create_topup(user["id"], method, amount, reference)
        await update.message.reply_text(t("topup_submitted", lang), reply_markup=back_kb(lang))
        await notify_admin_new_topup(context, user, method, amount, reference, topup_id)


async def notify_admin_new_topup(context, user, method, amount, reference, topup_id):
    from telegram import Bot
    admin_bot = Bot(token=config.ADMIN_BOT_TOKEN)
    method_label = "Vodafone Cash" if method == "vf" else "Binance Pay"
    ref_label = "رقم الهاتف" if method == "vf" else "Order ID"
    text = (
        f"💳 طلب شحن رصيد #{topup_id}\n"
        f"👤 العميل: {user['telegram_id']} (@{user['username']})\n"
        f"💰 الطريقة: {method_label}\n"
        f"{ref_label}: {reference}\n"
        f"💵 المبلغ: {amount:.2f} EGP"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data=f"admin_topup_ok:{topup_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"admin_topup_no:{topup_id}")],
    ])
    try:
        await admin_bot.send_message(config.ADMIN_ID, text, reply_markup=kb)
    except TelegramError as e:
        log.error("Failed to notify admin bot of new topup: %s", e)


def build_app():
    db.init_db()
    app = ApplicationBuilder().token(config.CUSTOMER_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


if __name__ == "__main__":
    application = build_app()
    log.info("Customer bot starting...")
    application.run_polling()

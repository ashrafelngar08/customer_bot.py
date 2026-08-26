import logging
import re
from datetime import datetime

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
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

# ---------------- Persistent bottom menu (Reply Keyboard) ----------------
# This is the always-visible menu under the message box, like a normal
# storefront bot - not attached to any single message, unlike inline
# keyboards. Each button just sends its label as plain text, which on_text
# below recognizes and routes to the right screen.

MENU_LABELS = {
    "services": {"ar": "🛒 الخدمات", "en": "🛒 Services"},
    "profile": {"ar": "⚙️ حسابي", "en": "⚙️ My Account"},
    "orders": {"ar": "📋 طلباتي", "en": "📋 My Orders"},
    "balance": {"ar": "💰 رصيدي", "en": "💰 My Balance"},
    "topup": {"ar": "💳 إضافة رصيد", "en": "💳 Add Balance"},
    "support": {"ar": "📞 الدعم", "en": "📞 Support"},
    "currency": {"ar": "💱 تحويل العملات", "en": "💱 Currency"},
    "lang": {"ar": "🌐 اللغة", "en": "🌐 Language"},
    "referral": {"ar": "💸 الربح عبر الدعوة", "en": "💸 Earn via Referral"},
}

# text -> action, built for both languages so a switch mid-session still matches
MENU_LOOKUP = {}
for _action, _labels in MENU_LABELS.items():
    for _lang_code, _label in _labels.items():
        MENU_LOOKUP[_label] = _action


def main_reply_keyboard(lang) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(MENU_LABELS["services"][lang]), KeyboardButton(MENU_LABELS["profile"][lang])],
        [KeyboardButton(MENU_LABELS["orders"][lang]), KeyboardButton(MENU_LABELS["balance"][lang])],
        [KeyboardButton(MENU_LABELS["topup"][lang]), KeyboardButton(MENU_LABELS["support"][lang])],
        [KeyboardButton(MENU_LABELS["currency"][lang]), KeyboardButton(MENU_LABELS["lang"][lang])],
        [KeyboardButton(MENU_LABELS["referral"][lang])],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def back_kb(lang, back_to=None):
    """Inline back button used only inside multi-step flows (categories ->
    services -> service detail, currency choice, language choice, topup
    method). The persistent bottom menu handles top-level navigation, so
    this never needs a 'back to main' target."""
    if back_to is None:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t("back", lang), callback_data=back_to)]])


async def respond(target, text, reply_markup=None, **kwargs):
    """target is either a CallbackQuery (edit in place) or an
    Update.message-like object (send a fresh message)."""
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=reply_markup, **kwargs)
    else:
        await target.reply_text(text, reply_markup=reply_markup, **kwargs)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    ref_code = None
    if context.args:
        ref_code = context.args[0]
    user = db.get_or_create_user(tg_user.id, tg_user.username, ref_code)
    if user["banned"]:
        await update.message.reply_text(t("banned", user["lang"]))
        return
    lang = user["lang"]
    await update.message.reply_text(t("welcome", lang), reply_markup=main_reply_keyboard(lang))


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


# ---------------- Inline callbacks: category/service browsing, currency,
# language, top-up method choice. All of these live inside a specific bot
# message, so they stay inline (that's what inline keyboards are for).

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username)
    if await guard_banned(update, user):
        return
    lang = user["lang"]
    data = query.data
    await query.answer()

    if data == "cats:root":
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

    elif data.startswith("setcur:"):
        cur = data.split(":")[1]
        db.set_currency(tg_user.id, cur)
        cur_label = t("egp", lang) if cur == "egp" else t("usd", lang)
        await query.edit_message_text(t("currency_saved", lang, cur=cur_label))

    elif data.startswith("topup:"):
        method = data.split(":")[1]
        context.user_data["awaiting"] = ("topup", method)
        if method == "vf":
            text = t("vf_instructions", lang, number=config.VODAFONE_CASH_NUMBER)
        else:
            text = t("bp_instructions", lang, bid=config.BINANCE_PAY_ID)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("setlang:"):
        new_lang = data.split(":")[1]
        db.set_lang(tg_user.id, new_lang)
        await query.edit_message_text(t("lang_saved", new_lang))
        # Bottom keyboard also needs to switch language - resend it
        await context.bot.send_message(
            tg_user.id, t("welcome", new_lang), reply_markup=main_reply_keyboard(new_lang)
        )


async def show_categories(target, lang):
    cats = db.list_categories()
    if not cats:
        await respond(target, t("no_categories", lang))
        return
    buttons = []
    for c in cats:
        name = c["name_ar"] if lang == "ar" else c["name_en"]
        buttons.append(InlineKeyboardButton(f"{c['emoji']} {name}".strip(), callback_data=f"cat:{c['id']}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await respond(target, t("choose_category", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_services(query, lang, cat_id):
    services = db.list_services(cat_id)
    if not services:
        await respond(query, t("no_services", lang), reply_markup=back_kb(lang, "cats:root"))
        return
    user = db.get_user(query.from_user.id)
    buttons = []
    for s in services:
        name = s["name_ar"] if lang == "ar" else s["name_en"]
        price = format_price(s["price_egp"], user["currency"])
        label = f"{name} — {price}"
        if s["stock"] == 0:
            label += " ❌"
        buttons.append(InlineKeyboardButton(label, callback_data=f"svc:{s['id']}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t("back", lang), callback_data="cats:root")])
    await query.edit_message_text(t("choose_service", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_service_detail(query, lang, user, svc_id):
    s = db.get_service(svc_id)
    if not s:
        await respond(query, t("no_services", lang), reply_markup=back_kb(lang, "cats:root"))
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
        await respond(query, t("no_services", lang), reply_markup=back_kb(lang, "cats:root"))
        return
    if s["stock"] == 0:
        await query.answer(t("out_of_stock", lang), show_alert=True)
        return
    if user["balance"] < s["price_egp"]:
        bal = format_price(user["balance"], user["currency"])
        price = format_price(s["price_egp"], user["currency"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t("back", lang), callback_data=f"svc:{svc_id}")]])
        await query.edit_message_text(
            t("insufficient_balance", lang, balance=bal, price=price) + "\n\n" + MENU_LABELS["topup"][lang],
            reply_markup=kb,
        )
        return

    if s["requires_email"]:
        context.user_data["awaiting"] = ("email", svc_id)
        await query.edit_message_text(t("ask_email", lang), reply_markup=back_kb(lang, f"svc:{svc_id}"))
        return

    await finalize_order(query, context, lang, user, s, email=None)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def finalize_order(target, context, lang, user, service, email=None):
    """target can be a CallbackQuery or an Update.message-like object."""
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
    await respond(target, text)

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


async def show_orders(target, lang, user):
    orders = db.list_orders_for_user(user["id"])
    if not orders:
        await respond(target, t("no_orders", lang))
        return
    lines = [t("orders_title", lang), ""]
    for o in orders:
        date = datetime.fromtimestamp(o["created_at"]).strftime("%Y-%m-%d")
        price = format_price(o["price_egp"], user["currency"])
        status = t(STATUS_KEY.get(o["status"], "status_pending"), lang)
        lines.append(t("order_line", lang, id=o["id"], name=o["service_name_ar"], date=date, price=price, status=status))
    await respond(target, "\n".join(lines))


async def show_profile(target, lang, user):
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
    await respond(target, text)


async def show_referral(target, context, lang, user):
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={user['referral_code']}"
    text = t("referral_title", lang, bonus=f"{config.REFERRAL_BONUS_EGP:.0f} EGP", count=user["referral_count"], link=link)
    await respond(target, text, disable_web_page_preview=True)


TOPUP_RE = re.compile(r"^\s*(\S+)\s+([0-9]+(?:\.[0-9]+)?)\s*$")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username)
    if await guard_banned(update, user):
        return
    lang = user["lang"]
    text_in = update.message.text.strip()
    awaiting = context.user_data.get("awaiting")

    # Step 1: are we mid-flow waiting for a specific free-text reply?
    if awaiting:
        kind = awaiting[0]

        if kind == "email":
            svc_id = awaiting[1]
            email = text_in
            if not EMAIL_RE.match(email):
                await update.message.reply_text(t("invalid_email", lang))
                return
            service = db.get_service(svc_id)
            if not service:
                context.user_data.pop("awaiting", None)
                return
            context.user_data.pop("awaiting", None)
            await finalize_order(update.message, context, lang, user, service, email=email)
            return

        elif kind == "topup":
            method = awaiting[1]
            m = TOPUP_RE.match(text_in)
            if not m:
                await update.message.reply_text(t("topup_bad_format", lang))
                return
            reference, amount_str = m.group(1), m.group(2)
            amount = float(amount_str)
            context.user_data.pop("awaiting", None)
            topup_id = db.create_topup(user["id"], method, amount, reference)
            await update.message.reply_text(t("topup_submitted", lang))
            await notify_admin_new_topup(context, user, method, amount, reference, topup_id)
            return

    # Step 2: is this one of the persistent bottom-menu buttons?
    action = MENU_LOOKUP.get(text_in)
    if action == "services":
        await show_categories(update.message, lang)
    elif action == "profile":
        await show_profile(update.message, lang, user)
    elif action == "orders":
        await show_orders(update.message, lang, user)
    elif action == "balance":
        bal = format_price(user["balance"], user["currency"])
        await update.message.reply_text(t("balance_title", lang, balance=bal))
    elif action == "topup":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Vodafone Cash 💵", callback_data="topup:vf")],
            [InlineKeyboardButton("Binance Pay 💰", callback_data="topup:bp")],
        ])
        await update.message.reply_text(t("topup_title", lang), reply_markup=kb)
    elif action == "support":
        text = t("support_title", lang, user=config.SUPPORT_USERNAME, channel=config.SUPPORT_CHANNEL)
        await update.message.reply_text(text)
    elif action == "currency":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("egp", lang), callback_data="setcur:egp"),
             InlineKeyboardButton(t("usd", lang), callback_data="setcur:usd")],
        ])
        await update.message.reply_text(t("currency_title", lang), reply_markup=kb)
    elif action == "lang":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇪🇬 العربية", callback_data="setlang:ar"),
             InlineKeyboardButton("🇬🇧 English", callback_data="setlang:en")],
        ])
        await update.message.reply_text(t("lang_title", lang), reply_markup=kb)
    elif action == "referral":
        await show_referral(update.message, context, lang, user)
    else:
        # Unrecognized free text - just resurface the bottom menu
        await update.message.reply_text(t("welcome", lang), reply_markup=main_reply_keyboard(lang))


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

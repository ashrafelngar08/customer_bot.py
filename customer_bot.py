import logging
import re
from datetime import datetime

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, CopyTextButton,
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

STATUS_EMOJI = {
    "pending": "⏳",
    "in_progress": "⏳",
    "delivered": "✅",
    "refunded": "🔁",
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
        await show_variants(query, lang, svc_id)

    elif data.startswith("var:"):
        variant_id = int(data.split(":")[1])
        await show_variant_detail(query, lang, user, variant_id)

    elif data.startswith("buy:"):
        variant_id = int(data.split(":")[1])
        await handle_buy(query, context, lang, user, variant_id)

    elif data.startswith("order:"):
        order_id = int(data.split(":")[1])
        await show_order_detail(query, lang, user, order_id)

    elif data.startswith("setcur:"):
        cur = data.split(":")[1]
        db.set_currency(tg_user.id, cur)
        cur_label = t("egp", lang) if cur == "egp" else t("usd", lang)
        await query.edit_message_text(t("currency_saved", lang, cur=cur_label))

    elif data.startswith("topup:"):
        method = data.split(":")[1]
        context.user_data["awaiting"] = ("topup_amount", method)
        if method == "vf":
            wallet = config.VODAFONE_CASH_NUMBER
            text = t("vf_instructions", lang, number=wallet)
        else:
            wallet = config.BINANCE_PAY_ID
            text = t("bp_instructions", lang, bid=wallet)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"📋 {wallet}", copy_text=CopyTextButton(text=wallet),
        )]])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        await context.bot.send_message(query.from_user.id, t("ask_topup_amount", lang))

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
        buttons.append(InlineKeyboardButton(
            f"{c['emoji']} {name}".strip(), callback_data=f"cat:{c['id']}",
            icon_custom_emoji_id=c.get("icon_custom_emoji_id") or None,
        ))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await respond(target, t("choose_category", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_services(query, lang, cat_id):
    services = db.list_services(cat_id)
    if not services:
        await respond(query, t("no_services", lang), reply_markup=back_kb(lang, "cats:root"))
        return
    buttons = []
    for s in services:
        name = s["name_ar"] if lang == "ar" else s["name_en"]
        label = name if s["stock"] != 0 else f"{name} ❌"
        buttons.append(InlineKeyboardButton(
            label, callback_data=f"svc:{s['id']}",
            icon_custom_emoji_id=s.get("icon_custom_emoji_id") or None,
        ))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t("back", lang), callback_data="cats:root")])
    await query.edit_message_text(t("choose_service", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_variants(query, lang, svc_id):
    """Shows the list of variants (durations/options) under a product -
    e.g. product 'جيميناي برو' -> variants '18 شهر' / '12 شهر'."""
    service = db.get_service(svc_id)
    variants = db.list_variants(svc_id)
    if not service or not variants:
        await respond(query, t("no_variants", lang), reply_markup=back_kb(lang, f"cat:{service['category_id']}" if service else "cats:root"))
        return
    buttons = []
    for v in variants:
        name = v["name_ar"] if lang == "ar" else v["name_en"]
        label = name if v["stock"] != 0 else f"{name} ❌"
        buttons.append(InlineKeyboardButton(label, callback_data=f"var:{v['id']}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t("back", lang), callback_data=f"cat:{service['category_id']}")])
    await query.edit_message_text(t("choose_variant", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_variant_detail(query, lang, user, variant_id):
    v = db.get_variant(variant_id)
    if not v:
        await respond(query, t("no_variants", lang), reply_markup=back_kb(lang, "cats:root"))
        return
    service = db.get_service(v["service_id"])
    product_name = service["name_ar"] if lang == "ar" else service["name_en"]
    variant_name = v["name_ar"] if lang == "ar" else v["name_en"]
    name = f"{product_name} — {variant_name}"
    details = v["details_ar"] if lang == "ar" else v["details_en"]
    price = format_price(v["price_egp"], user["currency"])
    text = t("service_details", lang, name=name, details=details, price=price)
    rows = []
    if v["stock"] != 0:
        rows.append([InlineKeyboardButton(t("buy", lang), callback_data=f"buy:{v['id']}")])
    else:
        text += "\n\n" + t("out_of_stock", lang)
    rows.append([InlineKeyboardButton(t("back", lang), callback_data=f"svc:{v['service_id']}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def handle_buy(query, context, lang, user, variant_id):
    v = db.get_variant(variant_id)
    if not v or v["hidden"]:
        await respond(query, t("no_variants", lang), reply_markup=back_kb(lang, "cats:root"))
        return
    if v["stock"] == 0:
        await query.answer(t("out_of_stock", lang), show_alert=True)
        return
    if user["balance"] < v["price_egp"]:
        bal = format_price(user["balance"], user["currency"])
        price = format_price(v["price_egp"], user["currency"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t("back", lang), callback_data=f"var:{variant_id}")]])
        await query.edit_message_text(
            t("insufficient_balance", lang, balance=bal, price=price) + "\n\n" + MENU_LABELS["topup"][lang],
            reply_markup=kb,
        )
        return

    if v["requires_email"]:
        context.user_data["awaiting"] = ("email", variant_id)
        await query.edit_message_text(t("ask_email", lang), reply_markup=back_kb(lang, f"var:{variant_id}"))
        return

    if v["requires_link"]:
        context.user_data["awaiting"] = ("link", variant_id)
        await query.edit_message_text(t("ask_link", lang), reply_markup=back_kb(lang, f"var:{variant_id}"))
        return

    await finalize_order(query, context, lang, user, v, email=None)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def finalize_order(target, context, lang, user, variant, email=None, link=None):
    """target can be a CallbackQuery or an Update.message-like object.
    variant is a row from the variants table (what's actually purchased)."""
    service = db.get_service(variant["service_id"])
    db.adjust_balance(user["id"], -variant["price_egp"])
    db.adjust_variant_stock(variant["id"], -1)
    full_name_ar = f"{service['name_ar']} — {variant['name_ar']}"
    order_id = db.create_order(user["id"], service["id"], variant["id"], full_name_ar, variant["price_egp"], email, link)

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

    full_name = f"{service['name_ar'] if lang == 'ar' else service['name_en']} — {variant['name_ar'] if lang == 'ar' else variant['name_en']}"
    fresh_user = db.get_user_by_id(user["id"])
    price = format_price(variant["price_egp"], fresh_user["currency"])
    text = t("order_placed", lang, name=full_name, price=price, order_id=order_id)
    await respond(target, text)

    # Notify admin bot so it can be delivered / actioned
    await notify_admin_new_order(context, fresh_user, service, variant, order_id, email, link)


async def notify_admin_new_order(context, user, service, variant, order_id, email, link=None):
    from telegram import Bot
    admin_bot = Bot(token=config.ADMIN_BOT_TOKEN)
    lines = [
        f"🆕 طلب جديد #{order_id}",
        f"👤 العميل: {user['telegram_id']} (@{user['username']})",
        f"📦 الخدمة: {service['name_ar']} — {variant['name_ar']}",
        f"💵 السعر: {variant['price_egp']:.2f} EGP",
    ]
    if email:
        lines.append(f"📧 الإيميل: {email}")
    if link:
        lines.append(f"🔗 الرابط: {link}")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم التسليم", callback_data=f"admin_deliver:{order_id}"),
         InlineKeyboardButton("♻️ إلغاء واسترجاع", callback_data=f"admin_refund:{order_id}")],
        [InlineKeyboardButton("✉️ راسل العميل", callback_data=f"admin_msg_order:{order_id}")],
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
    rows = []
    for o in orders:
        emoji = STATUS_EMOJI.get(o["status"], "⏳")
        label = t("order_button", lang, id=o["id"], name=o["service_name_ar"], status_emoji=emoji)
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([InlineKeyboardButton(label, callback_data=f"order:{o['id']}")])
    await respond(target, t("orders_title", lang), reply_markup=InlineKeyboardMarkup(rows))


async def show_order_detail(query, lang, user, order_id):
    """Sends the full detail of a single order as its own message (a reply
    into the chat), not an edit of the orders list - so tapping several
    order buttons leaves a trail of detail messages, same as the X Pro
    Store bot."""
    order = db.get_order(order_id)
    if not order or order["user_id"] != user["id"]:
        await query.answer(t("no_orders", lang), show_alert=True)
        return
    date = datetime.fromtimestamp(order["created_at"]).strftime("%Y-%m-%d")
    price = format_price(order["price_egp"], user["currency"])
    status = t(STATUS_KEY.get(order["status"], "status_pending"), lang)
    extra = ""
    if order.get("email"):
        extra += t("order_email_line", lang, email=order["email"])
    if order.get("link"):
        extra += t("order_link_line", lang, link=order["link"])
    text = t(
        "order_detail", lang,
        id=order["id"], name=order["service_name_ar"], price=price,
        date=date, status=status, extra=extra,
    )
    await query.message.reply_text(text)


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
            variant_id = awaiting[1]
            email = text_in
            if not EMAIL_RE.match(email):
                await update.message.reply_text(t("invalid_email", lang))
                return
            variant = db.get_variant(variant_id)
            if not variant:
                context.user_data.pop("awaiting", None)
                return
            context.user_data.pop("awaiting", None)
            await finalize_order(update.message, context, lang, user, variant, email=email)
            return

        elif kind == "link":
            variant_id = awaiting[1]
            link = text_in
            if len(link) < 4 or " " in link:
                await update.message.reply_text(t("invalid_link", lang))
                return
            variant = db.get_variant(variant_id)
            if not variant:
                context.user_data.pop("awaiting", None)
                return
            context.user_data.pop("awaiting", None)
            await finalize_order(update.message, context, lang, user, variant, link=link)
            return

        elif kind == "topup_amount":
            method = awaiting[1]
            try:
                amount = float(text_in.strip())
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(t("invalid_amount", lang))
                return
            context.user_data["awaiting"] = ("topup_reference", method, amount)
            ref_prompt = "ask_topup_reference_vf" if method == "vf" else "ask_topup_reference_bp"
            await update.message.reply_text(t(ref_prompt, lang))
            return

        elif kind == "topup_reference":
            method, amount = awaiting[1], awaiting[2]
            reference = text_in.strip()
            if not reference:
                await update.message.reply_text(t("topup_bad_format", lang))
                return
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

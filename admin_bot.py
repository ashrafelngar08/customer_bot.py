import logging
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)
from telegram.error import TelegramError
from deep_translator import GoogleTranslator

import config
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("admin_bot")


def only_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ADMIN_ID:
            if update.message:
                await update.message.reply_text("🚫 Not authorized.")
            return
        return await func(update, context)
    return wrapper


def translate_to_english(text: str) -> str:
    """Auto-translate Arabic text to English; falls back to the original text if translation fails."""
    try:
        return GoogleTranslator(source="ar", target="en").translate(text)
    except Exception as e:
        log.error("Translation failed, using original text as fallback: %s", e)
        return text


def main_kb():
    rows = [
        [InlineKeyboardButton("📨 رسالة فردية", callback_data="msg:one"),
         InlineKeyboardButton("📢 رسالة جماعية", callback_data="msg:all")],
        [InlineKeyboardButton("🗂️ إدارة الأصناف", callback_data="cats:root")],
        [InlineKeyboardButton("👥 إدارة العملاء", callback_data="users:list:0")],
        [InlineKeyboardButton("📦 كل الطلبات المعلقة", callback_data="orders:pending")],
    ]
    return InlineKeyboardMarkup(rows)


@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.init_db()
    await update.message.reply_text("🛠️ لوحة تحكم الإدارة", reply_markup=main_kb())


# ---------------- Broadcast / direct message ----------------

@only_admin
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "main":
        await query.edit_message_text("🛠️ لوحة تحكم الإدارة", reply_markup=main_kb())

    elif data == "msg:one":
        context.user_data["awaiting"] = ("msg_one_id",)
        await query.edit_message_text("أرسل Telegram ID الخاص بالعميل:")

    elif data == "msg:all":
        context.user_data["awaiting"] = ("msg_all",)
        await query.edit_message_text("أرسل نص الرسالة الجماعية اللي هتتبعت لكل العملاء:")

    elif data == "cats:root":
        await show_categories_admin(query)

    elif data.startswith("cats:view:"):
        cat_id = int(data.split(":")[2])
        await show_services_admin(query, cat_id)

    elif data == "cats:add":
        context.user_data["awaiting"] = ("add_category",)
        await query.edit_message_text("أرسل بيانات الصنف الجديد بالشكل:\nالاسم بالعربي | Name in English | إيموجي\nمثال: خدمات التصميم | Design Services | 🎨")

    elif data.startswith("cats:delete:"):
        cat_id = int(data.split(":")[2])
        db.delete_category(cat_id)
        await query.edit_message_text("✅ تم حذف الصنف وكل خدماته.")
        await show_categories_admin(query)

    elif data.startswith("cats:hide:"):
        cat_id = int(data.split(":")[2])
        cat = db.get_category(cat_id)
        db.set_category_hidden(cat_id, not cat["hidden"])
        await show_categories_admin(query)

    elif data.startswith("svc:add:"):
        cat_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("add_service_name", cat_id)
        await query.edit_message_text("أرسل اسم المنتج بالعربي:")

    elif data.startswith("svc:view:"):
        svc_id = int(data.split(":")[2])
        await show_product_admin(query, svc_id)

    elif data.startswith("svc:delete:"):
        svc_id = int(data.split(":")[2])
        s = db.get_service(svc_id)
        cat_id = s["category_id"]
        db.delete_service(svc_id)
        await show_services_admin(query, cat_id)

    elif data.startswith("svc:hide:"):
        svc_id = int(data.split(":")[2])
        s = db.get_service(svc_id)
        db.update_service_field(svc_id, "hidden", 0 if s["hidden"] else 1)
        await show_product_admin(query, svc_id)

    elif data.startswith("svc:editname:"):
        svc_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_service_name", svc_id)
        await query.edit_message_text("أرسل الاسم الجديد للمنتج بالعربي:")

    elif data.startswith("var:add:"):
        svc_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("add_variant_name", svc_id)
        await query.edit_message_text("أرسل اسم النسخة/المدة بالعربي (مثال: 18 شهر):")

    elif data.startswith("var:view:"):
        variant_id = int(data.split(":")[2])
        await show_variant_admin(query, variant_id)

    elif data.startswith("var:delete:"):
        variant_id = int(data.split(":")[2])
        v = db.get_variant(variant_id)
        svc_id = v["service_id"]
        db.delete_variant(variant_id)
        await show_product_admin(query, svc_id)

    elif data.startswith("var:hide:"):
        variant_id = int(data.split(":")[2])
        v = db.get_variant(variant_id)
        db.update_variant_field(variant_id, "hidden", 0 if v["hidden"] else 1)
        await show_variant_admin(query, variant_id)

    elif data.startswith("var:editprice:"):
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_variant_price", variant_id)
        await query.edit_message_text("أرسل السعر الجديد بالجنيه المصري:")

    elif data.startswith("var:editname:"):
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_variant_name", variant_id)
        await query.edit_message_text("أرسل الاسم الجديد للنسخة/المدة بالعربي:")

    elif data.startswith("var:editdetails:"):
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_variant_details", variant_id)
        await query.edit_message_text("أرسل الوصف/التفاصيل الجديدة بالعربي:")

    elif data.startswith("var:stock:"):
        _, _, variant_id, delta = data.split(":")
        db.adjust_variant_stock(int(variant_id), int(delta))
        await show_variant_admin(query, int(variant_id))

    elif data.startswith("users:list:"):
        offset = int(data.split(":")[2])
        await show_users_list(query, offset)

    elif data.startswith("users:view:"):
        user_id = int(data.split(":")[2])
        await show_user_admin(query, user_id)

    elif data.startswith("users:ban:"):
        user_id = int(data.split(":")[2])
        u = db.get_user_by_id(user_id)
        db.set_ban(u["telegram_id"], not u["banned"])
        await show_user_admin(query, user_id)

    elif data.startswith("users:addbal:"):
        user_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("add_balance", user_id)
        await query.edit_message_text("أرسل المبلغ المراد إضافته (استخدم إشارة سالبة للخصم، مثال: -50):")

    elif data == "orders:pending":
        await show_pending_orders(query)

    elif data.startswith("admin_deliver:"):
        order_id = int(data.split(":")[1])
        db.set_order_status(order_id, "delivered")
        await deliver_notify_customer(context, order_id)
        await query.edit_message_text(f"✅ تم تعليم الطلب #{order_id} كـ (تم التسليم) وتم إشعار العميل.")

    elif data.startswith("admin_refund:"):
        order_id = int(data.split(":")[1])
        order = db.refund_order(order_id)
        if order:
            await refund_notify_customer(context, order)
            await query.edit_message_text(f"♻️ تم إلغاء الطلب #{order_id} واسترجاع المبلغ للعميل.")
        else:
            await query.edit_message_text("⚠️ الطلب غير موجود أو تم استرجاعه بالفعل.")

    elif data.startswith("admin_topup_ok:"):
        topup_id = int(data.split(":")[1])
        t = db.resolve_topup(topup_id, approve=True)
        if t:
            await topup_notify_customer(context, t, approved=True)
            await query.edit_message_text(f"✅ تم تأكيد شحن #{topup_id} وإضافة الرصيد للعميل.")
        else:
            await query.edit_message_text("⚠️ الطلب غير موجود أو تم التعامل معه من قبل.")

    elif data.startswith("admin_topup_no:"):
        topup_id = int(data.split(":")[1])
        t = db.resolve_topup(topup_id, approve=False)
        if t:
            await topup_notify_customer(context, t, approved=False)
            await query.edit_message_text(f"❌ تم رفض طلب الشحن #{topup_id}.")
        else:
            await query.edit_message_text("⚠️ الطلب غير موجود أو تم التعامل معه من قبل.")


async def show_categories_admin(query):
    cats = db.list_categories(include_hidden=True)
    rows = []
    for c in cats:
        label = f"{c['emoji']} {c['name_ar']}" + (" (مخفي)" if c["hidden"] else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"cats:view:{c['id']}")])
    rows.append([InlineKeyboardButton("➕ إضافة صنف جديد", callback_data="cats:add")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="main")])
    await query.edit_message_text("🗂️ الأصناف الحالية:", reply_markup=InlineKeyboardMarkup(rows))


async def show_services_admin(query, cat_id):
    cat = db.get_category(cat_id)
    services = db.list_services(cat_id, include_hidden=True)
    rows = []
    for s in services:
        label = s["name_ar"]
        if s["hidden"]:
            label += " (مخفي)"
        rows.append([InlineKeyboardButton(label, callback_data=f"svc:view:{s['id']}")])
    rows.append([InlineKeyboardButton("➕ إضافة منتج", callback_data=f"svc:add:{cat_id}")])
    rows.append([
        InlineKeyboardButton("🗑️ حذف الصنف", callback_data=f"cats:delete:{cat_id}"),
        InlineKeyboardButton("👁️ إخفاء/إظهار الصنف", callback_data=f"cats:hide:{cat_id}"),
    ])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="cats:root")])
    await query.edit_message_text(f"منتجات صنف: {cat['name_ar']}", reply_markup=InlineKeyboardMarkup(rows))


async def show_product_admin(query, service_id):
    """Product-level admin screen: lists this product's variants (durations/
    options) - price, stock, and buying all live on the variant now."""
    s = db.get_service(service_id)
    variants = db.list_variants(service_id, include_hidden=True)
    rows = []
    for v in variants:
        label = f"{v['name_ar']} — {v['price_egp']:.0f} EGP"
        if v["hidden"]:
            label += " (مخفي)"
        rows.append([InlineKeyboardButton(label, callback_data=f"var:view:{v['id']}")])
    rows.append([InlineKeyboardButton("➕ إضافة نسخة/مدة", callback_data=f"var:add:{service_id}")])
    rows.append([InlineKeyboardButton("✏️ تعديل اسم المنتج", callback_data=f"svc:editname:{service_id}")])
    rows.append([
        InlineKeyboardButton("🗑️ حذف المنتج", callback_data=f"svc:delete:{service_id}"),
        InlineKeyboardButton("👁️ إخفاء/إظهار المنتج", callback_data=f"svc:hide:{service_id}"),
    ])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"cats:view:{s['category_id']}")])
    header = f"📦 {s['name_ar']}" + (" (مخفي)" if s["hidden"] else "")
    await query.edit_message_text(header + "\n\nنسخ/مدد هذا المنتج:", reply_markup=InlineKeyboardMarkup(rows))


async def show_variant_admin(query, variant_id):
    v = db.get_variant(variant_id)
    s = db.get_service(v["service_id"])
    stock_label = "غير محدود" if v["stock"] < 0 else str(v["stock"])
    text = (
        f"📦 {s['name_ar']} — {v['name_ar']}\n{v['details_ar']}\n\n"
        f"💵 السعر: {v['price_egp']:.2f} EGP\n"
        f"📊 الكمية: {stock_label}\n"
        f"📧 يتطلب إيميل: {'نعم' if v['requires_email'] else 'لا'}\n"
        f"👁️ الحالة: {'مخفي' if v['hidden'] else 'ظاهر'}"
    )
    rows = [
        [InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"var:editname:{variant_id}"),
         InlineKeyboardButton("✏️ تعديل الوصف", callback_data=f"var:editdetails:{variant_id}")],
        [InlineKeyboardButton("✏️ تعديل السعر", callback_data=f"var:editprice:{variant_id}")],
        [InlineKeyboardButton("➕ زيادة مخزون", callback_data=f"var:stock:{variant_id}:5"),
         InlineKeyboardButton("➖ إنقاص مخزون", callback_data=f"var:stock:{variant_id}:-5")],
        [InlineKeyboardButton("👁️ إخفاء/إظهار", callback_data=f"var:hide:{variant_id}"),
         InlineKeyboardButton("🗑️ حذف", callback_data=f"var:delete:{variant_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"svc:view:{v['service_id']}")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_users_list(query, offset):
    users = db.list_users(limit=10, offset=offset)
    rows = []
    for u in users:
        label = f"{u['telegram_id']} (@{u['username']}) — {u['balance']:.0f} EGP"
        if u["banned"]:
            label += " 🚫"
        rows.append([InlineKeyboardButton(label, callback_data=f"users:view:{u['id']}")])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"users:list:{max(offset - 10, 0)}"))
    if len(users) == 10:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"users:list:{offset + 10}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="main")])
    await query.edit_message_text("👥 قائمة العملاء:", reply_markup=InlineKeyboardMarkup(rows))


async def show_user_admin(query, user_id):
    u = db.get_user_by_id(user_id)
    joined = datetime.fromtimestamp(u["join_date"]).strftime("%Y-%m-%d")
    text = (
        f"👤 العميل {u['telegram_id']} (@{u['username']})\n"
        f"📅 انضم: {joined}\n"
        f"📱 الهاتف: {u['phone'] or '-'}\n"
        f"💰 الرصيد: {u['balance']:.2f} EGP\n"
        f"🛍️ الطلبات: {u['total_orders']} (مكتمل: {u['completed_orders']})\n"
        f"💵 إجمالي الإنفاق: {u['total_spent']:.2f} EGP\n"
        f"👥 الإحالات: {u['referral_count']}\n"
        f"🚫 محظور: {'نعم' if u['banned'] else 'لا'}"
    )
    rows = [
        [InlineKeyboardButton("🚫 حظر/فك حظر", callback_data=f"users:ban:{user_id}"),
         InlineKeyboardButton("💰 تعديل الرصيد", callback_data=f"users:addbal:{user_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="users:list:0")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_pending_orders(query):
    with db.get_conn() as conn:
        rows_ = conn.execute(
            "SELECT * FROM orders WHERE status='in_progress' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows_:
        await query.edit_message_text("لا توجد طلبات قيد التنفيذ حاليًا.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 رجوع", callback_data="main")]]))
        return
    lines = ["📦 الطلبات قيد التنفيذ:\n"]
    for o in rows_:
        lines.append(f"#{o['id']} — {o['service_name_ar']} — {o['price_egp']:.0f} EGP")
    lines.append("\nاستخدم الأزرار تحت رسالة الطلب نفسها للتسليم أو الاسترجاع.")
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 رجوع", callback_data="main")]]))


# ---------------- Notifications back to the customer bot ----------------

def customer_bot() -> Bot:
    return Bot(token=config.CUSTOMER_BOT_TOKEN)


async def deliver_notify_customer(context, order_id):
    order = db.get_order(order_id)
    user = db.get_user_by_id(order["user_id"])
    lang = user["lang"]
    text = (f"✅ تم تسليم طلبك #{order_id} ({order['service_name_ar']}) بنجاح!" if lang == "ar"
            else f"✅ Your order #{order_id} ({order['service_name_ar']}) has been delivered!")
    try:
        await customer_bot().send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("Failed notifying customer of delivery: %s", e)


async def refund_notify_customer(context, order):
    user = db.get_user_by_id(order["user_id"])
    lang = user["lang"]
    text = (f"♻️ تم إلغاء طلبك #{order['id']} ({order['service_name_ar']}) وإعادة {order['price_egp']:.2f} جنيه لرصيدك."
            if lang == "ar" else
            f"♻️ Your order #{order['id']} ({order['service_name_ar']}) was cancelled and {order['price_egp']:.2f} EGP was refunded to your balance.")
    try:
        await customer_bot().send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("Failed notifying customer of refund: %s", e)


async def topup_notify_customer(context, topup, approved: bool):
    user = db.get_user_by_id(topup["user_id"])
    lang = user["lang"]
    if approved:
        text = (f"✅ تم تأكيد شحن رصيدك بمبلغ {topup['amount']:.2f} جنيه. رصيدك الحالي: {user['balance']:.2f} جنيه"
                if lang == "ar" else
                f"✅ Your top-up of {topup['amount']:.2f} EGP was approved. Balance: {user['balance']:.2f} EGP")
    else:
        text = ("❌ للأسف تم رفض طلب شحن الرصيد. تواصل مع الدعم لو فيه استفسار." if lang == "ar"
                else "❌ Unfortunately your top-up request was rejected. Contact support if needed.")
    try:
        await customer_bot().send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("Failed notifying customer of topup result: %s", e)


# ---------------- Free-text admin inputs ----------------

@only_admin
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        await update.message.reply_text("استخدم /start لفتح لوحة التحكم.")
        return
    kind = awaiting[0]
    text = update.message.text.strip()

    if kind == "msg_one_id":
        context.user_data["awaiting"] = ("msg_one_text", int(text))
        await update.message.reply_text("أرسل نص الرسالة:")

    elif kind == "msg_one_text":
        target_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        try:
            await customer_bot().send_message(target_id, text)
            await update.message.reply_text("✅ تم الإرسال.")
        except TelegramError as e:
            await update.message.reply_text(f"⚠️ فشل الإرسال: {e}")

    elif kind == "msg_all":
        context.user_data.pop("awaiting", None)
        ids = db.all_active_telegram_ids()
        sent, failed = 0, 0
        bot = customer_bot()
        for tid in ids:
            try:
                await bot.send_message(tid, text)
                sent += 1
            except TelegramError:
                failed += 1
        await update.message.reply_text(f"✅ تم الإرسال لـ {sent} عميل. فشل: {failed}")

    elif kind == "add_category":
        context.user_data.pop("awaiting", None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("⚠️ صيغة غلط، حاول تاني بالشكل: عربي | English | إيموجي")
            return
        name_ar, name_en = parts[0], parts[1]
        emoji = parts[2] if len(parts) > 2 else ""
        db.add_category(name_ar, name_en, emoji)
        await update.message.reply_text(f"✅ تم إضافة الصنف: {name_ar}")

    elif kind == "add_service_name":
        cat_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        name_ar = text
        name_en = translate_to_english(name_ar)
        db.add_service(cat_id, name_ar, name_en)
        await update.message.reply_text(
            f"✅ تم إضافة المنتج: {name_ar}\nدلوقتي ضيفله نسخة/مدة واحدة على الأقل من زر ➕ إضافة نسخة/مدة عشان يبقى قابل للشراء."
        )

    elif kind == "add_variant_name":
        svc_id = awaiting[1]
        name_ar = text
        context.user_data["awaiting"] = ("add_variant_details", svc_id, name_ar)
        await update.message.reply_text("أرسل تفاصيل هذه النسخة بالعربي:")

    elif kind == "add_variant_details":
        svc_id, name_ar = awaiting[1], awaiting[2]
        details_ar = text
        context.user_data["awaiting"] = ("add_variant_price", svc_id, name_ar, details_ar)
        await update.message.reply_text("أرسل السعر بالجنيه:")

    elif kind == "add_variant_price":
        svc_id, name_ar, details_ar = awaiting[1], awaiting[2], awaiting[3]
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ من فضلك أرسل رقم صحيح للسعر.")
            return
        context.user_data["awaiting"] = ("add_variant_stock", svc_id, name_ar, details_ar, price)
        await update.message.reply_text("أرسل الكمية (اكتب -1 لغير محدود):")

    elif kind == "add_variant_stock":
        svc_id, name_ar, details_ar, price = awaiting[1], awaiting[2], awaiting[3], awaiting[4]
        try:
            stock = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ من فضلك أرسل رقم صحيح للكمية.")
            return
        context.user_data["awaiting"] = ("add_variant_email", svc_id, name_ar, details_ar, price, stock)
        await update.message.reply_text("يتطلب إيميل؟ (نعم/لا)")

    elif kind == "add_variant_email":
        svc_id, name_ar, details_ar, price, stock = (
            awaiting[1], awaiting[2], awaiting[3], awaiting[4], awaiting[5]
        )
        context.user_data.pop("awaiting", None)
        requires_email = 1 if text.strip() in ("نعم", "yes", "Yes", "y", "Y") else 0
        name_en = translate_to_english(name_ar)
        details_en = translate_to_english(details_ar)
        db.add_variant(svc_id, name_ar, name_en, details_ar, details_en, price, stock, requires_email)
        await update.message.reply_text(f"✅ تم إضافة النسخة: {name_ar}")

    elif kind == "edit_variant_price":
        variant_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ من فضلك أرسل رقم صحيح.")
            return
        db.update_variant_field(variant_id, "price_egp", price)
        await update.message.reply_text(f"✅ تم تحديث السعر إلى {price:.2f} EGP")

    elif kind == "edit_service_name":
        svc_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        name_ar = text
        name_en = translate_to_english(name_ar)
        db.update_service_field(svc_id, "name_ar", name_ar)
        db.update_service_field(svc_id, "name_en", name_en)
        await update.message.reply_text(f"✅ تم تحديث اسم المنتج إلى: {name_ar}")

    elif kind == "edit_variant_name":
        variant_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        name_ar = text
        name_en = translate_to_english(name_ar)
        db.update_variant_field(variant_id, "name_ar", name_ar)
        db.update_variant_field(variant_id, "name_en", name_en)
        await update.message.reply_text(f"✅ تم تحديث اسم النسخة إلى: {name_ar}")

    elif kind == "edit_variant_details":
        variant_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        details_ar = text
        details_en = translate_to_english(details_ar)
        db.update_variant_field(variant_id, "details_ar", details_ar)
        db.update_variant_field(variant_id, "details_en", details_en)
        await update.message.reply_text("✅ تم تحديث وصف النسخة.")

    elif kind == "add_balance":
        user_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ من فضلك أرسل رقم صحيح.")
            return
        db.adjust_balance(user_id, amount)
        u = db.get_user_by_id(user_id)
        await update.message.reply_text(f"✅ تم تحديث الرصيد. الرصيد الحالي: {u['balance']:.2f} EGP")
        try:
            note = (f"💰 تم تحديث رصيدك بمبلغ {amount:+.2f} جنيه من الإدارة. رصيدك الحالي: {u['balance']:.2f} جنيه"
                    if u["lang"] == "ar" else
                    f"💰 Your balance was adjusted by {amount:+.2f} EGP by the admin. Balance: {u['balance']:.2f} EGP")
            await customer_bot().send_message(u["telegram_id"], note)
        except TelegramError:
            pass


def build_app():
    db.init_db()
    app = ApplicationBuilder().token(config.ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


if __name__ == "__main__":
    application = build_app()
    log.info("Admin bot starting...")
    application.run_polling()


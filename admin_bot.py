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
import xprostore_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("admin_bot")


# ---------------- Roles & permissions ----------------
# config.ADMIN_ID (the owner) always has every permission and is never
# stored in the DB. Anyone else must be added via db.add_admin() and gets
# only the callback/text-input prefixes listed for their role below.
ROLE_OWNER = "owner"
ROLE_SERVICES = "services"

# Prefixes of callback_data (on_callback) and "awaiting" kinds (on_text)
# each non-owner role is allowed to touch. "main" is always allowed so
# everyone can navigate back to their own menu.
ROLE_PERMISSIONS = {
    ROLE_SERVICES: {
        "callback_prefixes": ("main", "cats:", "svc:", "var:"),
        "awaiting_kinds": {
            "add_category", "add_category_icon", "edit_category_name",
            "add_service_name", "add_service_icon",
            "edit_service_name", "edit_category_icon", "edit_service_icon",
            "add_variant_name", "add_variant_details", "add_variant_price",
            "add_variant_stock", "add_variant_requires", "edit_variant_requires", "edit_variant_price",
            "edit_variant_name", "edit_variant_details",
        },
    },
}


def get_role(telegram_id: int) -> str | None:
    """Returns the caller's role, or None if they're not an admin at all."""
    if telegram_id == config.ADMIN_ID:
        return ROLE_OWNER
    rec = db.get_admin(telegram_id)
    return rec["role"] if rec else None


def only_admin(func):
    """Allows anyone with a role (owner or any sub-admin role) through.
    Fine-grained restriction of *what* a sub-admin can do happens in
    on_callback/on_text via require_permission below."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        role = get_role(update.effective_user.id)
        if role is None:
            if update.message:
                await update.message.reply_text("🚫 Not authorized.")
            return
        context.user_data["role"] = role
        return await func(update, context)
    return wrapper


def require_permission(role: str, kind: str, value: str) -> bool:
    """kind is 'callback' or 'awaiting'. Owner can always do everything."""
    if role == ROLE_OWNER:
        return True
    perms = ROLE_PERMISSIONS.get(role)
    if not perms:
        return False
    if kind == "callback":
        return value == "main" or any(value.startswith(p) for p in perms["callback_prefixes"] if p != "main")
    return value in perms["awaiting_kinds"]


ICON_PROMPT = (
    "🖼️ ابعت الإيموجي المميز (Premium/Animated) اللي عايزه يظهر كأيقونة قبل اسم الزرار ده، "
    "لوحده في رسالة (اضغطه من لوحة الإيموجي المميزة، أو حوّل - Forward - رسالة فيها).\n"
    "أو ابعت \"تخطي\" لو مش عايز أيقونة / عايز تشيل الموجودة.\n\n"
    "⚠️ مهم: الأيقونة دي مش هتظهر إلا لو حساب صاحب البوت (ADMIN_ID) عنده Telegram Premium فعّال، "
    "أو البوت مشتري يوزر من Fragment."
)


def extract_custom_emoji_id(message) -> str | None:
    """Pulls the first custom_emoji entity's ID out of a message, whether it
    was typed/pasted directly or arrived via Forward (both carry the entity
    the same way in the Bot API)."""
    for e in (message.entities or []):
        if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None):
            return e.custom_emoji_id
    return None


REQUIRES_PROMPT = (
    "المنتج/النسخة دي محتاجة حاجة من العميل بعد الدفع عشان تقدر تسلّمها؟\n"
    "1️⃣ لا — تسليم عادي\n"
    "2️⃣ إيميل — (زي اشتراكات نتفلكس/جيميناي)\n"
    "3️⃣ رابط — رابط حساب العميل (زي نقل ملكية صفحة فيسبوك)\n\n"
    "ابعت رقم أو كلمة (لا / إيميل / رابط)."
)


def parse_requires_choice(text: str):
    """Returns (requires_email, requires_link) from a free-text admin reply."""
    t_norm = text.strip().lower()
    if t_norm in ("2", "إيميل", "ايميل", "email", "yes", "نعم", "y"):
        return 1, 0
    if t_norm in ("3", "رابط", "لينك", "link"):
        return 0, 1
    return 0, 0


def translate_to_english(text: str) -> str:
    """Auto-translate Arabic text to English; falls back to the original text if translation fails."""
    try:
        return GoogleTranslator(source="ar", target="en").translate(text)
    except Exception as e:
        log.error("Translation failed, using original text as fallback: %s", e)
        return text


def main_kb(role: str = ROLE_OWNER):
    if role == ROLE_SERVICES:
        # Sub-admins with the "services" role only get the catalog screen -
        # no messaging, no customer data, no orders/top-ups, no admin mgmt.
        return InlineKeyboardMarkup([[InlineKeyboardButton("🗂️ إدارة الأصناف", callback_data="cats:root")]])

    rows = [
        [InlineKeyboardButton("📨 رسالة فردية", callback_data="msg:one"),
         InlineKeyboardButton("📢 رسالة جماعية", callback_data="msg:all")],
        [InlineKeyboardButton("🗂️ إدارة الأصناف", callback_data="cats:root")],
        [InlineKeyboardButton("👥 إدارة العملاء", callback_data="users:list:0")],
        [InlineKeyboardButton("📦 كل الطلبات المعلقة", callback_data="orders:pending")],
        [InlineKeyboardButton("👤 إدارة المشرفين", callback_data="admins:root")],
        [InlineKeyboardButton("💰 رصيد xprostore.store", callback_data="xprostore:wallet")],
    ]
    return InlineKeyboardMarkup(rows)


@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.init_db()
    role = context.user_data.get("role", ROLE_OWNER)
    title = "🛠️ لوحة تحكم الإدارة" if role == ROLE_OWNER else "🗂️ لوحة تحكم الخدمات"
    await update.message.reply_text(title, reply_markup=main_kb(role))


# ---------------- Broadcast / direct message ----------------

@only_admin
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    role = context.user_data.get("role") or get_role(update.effective_user.id)

    if not require_permission(role, "callback", data):
        await query.answer("🚫 مفيش صلاحية لده.", show_alert=True)
        return
    await query.answer()

    if data == "main":
        title = "🛠️ لوحة تحكم الإدارة" if role == ROLE_OWNER else "🗂️ لوحة تحكم الخدمات"
        await query.edit_message_text(title, reply_markup=main_kb(role))

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

    elif data.startswith("cats:editicon:"):
        cat_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_category_icon", cat_id)
        await query.edit_message_text(ICON_PROMPT)

    elif data.startswith("cats:editname:"):
        cat_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_category_name", cat_id)
        await query.edit_message_text(
            "أرسل بيانات الصنف الجديدة بالشكل:\nالاسم بالعربي | English | إيموجي (اختياري)\n"
            "مثال: خدمات التصميم | Design Services | 🎨\n"
            "(لو سبت الإيموجي فاضي، هيفضل زي ما هو)"
        )

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

    elif data.startswith("svc:editicon:"):
        svc_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_service_icon", svc_id)
        await query.edit_message_text(ICON_PROMPT)

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

    elif data.startswith("var:editrequires:"):
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_variant_requires", variant_id)
        await query.edit_message_text(REQUIRES_PROMPT)

    elif data.startswith("var:editdetails:"):
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("edit_variant_details", variant_id)
        await query.edit_message_text("أرسل الوصف/التفاصيل الجديدة بالعربي:")

    elif data.startswith("var:stock:"):
        _, _, variant_id, delta = data.split(":")
        db.adjust_variant_stock(int(variant_id), int(delta))
        await show_variant_admin(query, int(variant_id))

    elif data.startswith("var:linkapi:"):
        if role != ROLE_OWNER:
            await query.answer("🚫 ربط API متاح لصاحب البوت بس.", show_alert=True)
            return
        variant_id = int(data.split(":")[2])
        context.user_data["awaiting"] = ("link_variant_api", variant_id)
        await query.edit_message_text(
            "أرسل رقم الـ ID بتاع الخدمة في xprostore.store عشان تربطها بالنسخة دي "
            "(الطلبات هتتنفذ تلقائيًا وهيتحدث المخزون لوحده).\n\n"
            "الخدمة عندها أكتر من نسخة (جنيه ودولار)؟ ابعت الأرقام مفصولة بفاصلة زي: 13,129 "
            "- هيجرب الأول، ولو فشل (مثلاً رصيدك بالعملة دي خلص) يجرب اللي بعده تلقائيًا.\n\n"
            "معرفش الـ ID؟ ابعت جزء من اسم الخدمة (زي: جيميناي) وهجيبلك أقرب الخدمات في القائمة.\n\n"
            "ابعت \"الغاء\" عشان تفك الربط وترجعها يدوية."
        )

    elif data.startswith("var:unlinkapi:"):
        if role != ROLE_OWNER:
            await query.answer("🚫 ربط API متاح لصاحب البوت بس.", show_alert=True)
            return
        variant_id = int(data.split(":")[2])
        db.update_variant_field(variant_id, "api_service_id", None)
        await show_variant_admin(query, variant_id)

    elif data.startswith("var:syncnow:"):
        if role != ROLE_OWNER:
            await query.answer("🚫 متاح لصاحب البوت بس.", show_alert=True)
            return
        variant_id = int(data.split(":")[2])
        v = db.get_variant(variant_id)
        primary_id = str(v["api_service_id"]).split(",")[0].strip()
        try:
            services = xprostore_api.list_services()
        except xprostore_api.XProStoreError as e:
            await query.answer(f"⚠️ تعذر جلب القائمة: {e}", show_alert=True)
            return
        match = None
        for s in services:
            sid = str(s.get("id") or s.get("service_id") or "")
            if sid == primary_id:
                match = s
                break
        if not match:
            await query.message.reply_text(
                f"⚠️ الخدمة رقم {primary_id} مش موجودة في قائمة الـ API دلوقتي (اتقفلت أو اتغير رقمها؟)."
            )
            return
        # Try the same field-name guesses api_sync.py uses, so we can tell
        # whether the guess actually matched something for this service.
        # Confirmed field names (from a real xprostore.store service entry):
        # available_inventory_count / track_inventory / stock_status.
        if match.get("track_inventory") is False:
            stock_val = -1
        elif str(match.get("stock_status", "")).lower() == "out_of_stock":
            stock_val = 0
        else:
            stock_val = match.get("available_inventory_count")
            if stock_val is None:
                stock_val = match.get("stock", match.get("quantity"))
        applied_note = ""
        if stock_val is not None:
            try:
                stock_int = int(stock_val)
                db.set_variant_stock(variant_id, stock_int)
                applied_note = f"\n✅ تم تحديث المخزون المحلي إلى: {stock_int}"
            except (TypeError, ValueError):
                applied_note = "\n⚠️ القيمة اللي لقيتها مش رقم صحيح، معرفتش أطبقها."
        else:
            applied_note = "\n⚠️ مفيش حقل stock/quantity في الرد - محتاجين نشوف الرد الخام تحت ونعرف الاسم الصح."
        import json
        raw = json.dumps(match, ensure_ascii=False, indent=2)[:1500]
        await query.message.reply_text(f"📦 الرد الخام لخدمة {primary_id} من xprostore:\n\n{raw}{applied_note}")

    elif data == "xprostore:wallet":
        try:
            wallet = xprostore_api.get_wallet()
            import json
            pretty = json.dumps(wallet, ensure_ascii=False, indent=2) if isinstance(wallet, (dict, list)) else str(wallet)
            await query.message.reply_text(f"💰 رصيدك في xprostore.store:\n\n{pretty}")
        except xprostore_api.XProStoreError as e:
            await query.answer(f"⚠️ تعذر جلب الرصيد: {e}", show_alert=True)

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

    elif data.startswith("order:view:"):
        order_id = int(data.split(":")[2])
        await show_order_admin(query, order_id)

    elif data.startswith("admin_deliver:"):
        order_id = int(data.split(":")[1])
        context.user_data["awaiting"] = ("deliver_note", order_id)
        await query.edit_message_text(
            "اكتب رسالة تسليم للعميل (تفاصيل، بيانات الحساب، إلخ)، أو ابعت \"تخطي\" للرسالة الافتراضية:"
        )

    elif data.startswith("admin_refund:"):
        order_id = int(data.split(":")[1])
        context.user_data["awaiting"] = ("refund_note", order_id)
        await query.edit_message_text(
            "اكتب سبب الإلغاء/الاسترجاع عشان يوصل للعميل، أو ابعت \"تخطي\" للرسالة الافتراضية:"
        )

    elif data.startswith("admin_msg_order:"):
        order_id = int(data.split(":")[1])
        order = db.get_order(order_id)
        if not order:
            await query.answer("⚠️ الطلب غير موجود.", show_alert=True)
            return
        context.user_data["awaiting"] = ("msg_order_text", order_id)
        await query.edit_message_text(f"✉️ أرسل نص الرسالة اللي تحب تبعتها للعميل بخصوص الطلب #{order_id}:")

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

    elif data == "admins:root":
        await show_admins_list(query)

    elif data == "admins:add":
        context.user_data["awaiting"] = ("add_subadmin_id",)
        await query.edit_message_text(
            "أرسل Telegram ID الخاص بالمشرف الجديد.\n"
            "(المشرف لازم يكون بدأ محادثة مع البوت قبل كده، أو خليه يبعتلك أي رسالة للبوت الأول عشان تعرف الـ ID بتاعه.)"
        )

    elif data.startswith("admins:remove:"):
        target_id = int(data.split(":")[2])
        db.remove_admin(target_id)
        await query.edit_message_text("✅ تم حذف المشرف.")
        await show_admins_list(query)


async def show_admins_list(query):
    admins = db.list_admins()
    rows = []
    role_labels = {ROLE_SERVICES: "إدارة الخدمات"}
    for a in admins:
        label = f"{a['telegram_id']}" + (f" (@{a['username']})" if a["username"] else "")
        label += f" — {role_labels.get(a['role'], a['role'])}"
        rows.append([InlineKeyboardButton(f"❌ {label}", callback_data=f"admins:remove:{a['telegram_id']}")])
    rows.append([InlineKeyboardButton("➕ إضافة مشرف جديد", callback_data="admins:add")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="main")])
    text = "👤 المشرفون الحاليون:" if admins else "👤 مفيش مشرفين إضافيين لسه.\nأنت (الأونر) عندك كل الصلاحيات دايمًا."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_categories_admin(query):
    cats = db.list_categories(include_hidden=True)
    rows = []
    for c in cats:
        label = f"{c['emoji']} {c['name_ar']}" + (" (مخفي)" if c["hidden"] else "")
        rows.append([InlineKeyboardButton(
            label, callback_data=f"cats:view:{c['id']}",
            icon_custom_emoji_id=c.get("icon_custom_emoji_id") or None,
        )])
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
        rows.append([InlineKeyboardButton(
            label, callback_data=f"svc:view:{s['id']}",
            icon_custom_emoji_id=s.get("icon_custom_emoji_id") or None,
        )])
    rows.append([InlineKeyboardButton("➕ إضافة منتج", callback_data=f"svc:add:{cat_id}")])
    rows.append([
        InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"cats:editname:{cat_id}"),
        InlineKeyboardButton("🖼️ أيقونة الصنف", callback_data=f"cats:editicon:{cat_id}"),
    ])
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
        if v.get("api_service_id"):
            label = "🤖 " + label
        if v["hidden"]:
            label += " (مخفي)"
        rows.append([InlineKeyboardButton(label, callback_data=f"var:view:{v['id']}")])
    rows.append([InlineKeyboardButton("➕ إضافة نسخة/مدة", callback_data=f"var:add:{service_id}")])
    rows.append([
        InlineKeyboardButton("✏️ تعديل اسم المنتج", callback_data=f"svc:editname:{service_id}"),
        InlineKeyboardButton("🖼️ تعديل الأيقونة", callback_data=f"svc:editicon:{service_id}"),
    ])
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
    requires_label = "إيميل 📧" if v["requires_email"] else ("رابط 🔗" if v["requires_link"] else "لا شيء")
    api_label = f"🤖 مربوطة بـ API (ID: {v['api_service_id']}) - تلقائي بالكامل" if v.get("api_service_id") \
        else "🖐️ يدوية (مش مربوطة بـ API)"
    text = (
        f"📦 {s['name_ar']} — {v['name_ar']}\n{v['details_ar']}\n\n"
        f"💵 السعر: {v['price_egp']:.2f} EGP\n"
        f"📊 الكمية: {stock_label}\n"
        f"📥 مطلوب من العميل بعد الشراء: {requires_label}\n"
        f"👁️ الحالة: {'مخفي' if v['hidden'] else 'ظاهر'}\n"
        f"🔗 التنفيذ: {api_label}"
    )
    rows = [
        [InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"var:editname:{variant_id}"),
         InlineKeyboardButton("✏️ تعديل الوصف", callback_data=f"var:editdetails:{variant_id}")],
        [InlineKeyboardButton("✏️ تعديل السعر", callback_data=f"var:editprice:{variant_id}")],
        [InlineKeyboardButton("📥 تعديل المطلوب بعد الشراء", callback_data=f"var:editrequires:{variant_id}")],
        [InlineKeyboardButton("➕ زيادة مخزون", callback_data=f"var:stock:{variant_id}:5"),
         InlineKeyboardButton("➖ إنقاص مخزون", callback_data=f"var:stock:{variant_id}:-5")],
        [InlineKeyboardButton("👁️ إخفاء/إظهار", callback_data=f"var:hide:{variant_id}"),
         InlineKeyboardButton("🗑️ حذف", callback_data=f"var:delete:{variant_id}")],
        ([InlineKeyboardButton("🔌 فك الربط بـ API", callback_data=f"var:unlinkapi:{variant_id}")]
         if v.get("api_service_id") else
         [InlineKeyboardButton("🔗 ربط API", callback_data=f"var:linkapi:{variant_id}")]),
    ]
    if v.get("api_service_id"):
        rows.append([InlineKeyboardButton("🔄 مزامنة المخزون الآن", callback_data=f"var:syncnow:{variant_id}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"svc:view:{v['service_id']}")])
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
    rows = []
    for o in rows_:
        label = f"#{o['id']} — {o['service_name_ar']} — {o['price_egp']:.0f} EGP"
        rows.append([InlineKeyboardButton(label, callback_data=f"order:view:{o['id']}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="main")])
    await query.edit_message_text("📦 الطلبات قيد التنفيذ - دوس على أي طلب عشان تسلّمه أو تسترجعه:",
                                   reply_markup=InlineKeyboardMarkup(rows))


async def show_order_admin(query, order_id):
    o = db.get_order(order_id)
    if not o:
        await query.edit_message_text("⚠️ الطلب غير موجود.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 رجوع", callback_data="orders:pending")]]))
        return
    user = db.get_user_by_id(o["user_id"])
    lines = [
        f"📦 طلب #{o['id']}",
        f"👤 العميل: {user['telegram_id']} (@{user['username']})" if user else "👤 العميل: غير معروف",
        f"🛍️ الخدمة: {o['service_name_ar']}",
        f"💵 السعر: {o['price_egp']:.2f} EGP",
        f"📌 الحالة: {o['status']}",
    ]
    if o.get("email"):
        lines.append(f"📧 الإيميل: {o['email']}")
    if o.get("link"):
        lines.append(f"🔗 الرابط: {o['link']}")
    if o.get("api_order_id"):
        lines.append(f"🤖 طلب API: #{o['api_order_id']} (الحالة عند xprostore: {o.get('api_status') or '؟'})")
    if o.get("note"):
        lines.append(f"📝 ملاحظة: {o['note']}")
    rows = []
    if o["status"] == "in_progress":
        rows.append([
            InlineKeyboardButton("✅ تم التسليم", callback_data=f"admin_deliver:{o['id']}"),
            InlineKeyboardButton("♻️ إلغاء واسترجاع", callback_data=f"admin_refund:{o['id']}"),
        ])
    rows.append([InlineKeyboardButton("✉️ راسل العميل", callback_data=f"admin_msg_order:{o['id']}")])
    rows.append([InlineKeyboardButton("🔙 رجوع للطلبات", callback_data="orders:pending")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


# ---------------- Notifications back to the customer bot ----------------

def customer_bot() -> Bot:
    return Bot(token=config.CUSTOMER_BOT_TOKEN)


async def deliver_notify_customer(context, order_id, note=None):
    order = db.get_order(order_id)
    user = db.get_user_by_id(order["user_id"])
    lang = user["lang"]
    text = (f"✅ تم تسليم طلبك #{order_id} ({order['service_name_ar']}) بنجاح!" if lang == "ar"
            else f"✅ Your order #{order_id} ({order['service_name_ar']}) has been delivered!")
    if note:
        text += f"\n\n{note}"
    try:
        await customer_bot().send_message(user["telegram_id"], text)
    except TelegramError as e:
        log.error("Failed notifying customer of delivery: %s", e)


async def refund_notify_customer(context, order, note=None):
    user = db.get_user_by_id(order["user_id"])
    lang = user["lang"]
    text = (f"♻️ تم إلغاء طلبك #{order['id']} ({order['service_name_ar']}) وإعادة {order['price_egp']:.2f} جنيه لرصيدك."
            if lang == "ar" else
            f"♻️ Your order #{order['id']} ({order['service_name_ar']}) was cancelled and {order['price_egp']:.2f} EGP was refunded to your balance.")
    if note:
        text += f"\n\n{note}"
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

    role = context.user_data.get("role") or get_role(update.effective_user.id)
    if kind != "add_subadmin_id" and not require_permission(role, "awaiting", kind):
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("🚫 مفيش صلاحية لده.")
        return
    if kind == "add_subadmin_id" and role != ROLE_OWNER:
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("🚫 مفيش صلاحية لده.")
        return

    NAME_KINDS = {"add_category", "edit_category_name", "add_service_name", "edit_service_name", "add_variant_name", "edit_variant_name"}
    is_forwarded = update.message.forward_origin is not None
    if kind in NAME_KINDS and not is_forwarded and any(
        e.type == "custom_emoji" for e in (update.message.entities or [])
    ):
        await update.message.reply_text(
            "⚠️ الإيموجي ده إيموجي مميز (بريميوم) من تليجرام - لو كتبته/لصقته مباشرة بيتحفظ بشكل مختلف عن اللي شايفه. "
            "الحل: افتح أي رسالة فيها نفس الإيموجي واعمل تحويل (Forward) للرسالة دي للبوت بدل ما تكتبه بنفسك، "
            "وهيتحفظ صح. أو ابعت اسم بإيموجي عادي من لوحة الإيموجي الأساسية."
        )
        return

    if kind == "add_subadmin_id":
        context.user_data.pop("awaiting", None)
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ من فضلك أرسل رقم Telegram ID صحيح.")
            return
        if new_admin_id == config.ADMIN_ID:
            await update.message.reply_text("⚠️ ده الآي دي بتاعك أنت، وأنت أونر أصلاً بكل الصلاحيات.")
            return
        db.add_admin(new_admin_id, role=ROLE_SERVICES, added_by=update.effective_user.id)
        await update.message.reply_text(
            f"✅ تم إضافة {new_admin_id} كمشرف بصلاحية (إدارة الخدمات فقط: إضافة/تعديل/حذف الأصناف والمنتجات والنسخ).\n"
            "المشرف الجديد يقدر يفتح البوت بـ /start دلوقتي."
        )
        try:
            await context.bot.send_message(
                new_admin_id,
                "🎉 تم إضافتك كمشرف في بوت الإدارة بصلاحية (إدارة الخدمات).\nابعت /start عشان تبدأ."
            )
        except TelegramError:
            pass

    elif kind == "msg_one_id":
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

    elif kind == "msg_order_text":
        order_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        order = db.get_order(order_id)
        if not order:
            await update.message.reply_text("⚠️ الطلب غير موجود.")
            return
        user = db.get_user_by_id(order["user_id"])
        try:
            await customer_bot().send_message(user["telegram_id"], f"✉️ بخصوص طلبك #{order_id}:\n\n{text}")
            await update.message.reply_text("✅ تم إرسال الرسالة للعميل.")
        except TelegramError as e:
            await update.message.reply_text(f"⚠️ فشل الإرسال: {e}")

    elif kind == "deliver_note":
        order_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        note = None if text.strip() in ("تخطي", "skip", "Skip") else text.strip()
        db.set_order_status(order_id, "delivered")
        await deliver_notify_customer(context, order_id, note=note)
        await update.message.reply_text(f"✅ تم تعليم الطلب #{order_id} كـ (تم التسليم) وتم إشعار العميل.")

    elif kind == "refund_note":
        order_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        note = None if text.strip() in ("تخطي", "skip", "Skip") else text.strip()
        order = db.refund_order(order_id)
        if order:
            await refund_notify_customer(context, order, note=note)
            await update.message.reply_text(f"♻️ تم إلغاء الطلب #{order_id} واسترجاع المبلغ للعميل.")
        else:
            await update.message.reply_text("⚠️ الطلب غير موجود أو تم استرجاعه بالفعل.")

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
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("⚠️ صيغة غلط، حاول تاني بالشكل: عربي | English | إيموجي")
            return
        name_ar, name_en = parts[0], parts[1]
        emoji = parts[2] if len(parts) > 2 else ""
        context.user_data["awaiting"] = ("add_category_icon", name_ar, name_en, emoji)
        await update.message.reply_text(ICON_PROMPT)

    elif kind == "add_category_icon":
        name_ar, name_en, emoji = awaiting[1], awaiting[2], awaiting[3]
        context.user_data.pop("awaiting", None)
        icon_id = None if text in ("تخطي", "skip", "Skip") else extract_custom_emoji_id(update.message)
        if icon_id is None and text not in ("تخطي", "skip", "Skip"):
            await update.message.reply_text(
                "⚠️ مش شايف إيموجي مميز في الرسالة دي. جرب تاني أو ابعت \"تخطي\"."
            )
            context.user_data["awaiting"] = ("add_category_icon", name_ar, name_en, emoji)
            return
        db.add_category(name_ar, name_en, emoji, icon_custom_emoji_id=icon_id)
        await update.message.reply_text(f"✅ تم إضافة الصنف: {name_ar}")

    elif kind == "edit_category_name":
        cat_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("⚠️ صيغة غلط، حاول تاني بالشكل: عربي | English | إيموجي (اختياري)")
            return
        name_ar, name_en = parts[0], parts[1]
        db.update_category_field(cat_id, "name_ar", name_ar)
        db.update_category_field(cat_id, "name_en", name_en)
        if len(parts) > 2 and parts[2]:
            db.update_category_field(cat_id, "emoji", parts[2])
        await update.message.reply_text(f"✅ تم تحديث اسم الصنف إلى: {name_ar}")

    elif kind == "add_service_name":
        cat_id = awaiting[1]
        name_ar = text
        context.user_data["awaiting"] = ("add_service_icon", cat_id, name_ar)
        await update.message.reply_text(ICON_PROMPT)

    elif kind == "add_service_icon":
        cat_id, name_ar = awaiting[1], awaiting[2]
        context.user_data.pop("awaiting", None)
        icon_id = None if text in ("تخطي", "skip", "Skip") else extract_custom_emoji_id(update.message)
        if icon_id is None and text not in ("تخطي", "skip", "Skip"):
            await update.message.reply_text(
                "⚠️ مش شايف إيموجي مميز في الرسالة دي. جرب تاني أو ابعت \"تخطي\"."
            )
            context.user_data["awaiting"] = ("add_service_icon", cat_id, name_ar)
            return
        name_en = translate_to_english(name_ar)
        db.add_service(cat_id, name_ar, name_en, icon_custom_emoji_id=icon_id)
        await update.message.reply_text(
            f"✅ تم إضافة المنتج: {name_ar}\nدلوقتي ضيفله نسخة/مدة واحدة على الأقل من زر ➕ إضافة نسخة/مدة عشان يبقى قابل للشراء."
        )

    elif kind == "edit_category_icon":
        cat_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        if text in ("تخطي", "skip", "Skip"):
            db.update_category_field(cat_id, "icon_custom_emoji_id", None)
            await update.message.reply_text("✅ تم إلغاء الأيقونة.")
            return
        icon_id = extract_custom_emoji_id(update.message)
        if icon_id is None:
            await update.message.reply_text("⚠️ مش شايف إيموجي مميز في الرسالة دي. جرب تاني أو ابعت \"تخطي\".")
            context.user_data["awaiting"] = ("edit_category_icon", cat_id)
            return
        db.update_category_field(cat_id, "icon_custom_emoji_id", icon_id)
        await update.message.reply_text("✅ تم تحديث أيقونة الصنف.")

    elif kind == "edit_service_icon":
        svc_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        if text in ("تخطي", "skip", "Skip"):
            db.update_service_field(svc_id, "icon_custom_emoji_id", None)
            await update.message.reply_text("✅ تم إلغاء الأيقونة.")
            return
        icon_id = extract_custom_emoji_id(update.message)
        if icon_id is None:
            await update.message.reply_text("⚠️ مش شايف إيموجي مميز في الرسالة دي. جرب تاني أو ابعت \"تخطي\".")
            context.user_data["awaiting"] = ("edit_service_icon", svc_id)
            return
        db.update_service_field(svc_id, "icon_custom_emoji_id", icon_id)
        await update.message.reply_text("✅ تم تحديث أيقونة المنتج.")

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
        context.user_data["awaiting"] = ("add_variant_requires", svc_id, name_ar, details_ar, price, stock)
        await update.message.reply_text(REQUIRES_PROMPT)

    elif kind == "add_variant_requires":
        svc_id, name_ar, details_ar, price, stock = (
            awaiting[1], awaiting[2], awaiting[3], awaiting[4], awaiting[5]
        )
        context.user_data.pop("awaiting", None)
        requires_email, requires_link = parse_requires_choice(text)
        name_en = translate_to_english(name_ar)
        details_en = translate_to_english(details_ar)
        db.add_variant(svc_id, name_ar, name_en, details_ar, details_en, price, stock, requires_email, requires_link)
        await update.message.reply_text(f"✅ تم إضافة النسخة: {name_ar}")

    elif kind == "edit_variant_requires":
        variant_id = awaiting[1]
        context.user_data.pop("awaiting", None)
        requires_email, requires_link = parse_requires_choice(text)
        db.update_variant_field(variant_id, "requires_email", requires_email)
        db.update_variant_field(variant_id, "requires_link", requires_link)
        await update.message.reply_text("✅ تم تحديث المطلوب من العميل بعد الشراء.")

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

    elif kind == "link_variant_api":
        variant_id = awaiting[1]
        if role != ROLE_OWNER:
            context.user_data.pop("awaiting", None)
            await update.message.reply_text("🚫 ربط API متاح لصاحب البوت بس.")
            return
        if text in ("الغاء", "إلغاء", "cancel"):
            context.user_data.pop("awaiting", None)
            db.update_variant_field(variant_id, "api_service_id", None)
            await update.message.reply_text("✅ تم فك الربط، النسخة رجعت يدوية.")
            return
        cleaned = text.replace(" ", "")
        parts = cleaned.split(",")
        if parts and all(p.isdigit() for p in parts):
            context.user_data.pop("awaiting", None)
            db.update_variant_field(variant_id, "api_service_id", cleaned)
            label = " ثم ".join(parts)
            await update.message.reply_text(
                f"✅ تم ربط النسخة بخدمة API رقم {label}. الطلبات هتتنفذ تلقائيًا والكمية هتتحدث لوحدها.\n"
                + ("لو الأول فشل، هيجرب الباقي بالترتيب تلقائيًا." if len(parts) > 1 else "")
            )
            return
        # Not a number and not "الغاء" -> treat as a name search to help find the ID.
        try:
            services = xprostore_api.list_services()
        except xprostore_api.XProStoreError as e:
            await update.message.reply_text(f"⚠️ تعذر البحث في قائمة API: {e}\nابعت رقم الـ ID مباشرة لو عارفه.")
            return
        needle = text.strip().lower()
        matches = [s for s in services if needle in str(s.get("name", "")).lower()][:8]
        if not matches:
            await update.message.reply_text("مفيش نتائج بالاسم ده. ابعت رقم الـ ID مباشرة، أو جرب كلمة تانية.")
            return
        lines = ["أقرب نتائج لقيتها:"]
        for s in matches:
            sid = s.get("id") or s.get("service_id")
            lines.append(f"• ID {sid} — {s.get('name', '؟')} — {s.get('price', '؟')}")
        lines.append("\nابعتلي رقم الـ ID اللي تحب تربطها بيه، أو \"الغاء\" لو غيرت رأيك.")
        await update.message.reply_text("\n".join(lines))
        # stays in the same awaiting state so the admin's next message (the ID) is handled above

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


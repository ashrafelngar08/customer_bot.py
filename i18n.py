"""All customer-facing strings, Arabic + English, keyed by short id."""

STRINGS = {
    "welcome": {
        "ar": "أهلًا بيك 👋\nاختر من القائمة تحت:",
        "en": "Welcome 👋\nChoose from the menu below:",
    },
    "menu_services": {"ar": "🛒 الخدمات", "en": "🛒 Services"},
    "menu_orders": {"ar": "📋 طلباتي السابقة", "en": "📋 My Orders"},
    "menu_currency": {"ar": "💱 تحويل العملات", "en": "💱 Currency"},
    "menu_topup": {"ar": "💳 إضافة رصيد", "en": "💳 Add Balance"},
    "menu_balance": {"ar": "💰 رصيدي", "en": "💰 My Balance"},
    "menu_lang": {"ar": "🌐 اللغة / Language", "en": "🌐 Language / اللغة"},
    "menu_profile": {"ar": "⚙️ حسابي الشخصي", "en": "⚙️ My Account"},
    "menu_referral": {"ar": "💸 الربح عبر الدعوة", "en": "💸 Earn via Referral"},
    "menu_support": {"ar": "📞 الدعم الفني", "en": "📞 Support"},
    "back": {"ar": "🔙 رجوع", "en": "🔙 Back"},
    "main_menu": {"ar": "🏠 القائمة الرئيسية", "en": "🏠 Main Menu"},

    "choose_category": {"ar": "اختر الصنف:", "en": "Choose a category:"},
    "no_categories": {"ar": "لا توجد أصناف متاحة حاليًا.", "en": "No categories available right now."},
    "choose_service": {"ar": "اختر الخدمة:", "en": "Choose a service:"},
    "no_services": {"ar": "لا توجد خدمات في هذا الصنف حاليًا.", "en": "No services in this category yet."},
    "service_details": {"ar": "📦 {name}\n\n{details}\n\n💵 السعر: {price}", "en": "📦 {name}\n\n{details}\n\n💵 Price: {price}"},
    "buy": {"ar": "🛍️ شراء", "en": "🛍️ Buy"},
    "out_of_stock": {"ar": "⚠️ نفدت الكمية المتاحة من هذه الخدمة حاليًا.", "en": "⚠️ This service is out of stock right now."},
    "insufficient_balance": {
        "ar": "❌ رصيدك غير كافٍ لإتمام هذا الطلب.\nرصيدك الحالي: {balance}\nسعر الخدمة: {price}\n\nجاري تحويلك لقائمة إضافة رصيد 👇",
        "en": "❌ Your balance isn't enough for this order.\nYour balance: {balance}\nService price: {price}\n\nRedirecting you to Add Balance 👇",
    },
    "ask_email": {"ar": "📧 من فضلك أرسل الإيميل المطلوب تفعيل الخدمة عليه:", "en": "📧 Please send the email to activate this service on:"},
    "invalid_email": {"ar": "⚠️ الإيميل غير صحيح، حاول تاني.", "en": "⚠️ That doesn't look like a valid email, try again."},
    "order_placed": {
        "ar": "✅ تم استلام طلبك بنجاح!\nالخدمة: {name}\nالسعر: {price}\nرقم الطلب: #{order_id}\n\nسيتم تنفيذه في أقرب وقت.",
        "en": "✅ Your order was placed successfully!\nService: {name}\nPrice: {price}\nOrder #{order_id}\n\nIt will be processed shortly.",
    },

    "orders_title": {"ar": "📋 طلباتك السابقة:", "en": "📋 Your previous orders:"},
    "no_orders": {"ar": "لا يوجد لديك أي طلبات بعد.", "en": "You have no orders yet."},
    "order_line": {
        "ar": "🔹 #{id} — {name}\n   📅 {date} | 💵 {price} | 📌 {status}",
        "en": "🔹 #{id} — {name}\n   📅 {date} | 💵 {price} | 📌 {status}",
    },
    "status_pending": {"ar": "قيد الانتظار", "en": "Pending"},
    "status_in_progress": {"ar": "قيد التنفيذ", "en": "In Progress"},
    "status_delivered": {"ar": "تم التسليم ✅", "en": "Delivered ✅"},
    "status_refunded": {"ar": "ملغي ومسترجع 🔁", "en": "Cancelled & Refunded 🔁"},

    "currency_title": {"ar": "💱 اختر العملة اللي هتتعرض بيها الأسعار:", "en": "💱 Choose the currency prices are shown in:"},
    "egp": {"ar": "🇪🇬 جنيه مصري", "en": "🇪🇬 EGP"},
    "usd": {"ar": "💵 دولار", "en": "💵 USD"},
    "currency_saved": {"ar": "تم الحفظ ✅ الأسعار هتتعرض دلوقتي بالـ {cur}", "en": "Saved ✅ Prices will now show in {cur}"},

    "topup_title": {"ar": "💳 اختر طريقة إضافة الرصيد:", "en": "💳 Choose a top-up method:"},
    "vf_instructions": {
        "ar": "حوّل المبلغ إلى المحفظة التالية:\n📱 `{number}`\n\nبعد التحويل، اكتب رسالة بالشكل التالي:\nرقم الهاتف اللي حولت منه، مسافة، ثم المبلغ\nمثال: `01012345678 100`",
        "en": "Send the amount to this wallet:\n📱 `{number}`\n\nAfter sending, reply with:\nthe phone number you sent from, a space, then the amount\nExample: `01012345678 100`",
    },
    "bp_instructions": {
        "ar": "حوّل المبلغ إلى Binance ID التالي:\n🆔 `{bid}`\n\nبعد التحويل، اكتب رسالة بالشكل التالي:\nرقم الطلب (Order ID)، مسافة، ثم المبلغ\nمثال: `123456789 100`",
        "en": "Send the amount to this Binance ID:\n🆔 `{bid}`\n\nAfter sending, reply with:\nyour Order ID, a space, then the amount\nExample: `123456789 100`",
    },
    "topup_bad_format": {"ar": "⚠️ الصيغة غلط. اكتب: القيمة الأولى مسافة ثم المبلغ (أرقام فقط).", "en": "⚠️ Wrong format. Send: first value, a space, then the amount (numbers only)."},
    "topup_submitted": {
        "ar": "✅ تم إرسال طلبك للإدارة، هيتم تأكيده يدويًا خلال وقت قصير وسيُضاف الرصيد تلقائيًا بعد التأكيد.",
        "en": "✅ Your request was sent to the admin team, it'll be confirmed shortly and your balance will be added automatically once approved.",
    },
    "topup_approved": {"ar": "✅ تم تأكيد شحن رصيدك بمبلغ {amount}. رصيدك الحالي: {balance}", "en": "✅ Your top-up of {amount} was approved. Your balance: {balance}"},
    "topup_rejected": {"ar": "❌ للأسف تم رفض طلب شحن الرصيد. تواصل مع الدعم الفني لو فيه استفسار.", "en": "❌ Unfortunately your top-up request was rejected. Contact support if you have questions."},

    "balance_title": {"ar": "💰 رصيدك الحالي: {balance}", "en": "💰 Your current balance: {balance}"},

    "lang_title": {"ar": "🌐 اختر اللغة:", "en": "🌐 Choose your language:"},
    "lang_saved": {"ar": "✅ تم تغيير اللغة للعربي", "en": "✅ Language switched to English"},

    "profile_title": {
        "ar": (
            "⚙️ حسابك الشخصي\n\n"
            "🆔 المعرف: {id}\n"
            "📱 الهاتف: {phone}\n"
            "📅 تاريخ الانضمام: {joined}\n"
            "💱 العملة: {currency}\n"
            "🛍️ إجمالي الطلبات: {total_orders}\n"
            "✅ الطلبات المكتملة: {completed}\n"
            "💵 إجمالي الإنفاق: {spent}\n"
            "💰 الرصيد الحالي: {balance}\n"
            "💸 إجمالي مكافآت الإحالة: {ref_earnings}"
        ),
        "en": (
            "⚙️ Your account\n\n"
            "🆔 ID: {id}\n"
            "📱 Phone: {phone}\n"
            "📅 Joined: {joined}\n"
            "💱 Currency: {currency}\n"
            "🛍️ Total orders: {total_orders}\n"
            "✅ Completed orders: {completed}\n"
            "💵 Total spent: {spent}\n"
            "💰 Current balance: {balance}\n"
            "💸 Total referral rewards: {ref_earnings}"
        ),
    },
    "not_set": {"ar": "غير مسجل", "en": "not set"},

    "referral_title": {
        "ar": "💸 اربح {bonus} عن كل صديق تدعوه!\nهيتم إضافة المكافأة لرصيدك فور ما صاحبك يعمل أول طلب ليه.\n\n👥 عدد إحالاتك: {count}\n🔗 رابط الإحالة الخاص بيك:\n{link}",
        "en": "💸 Earn {bonus} for every friend you invite!\nThe reward is added to your balance as soon as your friend places their first order.\n\n👥 Your referrals: {count}\n🔗 Your referral link:\n{link}",
    },

    "support_title": {"ar": "📞 الدعم الفني\n\nتواصل معنا: {user}\nقناتنا: {channel}", "en": "📞 Support\n\nContact us: {user}\nOur channel: {channel}"},

    "banned": {"ar": "🚫 تم حظر حسابك من استخدام البوت.", "en": "🚫 Your account has been banned from using this bot."},
    "ask_phone": {"ar": "📱 من فضلك شارك رقم هاتفك للمتابعة:", "en": "📱 Please share your phone number to continue:"},
    "share_phone_btn": {"ar": "📱 مشاركة رقم الهاتف", "en": "📱 Share phone number"},
    "phone_saved": {"ar": "✅ تم حفظ رقم هاتفك.", "en": "✅ Your phone number was saved."},
}


def t(key: str, lang: str, **kwargs) -> str:
    lang = lang if lang in ("ar", "en") else "ar"
    text = STRINGS[key][lang]
    return text.format(**kwargs) if kwargs else text

"""
Data layer. Deliberately isolated from any Telegram/bot code so the customer
bot and admin bot both read/write the exact same store, and so the bots can
be restarted, redeployed, or crash without losing any customer data or
balances.

Uses PostgreSQL (e.g. Railway's managed Postgres) so data survives redeploys
and restarts - unlike a local SQLite file, which lives on the container's
ephemeral disk and gets wiped on every new deploy.

A small _CursorProxy below translates the old sqlite3-style call pattern
(conn.execute("... ? ...", params), cur.lastrowid) to psycopg2, so
customer_bot.py and admin_bot.py did not need to change at all.
"""
import time
import secrets
import contextlib
import psycopg2
import psycopg2.extras
from config import DATABASE_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    phone TEXT,
    join_date BIGINT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'ar',
    currency TEXT NOT NULL DEFAULT 'egp',
    balance REAL NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0,
    referred_by INTEGER,
    referral_code TEXT UNIQUE,
    referral_bonus_paid INTEGER NOT NULL DEFAULT 0,
    total_orders INTEGER NOT NULL DEFAULT 0,
    completed_orders INTEGER NOT NULL DEFAULT 0,
    total_spent REAL NOT NULL DEFAULT 0,
    referral_earnings REAL NOT NULL DEFAULT 0,
    referral_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    icon_custom_emoji_id TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS services (
    id SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    details_ar TEXT NOT NULL DEFAULT '',
    details_en TEXT NOT NULL DEFAULT '',
    price_egp REAL NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL DEFAULT -1,
    requires_email INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    icon_custom_emoji_id TEXT
);

-- A "service" above is now a *product* (e.g. "جيميناي برو"). Each product
-- has one or more variants (e.g. "18 شهر" / "12 شهر"), and price/stock/
-- email-requirement/buying now live on the variant, not the product.
CREATE TABLE IF NOT EXISTS variants (
    id SERIAL PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id),
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    details_ar TEXT NOT NULL DEFAULT '',
    details_en TEXT NOT NULL DEFAULT '',
    price_egp REAL NOT NULL,
    stock INTEGER NOT NULL DEFAULT -1,
    requires_email INTEGER NOT NULL DEFAULT 0,
    requires_link INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    -- NULL = manual/no API link (admin fulfills by hand, as before).
    -- Set = this variant is auto-fulfilled through the xprostore.store API
    -- (see xprostore_api.py); stock is then kept in sync from the API and
    -- is no longer meant to be edited by hand (the +/-  buttons still work
    -- but api_sync.py will overwrite the value on its next pass).
    api_service_id TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    variant_id INTEGER REFERENCES variants(id),
    service_name_ar TEXT NOT NULL,
    price_egp REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    email TEXT,
    link TEXT,
    created_at BIGINT NOT NULL,
    delivered_at BIGINT,
    note TEXT,
    -- Set only for orders placed through a variant linked to the
    -- xprostore.store API (see xprostore_api.py / api_sync.py).
    api_order_id TEXT,
    api_status TEXT,
    idempotency_key TEXT
);

-- A snapshot of the full xprostore.store catalog (every service they
-- offer, not just the ones you've linked), refreshed by api_sync.py so it
-- can tell you when something changes: a new service appears, one
-- disappears/gets disabled, or its description/price changes - even for
-- services you haven't linked yet.
CREATE TABLE IF NOT EXISTS api_catalog_snapshot (
    api_service_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    price_amount TEXT NOT NULL DEFAULT '',
    price_currency TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_seen BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS topups (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    method TEXT NOT NULL,
    amount REAL NOT NULL,
    reference TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at BIGINT NOT NULL,
    resolved_at BIGINT
);

-- Sub-admins added by the main admin (config.ADMIN_ID, who is always the
-- implicit 'owner' and is never stored here). Each row is an extra person
-- allowed into the admin bot, scoped to a role/permission set that the bot
-- code enforces (see ROLE_PERMISSIONS in admin_bot.py).
CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    role TEXT NOT NULL DEFAULT 'services',
    added_by BIGINT,
    added_at BIGINT NOT NULL
);
"""


class _CursorProxy:
    """Makes a psycopg2 cursor behave like the sqlite3 cursor this file was
    originally written against: '?' placeholders and a .lastrowid attribute
    (via an explicit RETURNING id on the few inserts that need the new id)."""

    def __init__(self, cursor):
        self._cur = cursor
        self.lastrowid = None

    def execute(self, query, params=()):
        query = query.replace("?", "%s")
        self._cur.execute(query, params)
        if "RETURNING" in query.upper():
            row = self._cur.fetchone()
            if row:
                self.lastrowid = row["id"]
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


@contextlib.contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    proxy = _CursorProxy(cur)
    try:
        yield proxy
        conn.commit()
    finally:
        cur.close()
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(SCHEMA)
        # Migration for DBs created before the variants table existed.
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS variant_id INTEGER REFERENCES variants(id)")
        # Migration for DBs created before button icons (Telegram custom-emoji
        # icons on buttons, Bot API 9.4+) were supported.
        conn.execute("ALTER TABLE categories ADD COLUMN IF NOT EXISTS icon_custom_emoji_id TEXT")
        conn.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS icon_custom_emoji_id TEXT")
        # Migration for DBs created before "requires a link from the customer"
        # (e.g. Facebook page ownership-transfer link) was supported, alongside
        # the pre-existing "requires an email" option.
        conn.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS requires_link INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS link TEXT")
        # Migration for DBs created before the xprostore.store API integration.
        conn.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS api_service_id TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS api_order_id TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS api_status TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS api_catalog_snapshot (
            api_service_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            price_amount TEXT NOT NULL DEFAULT '',
            price_currency TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            last_seen BIGINT NOT NULL
        )""")
        # Seed starter categories/services only on first run
        row = conn.execute("SELECT COUNT(*) c FROM categories").fetchone()
        if row["c"] == 0:
            _seed(conn)


def _seed(conn):
    cats = [
        ("خدمات السوشيال ميديا", "Social Media Services", "🚀", 1),
        ("اشتراكات البرامج", "Software Subscriptions", "⭐", 2),
        ("صفحات فيسبوك", "Facebook Pages", "👑", 3),
    ]
    cat_ids = []
    for name_ar, name_en, emoji, order in cats:
        cur = conn.execute(
            "INSERT INTO categories (name_ar, name_en, emoji, sort_order) VALUES (?,?,?,?) RETURNING id",
            (name_ar, name_en, emoji, order),
        )
        cat_ids.append(cur.lastrowid)

    services = [
        (cat_ids[0], "إعلانات ممولة", "Paid Ads Management",
         "إدارة وتشغيل حملات إعلانية ممولة", "Running and managing paid ad campaigns",
         500.0, -1, 0),
        (cat_ids[0], "إدارة صفحات", "Page Management",
         "إدارة كاملة لصفحتك على السوشيال ميديا", "Full management of your social page",
         300.0, -1, 0),
        (cat_ids[1], "اشتراك كانفا برو", "Canva Pro Subscription",
         "تفعيل كانفا برو على إيميلك", "Activate Canva Pro on your email",
         150.0, 20, 1),
        (cat_ids[1], "اشتراك كاب كات برو", "CapCut Pro Subscription",
         "تفعيل كاب كات برو على إيميلك", "Activate CapCut Pro on your email",
         120.0, 20, 1),
        (cat_ids[2], "صفحة فيسبوك 10k متابع", "Facebook Page 10k followers",
         "صفحة فيسبوك حقيقية 10 آلاف متابع", "Real Facebook page with 10k followers",
         900.0, 5, 0),
    ]
    for cat_id, nar, nen, dar, den, price, stock, req_email in services:
        cur = conn.execute(
            "INSERT INTO services (category_id, name_ar, name_en) VALUES (?,?,?) RETURNING id",
            (cat_id, nar, nen),
        )
        service_id = cur.lastrowid
        # Every product needs at least one variant to be purchasable - seed a
        # single default variant ("عام"/"General") carrying over the old
        # flat price/stock/details, so admins can then add more variants
        # (e.g. different durations) alongside it.
        conn.execute(
            """INSERT INTO variants
               (service_id, name_ar, name_en, details_ar, details_en, price_egp, stock, requires_email)
               VALUES (?,?,?,?,?,?,?,?)""",
            (service_id, "عام", "General", dar, den, price, stock, req_email),
        )


# ---------------- Users ----------------

def get_or_create_user(telegram_id: int, username: str | None, referred_by_code: str | None = None):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if row:
            return dict(row)
        referred_by = None
        if referred_by_code:
            ref = conn.execute("SELECT id FROM users WHERE referral_code=?", (referred_by_code,)).fetchone()
            if ref and ref["id"]:
                referred_by = ref["id"]
        code = secrets.token_hex(4)
        conn.execute(
            """INSERT INTO users (telegram_id, username, join_date, referral_code, referred_by)
               VALUES (?,?,?,?,?)""",
            (telegram_id, username, int(time.time()), code, referred_by),
        )
        if referred_by:
            conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE id=?", (referred_by,))
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row)


def get_user(telegram_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def set_lang(telegram_id: int, lang: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET lang=? WHERE telegram_id=?", (lang, telegram_id))


def set_currency(telegram_id: int, currency: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET currency=? WHERE telegram_id=?", (currency, telegram_id))


def adjust_balance(user_id: int, delta: float):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))


def set_ban(telegram_id: int, banned: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET banned=? WHERE telegram_id=?", (1 if banned else 0, telegram_id))


def list_users(limit=50, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]


def all_active_telegram_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT telegram_id FROM users WHERE banned=0").fetchall()
        return [r["telegram_id"] for r in rows]


# ---------------- Categories & Services ----------------

def list_categories(include_hidden=False):
    with get_conn() as conn:
        q = "SELECT * FROM categories"
        if not include_hidden:
            q += " WHERE hidden=0"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_category(cat_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
        return dict(row) if row else None


def add_category(name_ar, name_en, emoji="", icon_custom_emoji_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO categories (name_ar, name_en, emoji, icon_custom_emoji_id, sort_order)
               VALUES (?,?,?,?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM categories)) RETURNING id""",
            (name_ar, name_en, emoji, icon_custom_emoji_id),
        )
        return cur.lastrowid


def update_category_field(cat_id: int, field: str, value):
    allowed = {"name_ar", "name_en", "emoji", "icon_custom_emoji_id"}
    if field not in allowed:
        raise ValueError("field not allowed")
    with get_conn() as conn:
        conn.execute(f"UPDATE categories SET {field}=? WHERE id=?", (value, cat_id))


def delete_category(cat_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM services WHERE category_id=?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))


def set_category_hidden(cat_id: int, hidden: bool):
    with get_conn() as conn:
        conn.execute("UPDATE categories SET hidden=? WHERE id=?", (1 if hidden else 0, cat_id))


def list_services(category_id: int, include_hidden=False):
    with get_conn() as conn:
        q = "SELECT * FROM services WHERE category_id=?"
        if not include_hidden:
            q += " AND hidden=0"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in conn.execute(q, (category_id,)).fetchall()]


def get_service(service_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        return dict(row) if row else None


def add_service(category_id, name_ar, name_en, details_ar="", details_en="", price_egp=0, stock=-1,
                 requires_email=0, icon_custom_emoji_id=None):
    """Adds a *product*. Price/stock/email fields are accepted for backward
    compatibility but purchasing now happens through the product's variants
    (see add_variant) - a product needs at least one variant to be buyable."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO services
               (category_id, name_ar, name_en, details_ar, details_en, price_egp, stock, requires_email, icon_custom_emoji_id)
               VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
            (category_id, name_ar, name_en, details_ar, details_en, price_egp, stock, requires_email, icon_custom_emoji_id),
        )
        return cur.lastrowid


def delete_service(service_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM variants WHERE service_id=?", (service_id,))
        conn.execute("DELETE FROM services WHERE id=?", (service_id,))


def update_service_field(service_id: int, field: str, value):
    allowed = {"name_ar", "name_en", "details_ar", "details_en", "price_egp", "stock", "requires_email", "hidden",
               "icon_custom_emoji_id"}
    if field not in allowed:
        raise ValueError("field not allowed")
    with get_conn() as conn:
        conn.execute(f"UPDATE services SET {field}=? WHERE id=?", (value, service_id))


def adjust_stock(service_id: int, delta: int):
    with get_conn() as conn:
        row = conn.execute("SELECT stock FROM services WHERE id=?", (service_id,)).fetchone()
        if row is None or row["stock"] < 0:
            return  # unlimited stock, nothing to track
        conn.execute("UPDATE services SET stock = GREATEST(stock + ?, 0) WHERE id=?", (delta, service_id))


# ---------------- Variants (durations / options under a product) ----------------

def list_variants(service_id: int, include_hidden=False):
    with get_conn() as conn:
        q = "SELECT * FROM variants WHERE service_id=?"
        if not include_hidden:
            q += " AND hidden=0"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in conn.execute(q, (service_id,)).fetchall()]


def get_variant(variant_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
        return dict(row) if row else None


def add_variant(service_id, name_ar, name_en, details_ar, details_en, price_egp, stock=-1, requires_email=0,
                 requires_link=0):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO variants
               (service_id, name_ar, name_en, details_ar, details_en, price_egp, stock, requires_email, requires_link)
               VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
            (service_id, name_ar, name_en, details_ar, details_en, price_egp, stock, requires_email, requires_link),
        )
        return cur.lastrowid


def delete_variant(variant_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM variants WHERE id=?", (variant_id,))


def update_variant_field(variant_id: int, field: str, value):
    allowed = {"name_ar", "name_en", "details_ar", "details_en", "price_egp", "stock", "requires_email",
               "requires_link", "hidden", "api_service_id"}
    if field not in allowed:
        raise ValueError("field not allowed")
    with get_conn() as conn:
        conn.execute(f"UPDATE variants SET {field}=? WHERE id=?", (value, variant_id))


def adjust_variant_stock(variant_id: int, delta: int):
    with get_conn() as conn:
        row = conn.execute("SELECT stock FROM variants WHERE id=?", (variant_id,)).fetchone()
        if row is None or row["stock"] < 0:
            return  # unlimited stock, nothing to track
        conn.execute("UPDATE variants SET stock = GREATEST(stock + ?, 0) WHERE id=?", (delta, variant_id))


def set_variant_stock(variant_id: int, stock: int):
    """Set stock to an absolute value - used by api_sync.py to mirror the
    API's reported quantity exactly, instead of nudging it by a delta."""
    with get_conn() as conn:
        conn.execute("UPDATE variants SET stock=? WHERE id=?", (stock, variant_id))


def list_api_linked_variants():
    """All variants linked to an xprostore.store service (api_service_id set),
    for the stock-sync job. Includes hidden ones so their stock stays correct
    if they're ever unhidden."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM variants WHERE api_service_id IS NOT NULL AND api_service_id != ''"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------- Orders ----------------

def create_order(user_id, service_id, variant_id, service_name_ar, price_egp, email=None, link=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO orders (user_id, service_id, variant_id, service_name_ar, price_egp, email, link, created_at, status)
               VALUES (?,?,?,?,?,?,?,?, 'in_progress') RETURNING id""",
            (user_id, service_id, variant_id, service_name_ar, price_egp, email, link, int(time.time())),
        )
        conn.execute("UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE id=?",
                     (price_egp, user_id))
        return cur.lastrowid


def list_orders_for_user(user_id, limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_order(order_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return dict(row) if row else None


def set_order_status(order_id, status):
    with get_conn() as conn:
        delivered_at = int(time.time()) if status == "delivered" else None
        if delivered_at:
            conn.execute("UPDATE orders SET status=?, delivered_at=? WHERE id=?", (status, delivered_at, order_id))
        else:
            conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        if status == "delivered":
            order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            conn.execute("UPDATE users SET completed_orders = completed_orders + 1 WHERE id=?", (order["user_id"],))


def refund_order(order_id):
    """Cancel an order and return the amount to the customer's balance."""
    with get_conn() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order or order["status"] == "refunded":
            return None
        conn.execute("UPDATE orders SET status='refunded' WHERE id=?", (order_id,))
        conn.execute("UPDATE users SET balance = balance + ?, total_spent = total_spent - ? WHERE id=?",
                     (order["price_egp"], order["price_egp"], order["user_id"]))
        return dict(order)


def set_order_api_info(order_id, api_order_id=None, api_status=None, idempotency_key=None, note=None):
    """Records the result of dispatching an order to the xprostore.store API
    (or the fact that it was attempted) so every attempt is auditable from
    the admin bot, whatever the outcome."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE orders SET
                 api_order_id = COALESCE(?, api_order_id),
                 api_status = COALESCE(?, api_status),
                 idempotency_key = COALESCE(?, idempotency_key),
                 note = COALESCE(?, note)
               WHERE id=?""",
            (api_order_id, api_status, idempotency_key, note, order_id),
        )


def list_pending_api_orders():
    """Orders dispatched to the API that aren't in a final state yet, for
    api_sync.py to poll and reconcile."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM orders
               WHERE api_order_id IS NOT NULL
                 AND status NOT IN ('delivered', 'refunded')
               ORDER BY id"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_api_catalog_snapshot() -> dict:
    """The last-seen state of the full xprostore.store catalog, keyed by
    their service id, so api_sync.py can diff against it and tell you
    what's new/removed/changed - not just for services you've linked."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM api_catalog_snapshot").fetchall()
        return {r["api_service_id"]: dict(r) for r in rows}


def save_api_catalog_snapshot(entries: list):
    """Replaces the snapshot with the current catalog state. `entries` is a
    list of dicts with keys: api_service_id, name, description,
    price_amount, price_currency, is_active."""
    now = int(time.time())
    with get_conn() as conn:
        seen_ids = []
        for e in entries:
            seen_ids.append(str(e["api_service_id"]))
            conn.execute(
                """INSERT INTO api_catalog_snapshot
                       (api_service_id, name, description, price_amount, price_currency, is_active, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (api_service_id) DO UPDATE SET
                       name=EXCLUDED.name, description=EXCLUDED.description,
                       price_amount=EXCLUDED.price_amount, price_currency=EXCLUDED.price_currency,
                       is_active=EXCLUDED.is_active, last_seen=EXCLUDED.last_seen""",
                (str(e["api_service_id"]), e.get("name", ""), e.get("description", ""),
                 e.get("price_amount", ""), e.get("price_currency", ""), 1 if e.get("is_active", True) else 0, now),
            )
        if seen_ids:
            placeholders = ",".join(["?"] * len(seen_ids))
            conn.execute(f"DELETE FROM api_catalog_snapshot WHERE api_service_id NOT IN ({placeholders})",
                         tuple(seen_ids))


# ---------------- Top-ups ----------------

def create_topup(user_id, method, amount, reference):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO topups (user_id, method, amount, reference, created_at)
               VALUES (?,?,?,?,?) RETURNING id""",
            (user_id, method, amount, reference, int(time.time())),
        )
        return cur.lastrowid


def get_topup(topup_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
        return dict(row) if row else None


def resolve_topup(topup_id, approve: bool):
    with get_conn() as conn:
        t = conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
        if not t or t["status"] != "pending":
            return None
        status = "approved" if approve else "rejected"
        conn.execute("UPDATE topups SET status=?, resolved_at=? WHERE id=?", (status, int(time.time()), topup_id))
        if approve:
            conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (t["amount"], t["user_id"]))
        return dict(t)


# ---------------- Referrals ----------------

def maybe_pay_referral_bonus(referred_user_id: int, bonus_egp: float):
    """Call after a referred user's FIRST order is created. Pays their
    referrer once, and only once, per referred user."""
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (referred_user_id,)).fetchone()
        if not user or not user["referred_by"] or user["referral_bonus_paid"]:
            return False
        if user["total_orders"] != 1:
            return False  # only pay on the referred user's first-ever order
        conn.execute(
            "UPDATE users SET balance = balance + ?, referral_earnings = referral_earnings + ? WHERE id=?",
            (bonus_egp, bonus_egp, user["referred_by"]),
        )
        conn.execute("UPDATE users SET referral_bonus_paid=1 WHERE id=?", (referred_user_id,))
        return True


# ---------------- Sub-admins ----------------

def add_admin(telegram_id: int, role: str, added_by: int, username: str | None = None):
    """Add a sub-admin, or update their role/username if they already exist."""
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM admins WHERE telegram_id=?", (telegram_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE admins SET role=?, username=? WHERE telegram_id=?",
                (role, username, telegram_id),
            )
        else:
            conn.execute(
                "INSERT INTO admins (telegram_id, username, role, added_by, added_at) VALUES (?,?,?,?,?)",
                (telegram_id, username, role, added_by, int(time.time())),
            )


def remove_admin(telegram_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE telegram_id=?", (telegram_id,))


def get_admin(telegram_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM admins WHERE telegram_id=?", (telegram_id,)).fetchone()
        return dict(row) if row else None


def list_admins():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()
        return [dict(r) for r in rows]

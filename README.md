# Pro Market — Telegram shop bot + admin bot

Two linked bots sharing one SQLite database (`shop.db`), exactly as specced:
- **customer_bot.py** — the storefront your customers use.
- **admin_bot.py** — your control panel; only your `ADMIN_ID` can use it.

## ⚠️ Before anything else: rotate your tokens

The two bot tokens in the original spec document are no longer private —
that document has passed through multiple systems. **Open @BotFather in
Telegram, run `/revoke` on both bots, and generate new tokens** before you
launch this for real. Never paste live tokens into a shared document again;
put them in a `.env` file that stays on your server only.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: paste your two NEW tokens, your admin ID, wallet numbers, etc.
```

Run each bot in its own process (e.g. two `screen`/`tmux` sessions, or two
systemd services):

```bash
python3 customer_bot.py
python3 admin_bot.py
```

Both read/write `shop.db` in the same folder — that's how "new order" and
"new top-up" notifications reach you, and how "delivered/refund" and
"top-up approved/rejected" reach the customer, even though they're two
separate bot processes/tokens.

## What's implemented

**Customer bot:** categories → services → buy flow with automatic balance
check (redirects to Add Balance if short), email-collection step for
services that need it, order history with status, EGP/USD display toggle
(fixed 49 EGP = $1, editable in `.env`), Vodafone Cash / Binance Pay top-up
requests routed to the admin for manual approval, balance view, Arabic/English
language toggle, full profile screen, referral link + auto 10 EGP payout on
the invitee's first order, support contact.

**Admin bot:** direct + broadcast messaging, full category/service CRUD
(add/hide/delete new categories and services with no code changes, ever —
this was the non-negotiable part of the spec), top-up approve/reject with
automatic balance crediting, customer list with ban/unban and manual balance
adjustment, stock +/- controls, one-tap order delivery confirmation and
cancel-with-refund from the same notification message, and sub-admins with
scoped permissions (see below).

### Sub-admins (limited-permission admins)

Only you (the `ADMIN_ID` in `.env`) are the **owner** — you always have full
access and don't need to be added anywhere. From the main menu, tap **👤
إدارة المشرفين** to add another Telegram account as a sub-admin.

Right now sub-admins get a single role, **"إدارة الخدمات" (services)**:
they can open the bot with `/start` and get a stripped-down menu with only
**🗂️ إدارة الأصناف**, where they can add/edit/hide/delete categories,
products, and variants (name, price, stock, details) — but they can't
message customers, see the customer list, adjust balances, touch
orders/top-ups, or manage other admins. That's enforced in `admin_bot.py`
(see `ROLE_PERMISSIONS`), not just hidden in the UI — a sub-admin who
somehow sends a `users:` or `admins:` callback gets rejected server-side.

To remove a sub-admin, open **👤 إدارة المشرفين** and tap the ❌ next to
their entry. Sub-admins are stored in the `admins` table in `shop.db`
(telegram_id, role, who added them, when) — nothing to configure in `.env`.

Need a different permission mix later (e.g. an admin who can approve
top-ups but not touch pricing)? Add a new role key to `ROLE_PERMISSIONS` in
`admin_bot.py` with its own allowed callback/text prefixes, and offer it as
a choice when adding the sub-admin — the plumbing (DB table, decorator,
menu-by-role) is already there to support more than one role.

### Menu icons (the "app-logo" look, like other stores' bots)

Category and product buttons can now show a small icon before their text —
the same look you see on bots like X Pro Store. This uses Telegram's
`icon_custom_emoji_id` button field (Bot API 9.4+), so `requirements.html`
now pins `python-telegram-bot>=22.8` (the old pin, `21.6`, doesn't have
this field at all — and had a typo, `python-tebinary-bot`, that would've
made `pip install -r requirements.txt` fail outright; fixed).

**Hard requirement, not optional:** button icons only render if **the bot
owner's personal Telegram account** (the human behind `ADMIN_ID`) has an
active **Telegram Premium** subscription, or the bot has a username
purchased on Fragment. Without that, any icon you set is silently ignored
by Telegram and the button just shows plain text — this is a Telegram-side
restriction, nothing in the code can work around it.

**To set an icon:** from **🗂️ إدارة الأصناف**, open a category → **🖼️
أيقونة الصنف**, or open a product → **🖼️ تعديل الأيقونة**. When adding a
new category/product you're asked for the icon right after the name. Send
the custom emoji by itself (tap it from the emoji panel's "premium" tab,
or forward a message that contains it) — plain/standard emoji won't work
here since they don't have a `custom_emoji_id`; those still go in the
regular `emoji` field for categories. Send "تخطي" to skip/clear it.

## Data safety

All state lives in `shop.db`, entirely separate from the bot code (per your
non-negotiable requirement). Restarting, redeploying, or crashing either bot
process never touches customer balances or order history. Set up a simple
cron job to copy `shop.db` somewhere safe on a schedule, e.g.:

```bash
0 * * * * cp /path/to/shop.db /path/to/backups/shop-$(date +\%Y\%m\%d\%H\%M).db
```

## xprostore.store API integration (auto-fulfillment)

Any variant can now be linked to a service on xprostore.store so it's
fulfilled automatically instead of by hand:

1. Put your API key in `.env` as `XPROSTORE_API_KEY` (see `.env.example`).
2. In the admin bot, open the variant → **🔗 ربط API** → send the
   xprostore.store service ID (or send a keyword like "جيميناي" and it'll
   suggest the closest matches with their IDs). Send "الغاء" to unlink and
   make it manual again. **Owner-only** - sub-admins with the "services"
   role can't link/unlink, since it moves real money through your
   xprostore.store wallet.
3. From then on, for that variant:
   - A customer purchase calls the API immediately and fulfills itself -
     nothing for you to do.
   - If the API call fails (your xprostore balance ran out, a network
     error, etc.), the customer is refunded automatically and told to try
     again shortly; you get an admin alert with the exact error so you can
     top up or fulfill it by hand if the service is otherwise available.
   - Stock shown to customers is kept in sync with xprostore.store's live
     quantity by `api_sync.py` (runs every `XPROSTORE_SYNC_INTERVAL`
     seconds, default 180) - **your price is never touched**, only the
     quantity.
   - Every attempt (success or failure) is recorded on the order itself
     (`api_order_id`, `api_status`, and a note on failure) and visible from
     the order screen in the admin bot, so nothing gets fulfilled twice or
     silently lost.
4. Variants you never link stay exactly as before - fully manual, admin
   delivers/refunds by hand.
5. `run_both.py` now also starts `api_sync.py` as a third supervised
   process; if you deploy the bots separately instead, start `api_sync.py`
   as its own worker/service too, or the stock-sync and order-reconciliation
   parts of the integration simply won't run.

Two things worth knowing:
- The exact path/shape for checking an individual order's status
  (`xprostore_api.get_order`) was written from the same REST convention as
  the other endpoints, since the full docs page requires login - if your
  account's docs show a different path there, that's the one function to
  adjust in `xprostore_api.py`.
- `requests` was added to `requirements.html` for this - install it (or
  redeploy on Railway, which reads that file automatically) before running.

## Notes / things worth knowing

- SQLite is fine at this scale; if you outgrow it, only `db.py` needs to
  change (swap the connection + placeholder style for Postgres/MySQL) —
  nothing in either bot file touches SQL directly.
- The admin bot's inline "add category / add service" flows use plain-text
  templates (shown when you tap the button) rather than a multi-step wizard,
  to keep the code simple and reliable — you can tighten this into a full
  step-by-step form later if you want friendlier data entry.
- Referral bonus pays out once, the first time the referred user places an
  order (matches بعدد إتمام المُحال أول طلب له"). If you actually want it
  tied to *delivery* rather than *placing* the order, that's a one-line
  change in `db.maybe_pay_referral_bonus`.

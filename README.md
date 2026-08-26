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
cancel-with-refund from the same notification message.

## Data safety

All state lives in `shop.db`, entirely separate from the bot code (per your
non-negotiable requirement). Restarting, redeploying, or crashing either bot
process never touches customer balances or order history. Set up a simple
cron job to copy `shop.db` somewhere safe on a schedule, e.g.:

```bash
0 * * * * cp /path/to/shop.db /path/to/backups/shop-$(date +\%Y\%m\%d\%H\%M).db
```

## Notes / things worth knowing

- SQLite is fine at this scale; if you outgrow it, only `db.py` needs to
  change (swap the connection + placeholder style for Postgres/MySQL) —
  nothing in either bot file touches SQL directly.
- The admin bot's inline "add category / add service" flows use plain-text
  templates (shown when you tap the button) rather than a multi-step wizard,
  to keep the code simple and reliable — you can tighten this into a full
  step-by-step form later if you want friendlier data entry.
- Referral bonus pays out once, the first time the referred user places an
  order (matches "بعد إتمام المُحال أول طلب له"). If you actually want it
  tied to *delivery* rather than *placing* the order, that's a one-line
  change in `db.maybe_pay_referral_bonus`.

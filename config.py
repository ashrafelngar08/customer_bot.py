"""
Central configuration. All secrets come from environment variables (loaded
from a local .env file via python-dotenv) - never hardcode tokens in source.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


CUSTOMER_BOT_TOKEN = _require("CUSTOMER_BOT_TOKEN")
ADMIN_BOT_TOKEN = _require("ADMIN_BOT_TOKEN")
ADMIN_ID = int(_require("ADMIN_ID"))

VODAFONE_CASH_NUMBER = os.getenv("VODAFONE_CASH_NUMBER", "01102394162")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "442725197")

USD_TO_EGP = float(os.getenv("USD_TO_EGP", "49"))
REFERRAL_BONUS_EGP = float(os.getenv("REFERRAL_BONUS_EGP", "10"))

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@X_XRaa")
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "https://t.me/AshrafMediaPro")

DATABASE_URL = _require("DATABASE_URL")

# xprostore.store API integration (optional - only needed for services you
# link via "🔗 ربط API" in the admin bot; unlinked services stay fully
# manual and none of this is required for the bot to run).
XPROSTORE_API_KEY = os.getenv("XPROSTORE_API_KEY", "")
XPROSTORE_BASE_URL = os.getenv("XPROSTORE_BASE_URL", "https://xprostore.store/api/v1")
# How often api_sync.py refreshes stock / polls order status, in seconds.
XPROSTORE_SYNC_INTERVAL = int(os.getenv("XPROSTORE_SYNC_INTERVAL", "180"))
# Alert the owner in the admin bot once the xprostore wallet balance drops
# below this amount (in whatever currency /me/wallet reports), so you can
# top it up before orders start failing.
XPROSTORE_LOW_BALANCE_ALERT = float(os.getenv("XPROSTORE_LOW_BALANCE_ALERT", "5"))
# The currency your xprostore.store wallet is actually funded in. Sent with
# every order so a service listed in a different currency (e.g. USDT) still
# gets paid from this wallet automatically, the same way it works when you
# buy manually from their own bot - it's just not implied by default on the
# reseller API the way it is in their UI.
XPROSTORE_WALLET_CURRENCY = os.getenv("XPROSTORE_WALLET_CURRENCY", "EGP")

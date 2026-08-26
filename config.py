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

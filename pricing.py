"""
The wallet always stores and moves money in EGP - this file only controls
how a price is *displayed* to a customer who chose USD. There is no
separate USD balance; it's a live conversion at render time, as specced.
"""
from config import USD_TO_EGP


def format_price(price_egp: float, currency: str) -> str:
    if currency == "usd":
        usd = price_egp / USD_TO_EGP
        return f"${usd:,.2f}"
    return f"{price_egp:,.2f} EGP"

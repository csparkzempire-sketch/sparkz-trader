"""
Approximate trading hours, so the robot doesn't treat a closed market as stale data.

Gold and FX trade from Sunday ~22:00 UTC to Friday ~21:00 UTC. Gold also pauses
for about an hour each day around 21:00-22:00 UTC. Crypto trades 24/7. These are
broker-typical approximations (holidays and DST shifts are not modelled); a
broker provider's own "tradeable" flag takes precedence when available.
"""

from __future__ import annotations

from datetime import datetime

from app.market.instruments import Instrument


def market_open(inst: Instrument, now: datetime) -> tuple[bool, str]:
    if inst.trades_24_7:
        return True, "trades 24/7"
    wd, hour = now.weekday(), now.hour + now.minute / 60  # Monday = 0
    if wd == 5 or (wd == 4 and hour >= 21) or (wd == 6 and hour < 22):
        return False, "weekend: market closed"
    if inst.symbol == "XAUUSD" and 21 <= hour < 22:
        return False, "daily gold break (about 21:00-22:00 UTC)"
    return True, "regular session"

"""Shared operational policy for live BTC/BRL market data."""

from __future__ import annotations

import os


MARKET_ASSET = os.getenv("MARKET_ASSET", "BTC/BRL")
MARKET_SYMBOL = os.getenv("MARKET_SYMBOL", "BTC-BRL")
MARKET_TIMEFRAME = os.getenv("MARKET_TIMEFRAME", "1m")

# Mercado Bitcoin may publish the latest requested 1-minute bucket several minutes
# behind wall time. Decisions run every 15 minutes, so a 20-minute ceiling rejects
# genuinely old data without treating normal publication gaps as an outage.
MARKET_DATA_MAX_AGE_SECONDS = int(os.getenv("MARKET_DATA_MAX_AGE_SECONDS", "1200"))
DECISION_INTERVAL_SECONDS = int(os.getenv("DECISION_INTERVAL_SECONDS", "900"))

# Exposure ceiling used by the live paper path. Kept here as the single source of
# truth so the runtime and the operator console cannot disagree: the RiskManager
# class default is 100.0, while the live pipeline constructs it with 80.0. A
# console that read the class default would advertise a limit the runtime does
# not enforce.
LIVE_MAX_EXPOSURE_PCT = float(os.getenv("LIVE_MAX_EXPOSURE_PCT", "80"))

if MARKET_DATA_MAX_AGE_SECONDS <= 0:
    raise ValueError("MARKET_DATA_MAX_AGE_SECONDS must be positive")
if DECISION_INTERVAL_SECONDS < 60:
    raise ValueError("DECISION_INTERVAL_SECONDS must be at least 60 seconds")
if not 0 < LIVE_MAX_EXPOSURE_PCT <= 100:
    raise ValueError("LIVE_MAX_EXPOSURE_PCT must be between 0 and 100")

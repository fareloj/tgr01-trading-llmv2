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

if MARKET_DATA_MAX_AGE_SECONDS <= 0:
    raise ValueError("MARKET_DATA_MAX_AGE_SECONDS must be positive")
if DECISION_INTERVAL_SECONDS < 60:
    raise ValueError("DECISION_INTERVAL_SECONDS must be at least 60 seconds")

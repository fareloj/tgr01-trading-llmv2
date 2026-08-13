"""Bounded episodic memory derived from prior audited decisions.

The memory deliberately excludes free-form LLM output. It contains only a
small whitelist of deterministic scenario fields and categorical tags so the
model can notice recent consistency without treating its own prose as market
evidence.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from backend.core import repository


DEFAULT_WINDOW_SECONDS = 2 * 60 * 60
DEFAULT_MAX_EPISODES = 8
MAX_WINDOW_SECONDS = 2 * 60 * 60
MAX_EPISODES = 8


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        value = default
    return min(maximum, max(minimum, value))


def _finite_number(value: Any, *, digits: int) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _load_snapshot(value: Any) -> dict:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _snapshot_sections(snapshot: dict) -> tuple[dict, dict, dict, dict]:
    """Normalize live payloads and the compact audit snapshot shape."""
    technical = snapshot.get("technical_context")
    if not isinstance(technical, dict):
        compact = snapshot.get("technical", {})
        technical = {
            "current_price": compact.get("current_price"),
            "rsi": {
                "value": compact.get("rsi_value"),
                "status": compact.get("rsi_status"),
            },
            "macd": {
                "histogram": compact.get("macd_histogram"),
                "status": compact.get("macd_status"),
            },
            "ema_crossover": {"status": compact.get("ema_status")},
        }
    health = snapshot.get("data_health", {})
    news_risk = snapshot.get("news_risk", {})
    portfolio = snapshot.get("portfolio_context")
    if not isinstance(portfolio, dict):
        portfolio = snapshot.get("portfolio", {})
    return technical, health, news_risk, portfolio


def _justification_tags(row: dict, snapshot: dict) -> list[str]:
    technical, health, news_risk, _ = _snapshot_sections(snapshot)
    tags: list[str] = []

    if health.get("is_market_data_stale"):
        tags.append("MARKET_STALE")
    if health.get("is_news_stale"):
        tags.append("NEWS_STALE")
    if news_risk.get("has_untrusted_instruction"):
        tags.append("UNTRUSTED_NEWS_INSTRUCTION")
    elif news_risk.get("has_negative_red_flag"):
        tags.append("NEGATIVE_NEWS_RISK")

    rsi_status = str(technical.get("rsi", {}).get("status") or "UNKNOWN")
    macd_status = str(technical.get("macd", {}).get("status") or "UNKNOWN")
    ema_status = str(technical.get("ema_crossover", technical.get("ema", {})).get("status") or "UNKNOWN")
    proposed = str(row.get("llm_action") or "UNKNOWN")
    final = str(row.get("action") or "HOLD")

    if proposed == "HOLD":
        tags.append("MODEL_ABSTAINED")
    elif final == "HOLD":
        tags.append("RISK_BLOCKED_DIRECTION")
    else:
        tags.append("RISK_APPROVED_DIRECTION")

    bullish = "BULLISH" in macd_status or "BULLISH" in ema_status
    bearish = "BEARISH" in macd_status or "BEARISH" in ema_status
    if (rsi_status == "OVERSOLD" and bearish) or (rsi_status == "OVERBOUGHT" and bullish):
        tags.append("TECHNICAL_CONFLICT")
    elif bullish and bearish:
        tags.append("TECHNICAL_CONFLICT")

    return tags[:3]


def _episode(row: dict, *, now: int) -> dict:
    snapshot = _load_snapshot(row.get("payload_snapshot_json"))
    technical, health, _, portfolio = _snapshot_sections(snapshot)
    timestamp = int(row.get("timestamp") or 0)
    rsi = technical.get("rsi", {})
    macd = technical.get("macd", {})
    ema = technical.get("ema_crossover", technical.get("ema", {}))
    return {
        "age_minutes": max(0, (now - timestamp) // 60),
        "proposed_action": str(row.get("llm_action") or "UNKNOWN")[:16],
        "conviction": _finite_number(row.get("llm_conviction"), digits=0),
        "risk_action": str(row.get("action") or "HOLD")[:16],
        "scenario": {
            "price": _finite_number(technical.get("current_price"), digits=2),
            "rsi": _finite_number(rsi.get("value"), digits=2),
            "rsi_status": str(rsi.get("status") or "UNKNOWN")[:32],
            "macd_status": str(macd.get("status") or "UNKNOWN")[:32],
            "ema_status": str(ema.get("status") or "UNKNOWN")[:32],
            "market_stale": bool(health.get("is_market_data_stale", True)),
            "news_stale": bool(health.get("is_news_stale", True)),
            "exposure_pct": _finite_number(
                portfolio.get("current_exposure_percentage"), digits=2
            ),
        },
        "justification_tags": _justification_tags(row, snapshot),
    }


def build_decision_memory(*, as_of_timestamp: int | None = None) -> dict:
    """Return recent, compact decision episodes without raw model text."""
    now = int(as_of_timestamp or time.time())
    window_seconds = _bounded_env_int(
        "DECISION_MEMORY_WINDOW_SECONDS",
        DEFAULT_WINDOW_SECONDS,
        minimum=60,
        maximum=MAX_WINDOW_SECONDS,
    )
    max_episodes = _bounded_env_int(
        "DECISION_MEMORY_MAX_EPISODES",
        DEFAULT_MAX_EPISODES,
        minimum=1,
        maximum=MAX_EPISODES,
    )
    base = {
        "schema_version": 1,
        "status": "OK",
        "window_minutes": window_seconds // 60,
        "episodes": [],
        "rules": {
            "chronological": True,
            "raw_model_text_excluded": True,
            "memory_is_not_market_evidence": True,
        },
    }
    try:
        rows = repository.get_trade_logs(
            since_timestamp=now - window_seconds,
            until_timestamp=now,
        )
        selected = rows[-max_episodes:]
        base["episodes"] = [_episode(row, now=now) for row in selected]
    except Exception:
        base["status"] = "UNAVAILABLE"
    return base

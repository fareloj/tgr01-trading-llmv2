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
MAX_SOURCE_ROWS = 64
ALLOWED_PROPOSED_ACTIONS = {"BUY", "SELL", "HOLD", "SKIPPED"}
ALLOWED_RISK_ACTIONS = {"BUY", "SELL", "HOLD"}
ALLOWED_RSI_STATUS = {"OVERSOLD", "OVERBOUGHT", "NEUTRAL", "UNKNOWN"}
ALLOWED_MACD_STATUS = {
    "BULLISH_EXPANDING",
    "BEARISH_EXPANDING",
    "BULLISH_DIVERGENCE",
    "BEARISH_DIVERGENCE",
    "NEUTRAL",
    "UNKNOWN",
}
ALLOWED_EMA_STATUS = {
    "BULLISH",
    "BEARISH",
    "BULLISH_CROSS",
    "BEARISH_CROSS",
    "UNKNOWN",
}


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


def _bounded_number(
    value: Any, *, digits: int, minimum: float, maximum: float
) -> float | None:
    number = _finite_number(value, digits=digits)
    if number is None or number < minimum or number > maximum:
        return None
    return number


def _enum(value: Any, allowed: set[str], default: str = "UNKNOWN") -> str:
    normalized = str(value or default).strip().upper()
    return normalized if normalized in allowed else default


def _strict_bool(value: Any, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


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

    rsi_status = _enum(technical.get("rsi", {}).get("status"), ALLOWED_RSI_STATUS)
    macd_status = _enum(technical.get("macd", {}).get("status"), ALLOWED_MACD_STATUS)
    ema_status = _enum(
        technical.get("ema_crossover", technical.get("ema", {})).get("status"),
        ALLOWED_EMA_STATUS,
    )
    proposed = _enum(row.get("llm_action"), ALLOWED_PROPOSED_ACTIONS)
    final = _enum(row.get("action"), ALLOWED_RISK_ACTIONS, default="HOLD")

    if proposed == "HOLD":
        tags.append("MODEL_ABSTAINED")
    elif proposed not in {"BUY", "SELL"}:
        tags.append("INVALID_AUDIT_ACTION")
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
    timestamp = int(row["timestamp"])
    if timestamp < now - MAX_WINDOW_SECONDS or timestamp > now:
        raise ValueError("episode timestamp outside the accepted memory window")
    rsi = technical.get("rsi", {})
    macd = technical.get("macd", {})
    ema = technical.get("ema_crossover", technical.get("ema", {}))
    return {
        "age_minutes": max(0, (now - timestamp) // 60),
        "repeat_count": 1,
        "proposed_action": _enum(row.get("llm_action"), ALLOWED_PROPOSED_ACTIONS),
        "conviction": _bounded_number(
            row.get("llm_conviction"), digits=0, minimum=0, maximum=100
        ),
        "risk_action": _enum(row.get("action"), ALLOWED_RISK_ACTIONS, default="HOLD"),
        "scenario": {
            "price": _bounded_number(
                technical.get("current_price"), digits=2, minimum=0, maximum=1_000_000_000
            ),
            "rsi": _bounded_number(rsi.get("value"), digits=2, minimum=0, maximum=100),
            "rsi_status": _enum(rsi.get("status"), ALLOWED_RSI_STATUS),
            "macd_status": _enum(macd.get("status"), ALLOWED_MACD_STATUS),
            "ema_status": _enum(ema.get("status"), ALLOWED_EMA_STATUS),
            "market_stale": _strict_bool(
                health.get("is_market_data_stale"), default=True
            ),
            "news_stale": _strict_bool(health.get("is_news_stale"), default=True),
            "exposure_pct": _bounded_number(
                portfolio.get("current_exposure_percentage"),
                digits=2,
                minimum=0,
                maximum=100,
            ),
        },
        "justification_tags": _justification_tags(row, snapshot),
    }


def _compact_repeated_episodes(episodes: list[dict]) -> list[dict]:
    compacted: list[dict] = []
    for episode in episodes:
        signature = {key: value for key, value in episode.items() if key not in {"age_minutes", "repeat_count"}}
        if compacted:
            previous = compacted[-1]
            previous_signature = {
                key: value
                for key, value in previous.items()
                if key not in {"age_minutes", "repeat_count"}
            }
            if signature == previous_signature:
                previous["repeat_count"] = min(999, int(previous["repeat_count"]) + 1)
                previous["age_minutes"] = episode["age_minutes"]
                continue
        compacted.append(episode)
    return compacted


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
        rows = repository.get_recent_trade_logs_window(
            since_timestamp=now - window_seconds,
            until_timestamp=now,
            limit=MAX_SOURCE_ROWS,
        )
        episodes = []
        for row in rows:
            try:
                episodes.append(_episode(row, now=now))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        base["episodes"] = _compact_repeated_episodes(episodes)[-max_episodes:]
    except Exception:
        base["status"] = "UNAVAILABLE"
    return base

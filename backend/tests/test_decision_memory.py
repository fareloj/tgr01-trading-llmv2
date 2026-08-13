import json

from backend.features.decision_memory import build_decision_memory
from backend.features import decision_memory


def _row(timestamp: int, *, action: str = "HOLD", reasoning: str = "RAW SECRET REASON"):
    snapshot = {
        "technical_context": {
            "current_price": 330123.456,
            "rsi": {"value": 21.234, "status": "OVERSOLD"},
            "macd": {"status": "BEARISH_EXPANDING"},
            "ema_crossover": {"status": "BEARISH"},
        },
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"current_exposure_percentage": 17.3999},
        "decision_memory": {"episodes": [{"must_not": "recurse"}]},
    }
    return {
        "timestamp": timestamp,
        "llm_action": action,
        "llm_conviction": 50,
        "llm_reasoning": reasoning,
        "llm_decision_brief": "RAW BRIEF",
        "action": "HOLD",
        "reasoning": "RAW RISK REASON",
        "payload_snapshot_json": json.dumps(snapshot),
    }


def test_memory_is_bounded_chronological_and_excludes_raw_text(monkeypatch):
    now = 10_000
    rows = [_row(now - 600 + index, action="BUY" if index % 2 else "HOLD") for index in range(12)]
    captured = {}

    def fake_get_trade_logs(**kwargs):
        captured.update(kwargs)
        return rows

    monkeypatch.setattr(decision_memory.repository, "get_trade_logs", fake_get_trade_logs)
    memory = build_decision_memory(as_of_timestamp=now)

    assert captured == {"since_timestamp": now - 7200, "until_timestamp": now}
    assert len(memory["episodes"]) == 8
    assert memory["episodes"][0]["age_minutes"] >= memory["episodes"][-1]["age_minutes"]
    serialized = json.dumps(memory)
    assert "RAW SECRET REASON" not in serialized
    assert "RAW BRIEF" not in serialized
    assert "RAW RISK REASON" not in serialized
    assert "must_not" not in serialized
    assert "llm_reasoning" not in serialized


def test_memory_scenario_and_justification_are_deterministic(monkeypatch):
    monkeypatch.setattr(
        decision_memory.repository,
        "get_trade_logs",
        lambda **kwargs: [_row(9_940, action="BUY")],
    )
    episode = build_decision_memory(as_of_timestamp=10_000)["episodes"][0]

    assert episode["proposed_action"] == "BUY"
    assert episode["risk_action"] == "HOLD"
    assert episode["scenario"] == {
        "price": 330123.46,
        "rsi": 21.23,
        "rsi_status": "OVERSOLD",
        "macd_status": "BEARISH_EXPANDING",
        "ema_status": "BEARISH",
        "market_stale": False,
        "news_stale": False,
        "exposure_pct": 17.4,
    }
    assert episode["justification_tags"] == [
        "RISK_BLOCKED_DIRECTION",
        "TECHNICAL_CONFLICT",
    ]


def test_memory_fails_safe_when_history_is_unavailable(monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(decision_memory.repository, "get_trade_logs", unavailable)
    memory = build_decision_memory(as_of_timestamp=10_000)

    assert memory["status"] == "UNAVAILABLE"
    assert memory["episodes"] == []
    assert memory["rules"]["memory_is_not_market_evidence"] is True


def test_memory_configuration_cannot_exceed_safety_caps(monkeypatch):
    monkeypatch.setenv("DECISION_MEMORY_WINDOW_SECONDS", "999999")
    monkeypatch.setenv("DECISION_MEMORY_MAX_EPISODES", "999")
    monkeypatch.setattr(decision_memory.repository, "get_trade_logs", lambda **kwargs: [])

    memory = build_decision_memory(as_of_timestamp=10_000)

    assert memory["window_minutes"] == 120
    assert memory["episodes"] == []


def test_memory_reads_compact_audit_snapshot(monkeypatch):
    compact = {
        "technical": {
            "current_price": 329000.0,
            "rsi_value": 44.0,
            "rsi_status": "NEUTRAL",
            "macd_status": "BULLISH_EXPANDING",
            "ema_status": "BULLISH",
        },
        "data_health": {"is_market_data_stale": False, "is_news_stale": True},
        "news_risk": {"has_negative_red_flag": False},
        "portfolio": {"current_exposure_percentage": 12.5},
    }
    row = _row(9_940, action="BUY")
    row["payload_snapshot_json"] = json.dumps(compact)
    monkeypatch.setattr(decision_memory.repository, "get_trade_logs", lambda **kwargs: [row])

    episode = build_decision_memory(as_of_timestamp=10_000)["episodes"][0]

    assert episode["scenario"] == {
        "price": 329000.0,
        "rsi": 44.0,
        "rsi_status": "NEUTRAL",
        "macd_status": "BULLISH_EXPANDING",
        "ema_status": "BULLISH",
        "market_stale": False,
        "news_stale": True,
        "exposure_pct": 12.5,
    }
    assert episode["justification_tags"] == ["NEWS_STALE", "RISK_BLOCKED_DIRECTION"]


def test_invalid_environment_values_fall_back_safely(monkeypatch):
    monkeypatch.setenv("DECISION_MEMORY_WINDOW_SECONDS", "invalid")
    monkeypatch.setenv("DECISION_MEMORY_MAX_EPISODES", "invalid")
    monkeypatch.setattr(decision_memory.repository, "get_trade_logs", lambda **kwargs: [])

    memory = build_decision_memory(as_of_timestamp=10_000)

    assert memory["window_minutes"] == 120
    assert memory["status"] == "OK"

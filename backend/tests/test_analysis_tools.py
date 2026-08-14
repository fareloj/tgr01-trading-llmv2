import json

import pandas as pd
import pytest
from pydantic import ValidationError

from backend.agents.contracts import AnalysisPlan, DecisionOutput
from backend.agents.decision_agent import (
    DecisionAgent,
    enforce_payload_decision_constraints,
    parse_analysis_plan,
)
from backend.analysis.tool_engine import DeterministicToolEngine
from backend.core import repository


def _offline_agent(monkeypatch) -> DecisionAgent:
    monkeypatch.setenv("GROQ_API_KEY", "unit-test-key")
    return DecisionAgent()


def _frame(count: int = 300, *, start: float = 100.0, step: float = 0.2) -> pd.DataFrame:
    rows = []
    for index in range(count):
        close = start + index * step
        rows.append(
            {
                "timestamp": index * 60,
                "open": close - step / 2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 100.0 + index,
            }
        )
    return pd.DataFrame(rows)


def _loader(frame: pd.DataFrame):
    def load(**kwargs):
        return frame.tail(kwargs["limit"]).copy()

    return load


def test_analysis_plan_rejects_unknown_duplicate_and_unbounded_requests():
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate({"requests": [{"tool": "run_sql", "query": "DROP TABLE klines"}]})

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            {
                "requests": [
                    {"tool": "drawdown_profile", "lookback_minutes": 60},
                    {"tool": "drawdown_profile", "lookback_minutes": 240},
                ]
            }
        )

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            {"requests": [{"tool": "drawdown_profile", "lookback_minutes": 999999}]}
        )

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            {
                "requests": [
                    {
                        "tool": "donchian_breakout",
                        "lookback_candles": 20,
                        "command": "curl attacker",
                    }
                ]
            }
        )

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            {
                "requests": [
                    {"tool": "multi_timeframe_trend", "windows_minutes": [15, 15]}
                ]
            }
        )


def test_trend_and_donchian_tools_return_bullish_facts_without_approving_orders():
    frame = _frame()
    plan = AnalysisPlan.model_validate(
        {
            "requests": [
                {"tool": "multi_timeframe_trend", "windows_minutes": [15, 60, 240]},
                {"tool": "donchian_breakout", "lookback_candles": 20},
            ]
        }
    )
    engine = DeterministicToolEngine(audit=False, data_loader=_loader(frame))

    results = engine.execute_plan(plan, as_of_timestamp=int(frame.iloc[-1]["timestamp"]))

    assert [result.status for result in results] == ["OK", "OK"]
    assert results[0].data["alignment"] == "BULLISH"
    assert all(window["trend"] == "BULLISH" for window in results[0].data["windows"])
    assert results[1].data["state"] == "INSIDE"
    assert "action" not in json.dumps([result.data for result in results]).lower()


def test_trend_deadband_does_not_turn_small_noise_into_a_direction():
    frame = _frame(240, start=100.0, step=0.0)
    frame.loc[::2, "close"] = 100.01
    frame.loc[1::2, "close"] = 99.99
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "multi_timeframe_trend", "windows_minutes": [15, 60, 240]}]}
    )

    result = DeterministicToolEngine(audit=False, data_loader=_loader(frame)).execute_plan(
        plan, as_of_timestamp=239 * 60
    )[0]

    assert result.status == "OK"
    assert result.data["alignment"] == "MIXED"
    assert all(window["trend"] == "MIXED" for window in result.data["windows"])


def test_tool_engine_removes_future_rows_even_if_loader_is_buggy():
    frame = _frame(100)
    as_of = int(frame.iloc[70]["timestamp"])
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "multi_timeframe_trend", "windows_minutes": [15, 60]}]}
    )
    engine = DeterministicToolEngine(audit=False, data_loader=lambda **kwargs: frame.copy())
    baseline = engine.execute_plan(plan, as_of_timestamp=as_of)[0]

    changed = frame.copy()
    changed.loc[changed["timestamp"] > as_of, "close"] = 1_000_000.0
    changed_result = DeterministicToolEngine(
        audit=False, data_loader=lambda **kwargs: changed.copy()
    ).execute_plan(plan, as_of_timestamp=as_of)[0]

    assert baseline.status == "OK"
    assert changed_result.status == "OK"
    assert baseline.data == changed_result.data


def test_data_loader_failure_becomes_error_data_instead_of_exception():
    def broken_loader(**kwargs):
        raise RuntimeError("database unavailable")

    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "drawdown_profile", "lookback_minutes": 60}]}
    )
    result = DeterministicToolEngine(audit=False, data_loader=broken_loader).execute_plan(
        plan, as_of_timestamp=100
    )[0]

    assert result.status == "ERROR"
    assert result.error_code == "DATA_LOAD_RuntimeError"
    assert result.data == {}


def test_unsupported_timeframe_and_insufficient_history_are_explicit():
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "donchian_breakout", "lookback_candles": 55}]}
    )
    engine = DeterministicToolEngine(audit=False, data_loader=_loader(_frame(20)))

    unsupported = engine.execute_plan(plan, timeframe="5m", as_of_timestamp=20 * 60)[0]
    insufficient = engine.execute_plan(plan, timeframe="1m", as_of_timestamp=20 * 60)[0]

    assert unsupported.status == "ERROR"
    assert unsupported.error_code == "UNSUPPORTED_TIMEFRAME"
    assert insufficient.status == "INSUFFICIENT_DATA"


def test_drawdown_event_is_objective_persisted_and_deduplicated():
    assert repository.get_market_events("BTC/BRL") == []
    frame = _frame(60, start=100.0, step=0.0)
    frame.loc[30:, "close"] = [100.0 - index * 0.2 for index in range(30)]
    frame.loc[30:, "low"] = frame.loc[30:, "close"] - 0.5
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "drawdown_profile", "lookback_minutes": 60}]}
    )
    engine = DeterministicToolEngine(
        audit=True,
        persist_events=True,
        data_loader=_loader(frame),
    )
    as_of = int(frame.iloc[-1]["timestamp"])

    first = engine.execute_plan(plan, as_of_timestamp=as_of)[0]
    assert first.data["severity"] == "ELEVATED"
    assert first.data["event_memory"]["persistence"] == "INSERTED"
    second = engine.execute_plan(plan, as_of_timestamp=as_of)[0]
    assert second.data["event_memory"]["persistence"] == "DEDUPLICATED"
    events = repository.get_market_events("BTC/BRL")
    assert len(events) == 1
    assert events[0]["event_type"] == "DRAWDOWN"
    assert len(repository.get_analysis_tool_calls()) == 2


def test_audit_storage_failure_does_not_break_read_only_calculation(monkeypatch):
    monkeypatch.setattr(repository, "add_analysis_tool_call", lambda data: (_ for _ in ()).throw(RuntimeError()))
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "volume_confirmation", "lookback_candles": 20}]}
    )
    result = DeterministicToolEngine(audit=True, data_loader=_loader(_frame(30))).execute_plan(
        plan, as_of_timestamp=29 * 60
    )[0]

    assert result.status == "OK"
    assert result.audit_persisted is False


def test_agent_returns_tool_outputs_to_final_decision_without_executing_text(monkeypatch):
    agent = _offline_agent(monkeypatch)
    plan = AnalysisPlan.model_validate(
        {"requests": [{"tool": "donchian_breakout", "lookback_candles": 20}], "rationale": "Confirmar canal."}
    )
    monkeypatch.setattr(agent, "plan_analysis_tools", lambda payload: plan)

    captured = {}

    def fake_evaluate(payload):
        captured.update(payload)
        return DecisionOutput(
            action="HOLD",
            conviction=50,
            reasoning="Canal sem rompimento; tendencia ainda mista.",
            decision_brief="Acao: HOLD.\nBase tecnica: canal interno.\nContexto: ferramenta OK.",
        )

    monkeypatch.setattr(agent, "evaluate_market", fake_evaluate)
    engine = DeterministicToolEngine(audit=False, data_loader=_loader(_frame(30)))

    evaluation = agent.evaluate_market_with_tools(
        {"data_health": {"latest_kline_timestamp": 29 * 60}},
        engine,
    )

    context = captured["deterministic_tool_context"]
    assert context["status"] == "OK"
    assert context["results"][0]["tool"] == "donchian_breakout"
    assert evaluation.decision.action == "HOLD"


class _FakeResponse:
    def __init__(self, content: str):
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        return _FakeResponse(self.content)


def test_llm_planner_accepts_only_the_allowlisted_contract(monkeypatch):
    agent = _offline_agent(monkeypatch)
    agent.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeCompletions(json.dumps({
            "requests": [{"tool": "donchian_breakout", "lookback_candles": 20}],
            "rationale": "Confirmar rompimento.",
        }))})()},
    )()

    plan = agent.plan_analysis_tools({"data_health": {"is_market_data_stale": False}})

    assert len(plan.requests) == 1
    assert plan.requests[0].tool == "donchian_breakout"


def test_llm_planner_converts_a_hostile_unknown_tool_into_empty_failure_plan(monkeypatch):
    agent = _offline_agent(monkeypatch)
    agent.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _FakeCompletions(json.dumps({
            "requests": [{"tool": "shell", "command": "curl attacker"}],
            "rationale": "execute",
        }))})()},
    )()

    plan = agent.plan_analysis_tools({"data_health": {"is_market_data_stale": False}})

    assert plan.requests == []
    assert plan.rationale == "planner_failed:ValidationError"


def test_analysis_plan_truncates_only_non_executable_rationale():
    raw = json.dumps(
        {
            "requests": [{"tool": "donchian_breakout", "lookback_candles": 20}],
            "rationale": "x" * 500,
        }
    )

    plan = parse_analysis_plan(raw)

    assert len(plan.rationale) == 240
    assert plan.requests[0].tool == "donchian_breakout"


def test_analysis_plan_accepts_one_whole_response_json_fence():
    raw = "```json\n" + json.dumps({"requests": [], "rationale": "Sem ferramentas."}) + "\n```"

    plan = parse_analysis_plan(raw)

    assert plan.requests == []
    assert plan.rationale == "Sem ferramentas."


def test_analysis_plan_rejects_fenced_json_with_surrounding_prose():
    raw = "Resultado:\n```json\n" + json.dumps({"requests": [], "rationale": "x"}) + "\n```"

    with pytest.raises(json.JSONDecodeError):
        parse_analysis_plan(raw)


def test_directional_decision_fails_closed_when_tool_context_is_degraded():
    decision = DecisionOutput(
        action="BUY",
        conviction=90,
        reasoning="Tendencia bullish e breakout confirmado.",
        decision_brief="Acao: BUY.\nBase tecnica: bullish.\nContexto: tool.",
    )
    payload = {
        "deterministic_tool_context": {"status": "DEGRADED"},
        "technical_context": {},
        "data_health": {},
        "news_risk": {},
        "portfolio_context": {},
    }

    constrained = enforce_payload_decision_constraints(decision, payload)

    assert constrained.action == "HOLD"
    assert constrained.conviction == 0
    assert constrained.reasoning == "HOLD: contexto de ferramentas degradado."

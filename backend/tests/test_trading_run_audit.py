import json

import pytest

from backend.agents.contracts import DecisionOutput
from backend.core import repository
from backend.core.trading_run_audit import TradingRunAudit
import backend.main as trading_main


def _payload():
    return {
        "technical_context": {
            "current_price": 350000.0,
            "rsi": {"value": 45.0, "status": "NEUTRAL"},
            "macd": {"histogram": 10.0, "status": "BULLISH_EXPANDING"},
            "volatility_atr": 500.0,
        },
        "data_health": {
            "kline_age_seconds": 60,
            "news_age_seconds": 600,
            "is_market_data_stale": False,
            "is_news_stale": False,
        },
        "news_risk": {"risk_level": "NORMAL"},
        "portfolio_context": {"current_exposure_percentage": 5.0},
    }


def test_completed_run_persists_decision_risk_execution_and_trade_link(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    run = TradingRunAudit.start()
    decision = DecisionOutput(
        action="BUY",
        conviction=80,
        reasoning="Momentum tecnico confirmado.",
        decision_brief="Acao: BUY\nBase tecnica: MACD bullish\nContexto: dados frescos",
    )

    with run:
        run.mark_stage("payload")
        run.capture_payload(_payload())
        run.mark_stage("llm")
        run.capture_llm(decision)
        run.mark_stage("risk")
        run.capture_risk({"action": "BUY", "reason": "Aprovado", "executed_size": 5.0})
        run.mark_stage("execution")
        run.complete(trade_log_id=42, execution_audit={"fee_brl": 1.25})

    row = repository.get_trading_run(run.run_id)
    assert row["status"] == "COMPLETED"
    assert row["stage"] == "execution"
    assert row["model"] == "test-model"
    assert row["llm_called"] is True
    assert row["llm_action"] == "BUY"
    assert row["risk_action"] == "BUY"
    assert row["trade_log_id"] == 42
    assert json.loads(row["execution_audit_json"])["fee_brl"] == 1.25
    assert row["duration_ms"] >= 0


def test_aborted_run_records_reason_without_llm_call():
    run = TradingRunAudit.start()

    with run:
        run.mark_stage("worker_preflight")
        run.abort("news_worker stale")

    row = repository.get_trading_run(run.run_id)
    assert row["status"] == "ABORTED"
    assert row["llm_called"] is False
    assert row["risk_action"] == "HOLD"
    assert row["risk_reason"] == "news_worker stale"


def test_failed_run_records_exception_without_swallowing_it():
    run = TradingRunAudit.start()

    with pytest.raises(ValueError, match="falha controlada"):
        with run:
            run.mark_stage("llm")
            raise ValueError("falha controlada")

    row = repository.get_trading_run(run.run_id)
    assert row["status"] == "FAILED"
    assert row["stage"] == "llm"
    assert row["error_type"] == "ValueError"
    assert row["error_message"] == "falha controlada"


def test_runtime_worker_abort_creates_end_to_end_run(monkeypatch):
    monkeypatch.setattr(trading_main, "init_db", lambda: None)
    monkeypatch.setattr(trading_main, "print_db_diagnostics", lambda: None)
    monkeypatch.setattr(trading_main, "_workers_are_healthy", lambda: False)

    assert trading_main.run_trading_cycle() is False

    latest = repository.get_trading_runs(limit=1)[0]
    assert latest["status"] == "ABORTED"
    assert latest["stage"] == "worker_preflight"
    assert latest["risk_action"] == "HOLD"
    assert latest["trade_log_id"] is None

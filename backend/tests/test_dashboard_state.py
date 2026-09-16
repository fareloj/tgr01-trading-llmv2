import json
import time
from pathlib import Path

from backend.core import database
from backend.core.db_models import (
    equity_snapshots,
    klines,
    paper_position_reconciliations,
    paper_position_state,
    rag_chunks,
    rag_documents,
    rag_retrieval_logs,
    virtual_portfolio,
)
from backend.tests import dashboard_state


def _stub_clock(monkeypatch):
    monkeypatch.setattr(
        dashboard_state,
        "check_clock_skew",
        lambda timeout: {"status": "OK", "skew_seconds": 0, "max_skew_seconds": 300},
    )
    monkeypatch.setattr(
        dashboard_state,
        "get_external_rag_health",
        lambda: {"status": "ready", "reachable": True, "dense_indexed": 10, "lexical_indexed": 10},
    )


def test_dashboard_state_uses_postgresql_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", tmp_path / "missing-reports")
    _stub_clock(monkeypatch)

    state = dashboard_state.fetch_dashboard_state()

    assert state["database"]["backend"] == "PostgreSQL"
    assert "***" in state["database"]["label"]
    assert state["rag"] == {"documents": 0, "chunks": 0, "retrievals": 0}
    assert state["external_rag"]["status"] == "ready"
    assert state["reports"] == []
    assert state["portfolio"]["equity_brl"] == 10000
    assert state["position"] is None


def test_dashboard_state_counts_rag_and_reports(tmp_path, monkeypatch):
    now = int(time.time())
    with database.engine.begin() as conn:
        document_id = conn.execute(
            rag_documents.insert().values(
                source_type="test",
                source="test.md",
                title="Test",
                content_hash="dashboard-test",
                created_at=now,
                metadata_json="{}",
            )
        ).inserted_primary_key[0]
        conn.execute(
            rag_chunks.insert().values(
                document_id=document_id,
                chunk_index=0,
                text="content",
                token_estimate=2,
                metadata_json="{}",
            )
        )
        conn.execute(
            rag_retrieval_logs.insert().values(
                timestamp=now,
                purpose="test",
                query="content",
                filters_json="{}",
                selected_chunk_ids_json=json.dumps([]),
            )
        )
        conn.execute(
            klines.insert().values(
                asset="BTC/BRL",
                timeframe="1m",
                timestamp=now,
                open=100,
                high=100,
                low=100,
                close=100,
                volume=1,
            )
        )
        conn.execute(
            paper_position_state.insert().values(
                asset="BTC/BRL",
                quantity=0.01,
                avg_cost_brl=90000,
                realized_pnl_brl=15,
                updated_at=now,
            )
        )
        conn.execute(
            equity_snapshots.insert().values(
                timestamp=now,
                asset="BTC/BRL",
                mark_price=100,
                brl_balance=10000,
                btc_balance=0,
                equity_brl=10000,
                source="pytest",
            )
        )
        conn.execute(
            paper_position_reconciliations.insert().values(
                asset="BTC/BRL",
                timestamp=now,
                method="legacy_trade_log_replay_v1",
                initial_brl=10000,
                initial_btc=0,
                reconstructed_brl=9000,
                reconstructed_btc=0.01,
                observed_brl=9000,
                observed_btc=0.01,
                avg_cost_brl=90000,
                realized_pnl_brl=15,
                source_log_ids_json="[7]",
                details_json="{}",
            )
        )

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dashboard_state, "REPORTS_DIR", reports_dir)
    _stub_clock(monkeypatch)

    state = dashboard_state.fetch_dashboard_state()

    assert state["rag"] == {"documents": 1, "chunks": 1, "retrievals": 1}
    assert state["reports"][0]["name"] == "report.json"
    assert state["position"]["avg_cost_brl"] == 90000
    assert state["portfolio"]["daily_reference_equity_brl"] == 10000
    assert state["portfolio"]["daily_drawdown_pct"] == 0
    assert state["position"]["reconciliation"] == {
        "id": 1,
        "timestamp": now,
        "method": "legacy_trade_log_replay_v1",
        "source_log_ids": [7],
    }


def _risk_payload(*, news_context=None, is_news_stale=False, macd_status="BULLISH_EXPANDING"):
    return {
        "technical_context": {
            "current_price": 100000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": macd_status},
            "volatility_atr": {"value": 500.0, "status": "NORMAL"},
        },
        "news_context": news_context if news_context is not None else [{"headline": "neutral"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": is_news_stale},
        "news_risk": {"has_negative_red_flag": False, "has_untrusted_instruction": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0, "daily_drawdown_percentage": 0.0},
    }


def test_execution_config_exposes_the_hurdles_the_console_shows():
    config = dashboard_state.build_execution_config()

    assert config["one_side_cost_pct"] == 0.35
    assert config["buy_round_trip_cost_pct"] == 0.70
    assert config["sell_exit_cost_pct"] == 0.35
    # BUY pays both sides; SELL only the exit.
    assert config["buy_round_trip_cost_pct"] == 2 * config["one_side_cost_pct"]
    assert config["sell_exit_cost_pct"] == config["one_side_cost_pct"]


def test_exposed_conviction_gate_matches_risk_manager_behaviour():
    """The console must not advertise a threshold the Risk Manager disagrees with.

    Checked behaviourally rather than by comparing literals, so changing the
    threshold in the Risk Manager fails this test instead of silently drifting.
    """
    gates = dashboard_state.build_risk_gates()
    manager = dashboard_state.RiskManager(max_exposure=80.0, cooldown_minutes=0)
    minimum = gates["minimum_conviction_pct"]

    below = manager.evaluate_order("BUY", minimum - 1, _risk_payload(), current_exposure=10.0)
    at = manager.evaluate_order("BUY", minimum, _risk_payload(), current_exposure=10.0)

    assert below["action"] == "HOLD"
    assert "insuficiente" in below["reason"]
    assert at["action"] == "BUY"


def test_exposed_no_news_gate_matches_risk_manager_behaviour():
    gates = dashboard_state.build_risk_gates()
    manager = dashboard_state.RiskManager(max_exposure=80.0, cooldown_minutes=0)
    minimum = gates["no_news_minimum_conviction_pct"]

    below = manager.evaluate_order(
        "BUY", minimum - 1, _risk_payload(news_context=[]), current_exposure=10.0
    )
    at = manager.evaluate_order(
        "BUY", minimum, _risk_payload(news_context=[]), current_exposure=10.0
    )

    assert below["action"] == "HOLD"
    assert "Noticias velhas/ausentes" in below["reason"]
    assert at["action"] == "BUY"


def test_exposed_drawdown_and_exposure_match_the_risk_manager_instance():
    gates = dashboard_state.build_risk_gates()
    manager = dashboard_state.RiskManager(max_exposure=dashboard_state.LIVE_MAX_EXPOSURE_PCT)

    assert gates["max_daily_drawdown_pct"] == manager.max_daily_drawdown
    assert gates["max_exposure_pct"] == manager.max_exposure
    assert gates["cooldown_minutes"] == manager.cooldown_minutes


def test_exposed_exposure_matches_the_live_pipeline_not_the_class_default():
    """The console must show the limit the runtime enforces, not the class default.

    main.py constructs RiskManager with LIVE_MAX_EXPOSURE_PCT (80), while the
    RiskManager class default is 100. Reading the default would advertise a
    ceiling 20 points higher than the one actually applied.
    """
    gates = dashboard_state.build_risk_gates()

    assert gates["max_exposure_pct"] == dashboard_state.LIVE_MAX_EXPOSURE_PCT
    assert gates["max_exposure_pct"] != dashboard_state.RiskManager().max_exposure
    assert dashboard_state.LIVE_MAX_EXPOSURE_PCT == 80


def test_hybrid_confidence_gate_matches_risk_manager_behaviour():
    """Behavioural check, so changing 0.50 in the Risk Manager fails this test."""
    gates = dashboard_state.build_risk_gates()
    threshold = gates["minimum_hybrid_confidence_pct"] / 100.0
    manager = dashboard_state.RiskManager(max_exposure=80.0, cooldown_minutes=0)

    # No news penalises reliability by 0.7 and stale adds 0.6, so a conviction
    # just below the threshold must be blocked and the advertised threshold must
    # be the one that separates blocked from approved.
    assert (
        manager.MINIMUM_HYBRID_CONFIDENCE
        == dashboard_state.RiskManager.MINIMUM_HYBRID_CONFIDENCE
    )
    assert round(threshold, 4) == dashboard_state.RiskManager.MINIMUM_HYBRID_CONFIDENCE

    no_news_payload = _risk_payload(news_context=[], is_news_stale=True)
    # conviction 70 -> reliability 0.42 -> hybrid 0.294, below any sane floor.
    blocked = manager.evaluate_order("BUY", 70, no_news_payload, current_exposure=10.0)
    assert blocked["action"] == "HOLD"


def test_exposed_conviction_constants_match_the_risk_manager_class():
    gates = dashboard_state.build_risk_gates()

    assert gates["minimum_conviction_pct"] == dashboard_state.RiskManager.MINIMUM_CONVICTION
    assert (
        gates["no_news_minimum_conviction_pct"]
        == dashboard_state.RiskManager.NO_NEWS_MINIMUM_CONVICTION
    )


def test_live_exposure_is_not_environment_configurable():
    """Raising the exposure ceiling must require a reviewed diff, not an env var.

    The console is read-only, but the ceiling it displays is a Risk Manager gate.
    If it were env-configurable, a value change could weaken that gate without a
    code review, contrary to the repository's sign-off rule.
    """
    import backend.core.market_policy as policy

    source = Path(policy.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("LIVE_MAX_EXPOSURE_PCT"' not in source
    assert "LIVE_MAX_EXPOSURE_PCT = 80.0" in source
    assert policy.LIVE_MAX_EXPOSURE_PCT <= policy._REVIEWED_MAX_EXPOSURE_CEILING


def test_freshness_policy_matches_the_pipeline_thresholds():
    """The console must judge freshness against the runtime's own ceilings."""
    from backend.core.market_policy import MARKET_DATA_MAX_AGE_SECONDS
    from backend.features.payload_builder import NEWS_STALE_SECONDS

    policy = dashboard_state.build_freshness_policy()

    assert policy["market_data_stale_threshold_seconds"] == MARKET_DATA_MAX_AGE_SECONDS
    assert policy["news_stale_threshold_seconds"] == NEWS_STALE_SECONDS
    assert policy["market_data_stale_threshold_seconds"] > 0
    assert policy["news_stale_threshold_seconds"] > 0


def test_missing_candle_is_none_not_a_zero_price():
    """An absent candle must not be reported as a 0.00 price.

    Zero is a legitimate close value, so the two cases have to be
    distinguishable or the console shows a fake price for missing data.
    """
    # The test DB is clean here, so there is no BTC/BRL kline.
    state = dashboard_state.fetch_dashboard_state()

    assert state["latest_kline"]["close"] is None
    assert state["latest_kline"]["timestamp"] is None
    assert state["latest_kline"]["age_seconds"] is None


def test_exposure_is_unknown_without_a_price_when_btc_is_held():
    """With BTC but no price, exposure must be unknown, not 0%.

    Reporting 0% would claim the account is unexposed while it actually holds
    BTC of unknown value.
    """
    now = int(time.time())
    with database.engine.begin() as conn:
        conn.execute(
            virtual_portfolio.update()
            .where(virtual_portfolio.c.currency == "BTC")
            .values(amount=0.01)
        )
        conn.execute(
            virtual_portfolio.update()
            .where(virtual_portfolio.c.currency == "BRL")
            .values(amount=1000)
        )

    state = dashboard_state.fetch_dashboard_state()

    assert state["latest_kline"]["close"] is None
    assert state["portfolio"]["exposure_pct"] is None
    assert state["portfolio"]["btc"] == 0.01


def test_execution_config_fails_closed_on_a_malformed_rate(monkeypatch):
    monkeypatch.setenv("PAPER_FEE_RATE", "not-a-number")

    config = dashboard_state.build_execution_config_safe()

    assert "error" in config
    assert "one_side_cost_pct" not in config


def test_model_roles_expose_no_credentials():
    roles = dashboard_state.build_model_roles()

    assert set(roles["roles"]) == {"news", "technical", "decision"}
    # Check the values, not the key names: "max_tokens" legitimately contains
    # "token" and is not a credential.
    values = json.dumps(roles, default=str).lower()
    for forbidden in ("api_key", "apikey", "secret", "bearer", "sk-", "gsk_"):
        assert forbidden not in values
    for role in roles["roles"].values():
        assert set(role) == {"model", "provider", "temperature", "max_tokens", "reasoning_effort"}
        assert role["provider"] in {"ollama", "ollama-cloud", "opencode-go"}
        assert role["model"]
        assert role["max_tokens"] > 0


def test_model_roles_report_an_invalid_configuration_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("NEWS_PROVIDER", "not-a-provider")

    roles = dashboard_state.build_model_roles()

    assert roles["enabled"] is False
    assert "unknown model provider" in roles["error"]
    assert roles["roles"] == {}

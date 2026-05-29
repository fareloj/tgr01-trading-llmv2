import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ["GROQ_API_KEY"] = ""

from core import database
from agents.contracts import DecisionOutput
from agents.decision_agent import load_api_keys, parse_retry_seconds, replace_generic_hold_reason
from features.payload_builder import build_agent_payload, build_news_risk
from main import is_llm_technical_failure
from risk.risk_manager import RiskManager


def _compatible_payload() -> dict:
    return {
        "technical_context": {
            "current_price": 40000,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": 100,
        },
        "news_context": [{"headline": "Mock headline"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False, "risk_level": "NORMAL", "matched_terms": [], "matched_headlines": []},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }


def _insert_candles(count: int, latest_age_seconds: int = 60):
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        timestamp = int(time.time()) - latest_age_seconds - ((count - 1) * 60)
        price = 100000.0
        for i in range(count):
            cursor.execute(
                """
                INSERT OR IGNORE INTO klines
                    (asset, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("BTC/BRL", "1m", timestamp, price, price + 100, price - 100, price + 10, 1.0),
            )
            timestamp += 60
            price += 5
        conn.commit()
    finally:
        conn.close()


def _insert_news(age_seconds: int = 0):
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO news (timestamp, headline, headline_hash, source)
            VALUES (?, ?, ?, ?)
            """,
            (int(time.time()) - age_seconds, "Mock headline segura", f"mock_headline_segura_{age_seconds}", "pytest"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_news_raw(timestamp: int, headline: str, source: str, headline_hash: str):
    conn = database.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO news (timestamp, headline, headline_hash, source)
            VALUES (?, ?, ?, ?)
            """,
            (timestamp, headline, headline_hash, source),
        )
        conn.commit()
    finally:
        conn.close()


def test_payload_blocks_before_30_klines():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_pytest_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        _insert_candles(29)

        payload = build_agent_payload()

        assert payload["status"] == "ERROR"
        assert payload["found_klines"] == 29
        assert payload["required_klines"] == 30
        assert payload["asset"] == "BTC/BRL"
        assert payload["timeframe"] == "1m"
        assert payload["db_path"] == str(database.get_db_path())
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_payload_allows_30_klines_and_keeps_schema():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_pytest_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        _insert_candles(30)
        _insert_news()

        payload = build_agent_payload()

        assert payload["technical_context"]["status"] == "OK"
        assert "current_price" in payload["technical_context"]
        assert len(payload["news_context"]) == 1
        assert payload["news_risk"]["has_negative_red_flag"] is False
        assert payload["data_health"]["is_market_data_stale"] is False
        assert payload["data_health"]["is_news_stale"] is False
        assert "current_exposure_percentage" in payload["portfolio_context"]
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_payload_ignores_news_too_far_in_future():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_pytest_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        _insert_candles(30)
        now = int(time.time())
        _insert_news_raw(now, "Noticia realista atual", "pytest", "current_news")
        _insert_news_raw(now + 3600, "Noticia venenosa do futuro", "pytest", "future_news")

        payload = build_agent_payload()

        headlines = [item["headline"] for item in payload["news_context"]]
        assert "Noticia realista atual" in headlines
        assert "Noticia venenosa do futuro" not in headlines
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_risk_manager_blocks_low_reliability_buy():
    payload = {
        "technical_context": {"volatility_atr": 5000, "current_price": 40000},
        "news_context": [{"headline": "Mock headline"}],
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["executed_size"] == 0.0


def test_risk_manager_blocks_stale_market_data():
    payload = {
        "technical_context": {"volatility_atr": 100, "current_price": 40000},
        "news_context": [{"headline": "Mock headline"}],
        "data_health": {
            "is_market_data_stale": True,
            "kline_age_seconds": 900,
            "market_data_stale_threshold_seconds": 300,
            "is_news_stale": False,
        },
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("SELL", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert "market data stale" in final_order["reason"]


def test_risk_manager_blocks_buy_with_stale_news():
    payload = {
        "technical_context": {"volatility_atr": 100, "current_price": 40000},
        "news_context": [{"headline": "Mock headline"}],
        "data_health": {
            "is_market_data_stale": False,
            "is_news_stale": True,
            "news_age_seconds": 30000,
            "news_stale_threshold_seconds": 21600,
        },
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: BUY bloqueado por noticias stale"


def test_payload_marks_stale_news_and_market_data():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_pytest_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        _insert_candles(30, latest_age_seconds=900)
        _insert_news(age_seconds=30000)

        payload = build_agent_payload()

        assert payload["technical_context"]["status"] == "OK"
        assert payload["data_health"]["is_market_data_stale"] is True
        assert payload["data_health"]["is_news_stale"] is True
        assert payload["data_health"]["kline_age_seconds"] >= 900
        assert payload["data_health"]["news_age_seconds"] >= 30000
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_directional_gate_blocks_buy_with_rsi_overbought():
    payload = _compatible_payload()
    payload["technical_context"]["rsi"]["status"] = "OVERBOUGHT"
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: BUY bloqueado por RSI OVERBOUGHT"


def test_directional_gate_blocks_buy_with_bearish_macd():
    payload = _compatible_payload()
    payload["technical_context"]["macd"]["status"] = "BEARISH_EXPANDING"
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: BUY bloqueado por MACD BEARISH_EXPANDING"


def test_directional_gate_blocks_sell_with_rsi_oversold():
    payload = _compatible_payload()
    payload["technical_context"]["rsi"]["status"] = "OVERSOLD"
    payload["technical_context"]["macd"]["status"] = "BEARISH_EXPANDING"
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("SELL", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: SELL bloqueado por RSI OVERSOLD"


def test_directional_gate_blocks_sell_with_bullish_macd():
    payload = _compatible_payload()
    payload["technical_context"]["macd"]["status"] = "BULLISH_EXPANDING"
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("SELL", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: SELL bloqueado por MACD BULLISH_EXPANDING"


def test_hold_is_always_allowed():
    payload = _compatible_payload()
    payload["technical_context"]["rsi"]["status"] = "OVERBOUGHT"
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("HOLD", 100, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "LLM sugeriu HOLD."


def test_invalid_llm_action_is_distinguished_from_hold():
    payload = _compatible_payload()
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("WAIT", 100, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "LLM sugeriu acao invalida: WAIT"


def test_buy_with_compatible_context_can_pass_to_next_rules():
    payload = _compatible_payload()
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "BUY"
    assert final_order["executed_size"] > 0


def test_cooldown_blocks_repeated_buy():
    original_db_path = database.DB_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="tgr01_pytest_"))
    try:
        database.DB_PATH = (temp_dir / "trading_v2_test.db").resolve()
        database.init_db()
        conn = database.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trade_logs
                    (timestamp, llm_action, llm_reasoning, action, llm_conviction,
                     system_reliability, final_confidence, executed_size, execution_price, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(time.time()), "BUY", "prior buy", "BUY", 90, 1.0, 0.9, 5.0, 40000, "prior approved buy"),
            )
            conn.commit()
        finally:
            conn.close()

        payload = _compatible_payload()
        rm = RiskManager(max_exposure=80.0, cooldown_minutes=15)

        final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

        assert final_order["action"] == "HOLD"
        assert final_order["reason"] == "Cooldown: BUY repetido nos ultimos 15 minutos"
    finally:
        database.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_news_risk_detects_negative_red_flag():
    news = [
        {"headline": "Bitcoin sobe com forte volume institucional", "source": "pytest"},
        {"headline": "Rumores de proibicao regional de criptomoedas elevam incerteza", "source": "pytest"},
    ]

    risk = build_news_risk(news)

    assert risk["has_negative_red_flag"] is True
    assert risk["risk_level"] == "ELEVATED"
    assert "proibicao" in risk["matched_terms"]


def test_directional_gate_blocks_buy_with_news_red_flag():
    payload = _compatible_payload()
    payload["news_risk"] = {
        "has_negative_red_flag": True,
        "risk_level": "ELEVATED",
        "matched_terms": ["proibicao"],
        "matched_headlines": [],
    }
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    final_order = rm.evaluate_order("BUY", 90, payload, current_exposure=30.0)

    assert final_order["action"] == "HOLD"
    assert final_order["reason"] == "Directional Gate: BUY bloqueado por news red flag (proibicao)"


def test_generic_llm_hold_reason_is_replaced_with_specific_payload_reason():
    payload = _compatible_payload()
    payload["technical_context"]["macd"]["status"] = "BEARISH_EXPANDING"
    decision = DecisionOutput(action="HOLD", conviction=50, reasoning="Noticias confusas")

    updated = replace_generic_hold_reason(decision, payload)

    assert updated.action == "HOLD"
    assert updated.reasoning == "HOLD: RSI NEUTRAL; MACD BEARISH_EXPANDING; sem alinhamento direcional."


def test_specific_llm_hold_reason_is_preserved():
    payload = _compatible_payload()
    decision = DecisionOutput(action="HOLD", conviction=50, reasoning="HOLD: RSI NEUTRAL; MACD BEARISH_EXPANDING.")

    updated = replace_generic_hold_reason(decision, payload)

    assert updated.reasoning == decision.reasoning


def test_llm_technical_failure_is_distinguished_from_analytical_hold():
    decision = DecisionOutput(action="HOLD", conviction=0, reasoning="LLM technical failure: RateLimitError")

    assert is_llm_technical_failure(decision) is True


def test_zero_conviction_analytical_hold_is_not_technical_failure():
    decision = DecisionOutput(action="HOLD", conviction=0, reasoning="HOLD: RSI NEUTRAL; MACD NEUTRAL.")

    assert is_llm_technical_failure(decision) is False


def test_parse_retry_seconds_from_rate_limit_message():
    error = Exception("Rate limit reached. Please try again in 3m40.32s.")

    assert parse_retry_seconds(error) == 220


def test_load_api_keys_supports_list_and_numbered_vars(monkeypatch):
    for index in range(1, 11):
        monkeypatch.delenv(f"GROQ_API_KEY_{index}", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "key_a,key_b;key_a")
    monkeypatch.setenv("GROQ_API_KEY", "key_single")
    monkeypatch.setenv("GROQ_API_KEY_1", "key_1")
    monkeypatch.setenv("GROQ_API_KEY_2", "key_2")

    assert load_api_keys() == ["key_a", "key_b", "key_single", "key_1", "key_2"]

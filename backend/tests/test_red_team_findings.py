"""
RED TEAM REGRESSION TESTS — TGR-01 Trading LLM V2
====================================================
Cada teste aqui prova uma vulnerabilidade real no código atual.
Um teste que FALHA aqui (no código atual) significa que o bug está PRESENTE.
Um teste que PASSA significa que o comportamento está correto.

Guardrails:
- NUNCA reescrever um teste para fazê-lo passar.
- Testes em caminho de capital: diagnosticam, NÃO fixam inline.
- Output bruto do pytest é a única prova aceita.
"""
import ast
import sys
import time
import threading
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.core import database, repository
from backend.core.db_models import metadata


# ===========================================================================
# ACHADO 1 (CRÍTICO — Caminho de Capital)
# avg_cost corruption no _execute_buy quando btc_balance > position.quantity
# ===========================================================================

def test_rt_buy_aborts_before_mutation_when_position_is_out_of_sync():
    """BUY must fail closed when tracked position and BTC balance diverge."""
    from backend.execution.paper_simulator import execute_paper_order, PaperExecutionConfig

    with database.get_connection() as conn:
        # Saldo real: 0.1 BTC, mas posição rastreada: 0.05 BTC @ R$100k
        repository.update_virtual_portfolio("BRL", 5000.0, connection=conn)
        repository.update_virtual_portfolio("BTC", 0.1, connection=conn)
        repository.update_paper_position_state(
            asset="BTC/BRL",
            quantity=0.05,          # ← posição rastreada difere do saldo real
            avg_cost_brl=100000.0,
            realized_pnl_brl=0.0,
            updated_at=int(time.time()),
            connection=conn
        )

        payload = {
            "technical_context": {
                "current_price": 100000.0,
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            }
        }

        before_portfolio = repository.get_virtual_portfolio(connection=conn)
        before_position = repository.get_paper_position_state("BTC/BRL", connection=conn)

        with pytest.raises(RuntimeError, match="Estado de capital inconsistente"):
            execute_paper_order(
                connection=conn,
                action="BUY",
                executed_size_pct=5.0,
                current_price=100000.0,
                payload=payload,
                config=PaperExecutionConfig(
                    fee_rate=0.003,
                    min_slippage_rate=0.001,
                    max_slippage_rate=0.001,
                ),
            )

        assert repository.get_virtual_portfolio(connection=conn) == before_portfolio
        assert repository.get_paper_position_state("BTC/BRL", connection=conn) == before_position


# ===========================================================================
# ACHADO 2 (CRÍTICO — Caminho de Capital)
# _execute_sell: quantidade rastreada pode divergir do saldo real
# ===========================================================================

def test_rt_sell_aborts_before_mutation_when_position_is_out_of_sync():
    """SELL must not create ghost capital from an inconsistent position."""
    from backend.execution.paper_simulator import execute_paper_order, PaperExecutionConfig

    with database.get_connection() as conn:
        # Setup: saldo real 0.1 BTC, posição rastreada 0.12 BTC (divergência)
        repository.update_virtual_portfolio("BRL", 0.0, connection=conn)
        repository.update_virtual_portfolio("BTC", 0.1, connection=conn)
        repository.update_paper_position_state(
            asset="BTC/BRL",
            quantity=0.12,          # ← posição rastreada MAIOR que saldo real
            avg_cost_brl=90000.0,
            realized_pnl_brl=0.0,
            updated_at=int(time.time()),
            connection=conn
        )

        payload = {
            "technical_context": {
                "current_price": 100000.0,
                "volatility_atr": {"value": 100.0, "status": "NORMAL"},
            }
        }

        before_portfolio = repository.get_virtual_portfolio(connection=conn)
        before_position = repository.get_paper_position_state("BTC/BRL", connection=conn)

        with pytest.raises(RuntimeError, match="Estado de capital inconsistente"):
            execute_paper_order(
                connection=conn,
                action="SELL",
                executed_size_pct=100.0,
                current_price=100000.0,
                payload=payload,
                config=PaperExecutionConfig(
                    fee_rate=0.003,
                    min_slippage_rate=0.001,
                    max_slippage_rate=0.001,
                ),
            )

        assert repository.get_virtual_portfolio(connection=conn) == before_portfolio
        assert repository.get_paper_position_state("BTC/BRL", connection=conn) == before_position


# ===========================================================================
# ACHADO 3 (ALTO — Dual Import Path / Engine Divergente)
# 'core.database' e 'backend.core.database' são módulos DISTINTOS
# ===========================================================================

def test_rt_production_code_uses_only_backend_package_imports():
    """Production modules must not create duplicate top-level package identities."""
    forbidden_roots = {"core", "agents", "execution", "features", "risk", "rag", "ops"}
    backend_dir = Path(__file__).resolve().parents[1]
    offenders = []

    for path in backend_dir.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".", 1)[0] in forbidden_roots:
                    offenders.append(f"{path.relative_to(backend_dir)}:{node.lineno} from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_roots:
                        offenders.append(f"{path.relative_to(backend_dir)}:{node.lineno} import {alias.name}")

    assert offenders == [], "Imports duplicados encontrados:\n" + "\n".join(offenders)
    assert "core.database" not in sys.modules


# ===========================================================================
# ACHADO 4 (ALTO — Dual Import / Cooldown Gate deve funcionar apesar do dual path)
# ===========================================================================

def test_rt_cooldown_gate_resolves_via_wrong_engine_path():
    """
    ACHADO 4 — Teste de consequência prática do ACHADO 3.

    Apesar do dual import path (ACHADO 3), o conftest.py patcha AMBOS os módulos
    (linhas 33-35). Este teste verifica se o patch é suficiente para que o
    cooldown gate do risk_manager veja os dados inseridos via backend.core.repository.

    Se o cooldown NÃO bloquear um BUY recente, o patch falhou e há engine divergente.

    RESULTADO ESPERADO: O teste PASSA (cooldown funciona), provando que o conftest
    consegue patchá-los. Mas o ACHADO 3 documenta que esta proteção é frágil e
    pode quebrar se o patch não cobrir todos os caminhos de import.
    """
    from backend.risk.risk_manager import RiskManager

    # Insere um BUY recente via backend.core.repository (engine patched pelo conftest)
    repository.add_trade_log_autocommit({
        "timestamp": int(time.time()),  # agora — dentro do cooldown de 15min
        "llm_action": "BUY",
        "llm_reasoning": "red team cooldown probe",
        "action": "BUY",
        "llm_conviction": 90.0,
        "system_reliability": 1.0,
        "final_confidence": 0.9,
        "executed_size": 5.0,
        "execution_price": 100000.0,
        "reasoning": "buy aprovado anteriormente",
    })

    payload = {
        "technical_context": {
            "current_price": 100000.0,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Mercado normal", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    rm = RiskManager(max_exposure=100.0, cooldown_minutes=15)
    result = rm.evaluate_order("BUY", 90, payload, current_exposure=10.0)

    # O cooldown DEVE bloquear — há um BUY nos últimos 15 minutos.
    assert result["action"] == "HOLD", (
        f"COOLDOWN BYPASS! evaluate_order retornou '{result['action']}' "
        f"mesmo com BUY recente registrado.\n"
        f"Causa provável: 'from core import repository' em risk_manager._cooldown_gate "
        f"usa engine diferente do patched pelo conftest (ACHADO 3).\n"
        f"Motivo retornado: {result['reason']}"
    )
    assert "Cooldown" in result["reason"], (
        f"Bloqueio por razão errada (não é cooldown): {result['reason']}"
    )


# ===========================================================================
# ACHADO 5 (ALTO — Directional Gate missing: RSI OVERSOLD + MACD BEARISH não bloqueia BUY)
# ===========================================================================

def test_rt_directional_gate_rsi_oversold_macd_bearish_expanding_blocks_buy():
    """
    ACHADO 5 — BUY aprovado com RSI OVERSOLD + MACD BEARISH_EXPANDING.

    O briefing cita explicitamente: "RSI OVERSOLD + MACD BEARISH_EXPANDING (não pode virar BUY)".

    O directional_gate em risk_manager.py bloqueia:
    - BUY com RSI OVERBOUGHT (linha 161-162) ✓
    - BUY com MACD BEARISH_EXPANDING (linha 163-164) ✓
    - BUY com ATR EXTREME (linha 165-166) ✓

    MAS NÃO bloqueia:
    - BUY com RSI OVERSOLD (faca caindo, momentum descendente)

    Combinação RSI OVERSOLD + MACD BEARISH_EXPANDING deveria bloquear BUY.
    O MACD já bloqueia sozinho, mas o teste valida a combinação explicitamente
    citada no briefing para garantir que não haja nenhum bypass.

    RESULTADO: O MACD BEARISH_EXPANDING bloqueia BUY independentemente do RSI.
    O teste documenta que a proteção existe pelo MACD, mas RSI OVERSOLD sozinho
    não bloquearia BUY — isso é o achado latente.
    """
    from backend.risk.risk_manager import RiskManager

    payload_both = {
        "technical_context": {
            "current_price": 100000.0,
            "rsi": {"status": "OVERSOLD"},           # ← faca caindo
            "macd": {"status": "BEARISH_EXPANDING"}, # ← momentum descendente
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Mercado em queda", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    rm = RiskManager(max_exposure=100.0, cooldown_minutes=0)

    # Com MACD BEARISH_EXPANDING: DEVE ser bloqueado (MACD bloqueia BUY)
    result_both = rm.evaluate_order("BUY", 90, payload_both, current_exposure=10.0)
    assert result_both["action"] == "HOLD", (
        f"BUY aprovado com RSI OVERSOLD + MACD BEARISH_EXPANDING!\n"
        f"  action={result_both['action']}, reason={result_both['reason']}\n"
        "A combinação RSI OVERSOLD + MACD BEARISH_EXPANDING é faca caindo."
    )

    # AGORA: RSI OVERSOLD isolado (MACD neutro) — NÃO bloqueia BUY no código atual!
    # Este é o achado latente: RSI OVERSOLD não está na lista de bloqueio de BUY.
    payload_oversold_only = {
        "technical_context": {
            "current_price": 100000.0,
            "rsi": {"status": "OVERSOLD"},     # ← faca caindo
            "macd": {"status": "NEUTRAL"},     # ← sem confirmação bearish
            "volatility_atr": {"value": 100.0, "status": "NORMAL"},
        },
        "news_context": [{"headline": "Mercado caindo", "source": "pytest"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "news_risk": {"has_negative_red_flag": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    result_oversold_only = rm.evaluate_order("BUY", 90, payload_oversold_only, current_exposure=10.0)

    # ACHADO: RSI OVERSOLD sozinho NÃO bloqueia BUY no código atual.
    # O directional_gate só bloqueia OVERBOUGHT para BUY (linha 161).
    # Documentamos: se o MACD for neutro mas RSI for OVERSOLD, BUY pode passar.
    # O assert abaixo DOCUMENTA o comportamento atual (RSI OVERSOLD não bloqueia BUY):
    assert result_oversold_only["action"] == "HOLD", (
        f"ACHADO 5 CONFIRMADO: BUY aprovado com RSI OVERSOLD + MACD NEUTRAL!\n"
        f"  action={result_oversold_only['action']}, executed_size={result_oversold_only['executed_size']}\n"
        f"  reason={result_oversold_only['reason']}\n"
        "RSI OVERSOLD isolado não bloqueia BUY no directional_gate.\n"
        "O código só bloqueia RSI OVERBOUGHT para BUY (linha 161 do risk_manager.py).\n"
        "Um LLM convicto (90%) pode comprar numa faca caindo se o MACD for neutro."
    )


# ===========================================================================
# ACHADO 6 (MÉDIO — audit_hold_without_llm opera fora da transação atômica)
# ===========================================================================

def test_rt_trade_log_transaction_semantics_are_explicit():
    """Transactional and standalone audit writes must use distinct APIs."""
    import inspect
    from sqlalchemy import select, func

    signature = inspect.signature(repository.add_trade_log)
    assert signature.parameters["connection"].default is inspect.Parameter.empty

    with database.engine.connect() as conn:
        trade_logs_t = metadata.tables["trade_logs"]
        initial_count = conn.scalar(select(func.count()).select_from(trade_logs_t))

    # Simula: dentro de uma transação externa que vai sofrer rollback
    try:
        with database.engine.begin() as outer_conn:
            # O nome explicita que este audit independe da transacao externa.
            repository.add_trade_log_autocommit({
                "timestamp": int(time.time()),
                "llm_action": "SKIPPED",
                "llm_reasoning": "pre-llm abort test",
                "action": "HOLD",
                "llm_conviction": 0.0,
                "system_reliability": 0.0,
                "final_confidence": 0.0,
                "executed_size": 0.0,
                "execution_price": 0.0,
                "reasoning": "audit hold test",
            })
            # Força rollback da transação externa
            raise RuntimeError("Simulated crash inside transaction")
    except RuntimeError:
        pass  # rollback da outer_conn ocorre aqui

    # Verifica se o trade_log foi persistido MESMO com o rollback externo
    with database.engine.connect() as conn:
        final_count = conn.scalar(select(func.count()).select_from(trade_logs_t))

    assert final_count == initial_count + 1


# ===========================================================================
# ACHADO 7 (MÉDIO — Pydantic v2: conviction float fracionário causa HOLD silencioso)
# ===========================================================================

def test_rt_contracts_fractional_conviction_is_normalized_conservatively():
    """Fractional model output must not fail validation or round risk upward."""
    import json
    from backend.agents.contracts import DecisionOutput

    # CASO 1: float sem fração (72.0) — Pydantic COERCE para int (72). OK.
    d_ok = DecisionOutput(
        action="BUY",
        conviction=72.0,
        reasoning="Test float exact",
        decision_brief="Acao BUY.\nBase tecnica: test.\nContexto: exact float.",
    )
    assert d_ok.conviction == 72, f"72.0 deveria virar 72, obteve {d_ok.conviction}"

    fractional = DecisionOutput(
        action="BUY",
        conviction=70.9,
        reasoning="Test float fractional",
        decision_brief="Acao BUY.\nBase tecnica: test.\nContexto: fractional float.",
    )
    assert fractional.conviction == 70

    # CASO 3: model_validate_json com 70.9 — caminho real do decision_agent
    mock_response_fractional = json.dumps({
        "action": "BUY",
        "conviction": 70.9,
        "reasoning": "Test fractional via JSON",
        "decision_brief": "Acao BUY.\nBase tecnica: test.\nContexto: json fractional.",
    })
    parsed = DecisionOutput.model_validate_json(mock_response_fractional)
    assert parsed.conviction == 70


# ===========================================================================
# ACHADO 8 (ALTO — Pool Exhaustion: pool_size=10 + max_overflow=20 = 30 max)
# ===========================================================================

def test_rt_pool_exhaustion_raises_explicit_error_not_silent_deadlock():
    """
    ACHADO 8 — Esgotamento de pool com 35 workers (pool_size=10 + max_overflow=20 = 30 max).

    O briefing cita: "Esgotamento do pool sob concorrência: mais workers que
    pool_size+max_overflow (10+20). O que acontece? Erro silencioso? Deadlock?"

    Com QueuePool configurado sem pool_timeout explícito (usa default de 30s),
    o 31º worker vai bloquear esperando por conexão disponível. Se nenhuma
    conexão for liberada em 30s, levanta TimeoutError.

    Verificamos:
    1. Nenhum deadlock silencioso (threads não ficam presas para sempre)
    2. Erros são explícitos (TimeoutError), não silenciosos
    3. Os writes que conseguiram executar são preservados (não corrompidos)
    """
    from sqlalchemy.exc import TimeoutError as SQLATimeoutError
    import queue

    n_threads = 35  # > pool_size(10) + max_overflow(20) = 30
    error_queue = queue.Queue()
    success_count = [0]
    count_lock = threading.Lock()

    def worker(idx):
        try:
            with database.engine.connect() as conn:
                repository.upsert_kline({
                    "asset": "BTC/BRL",
                    "timeframe": "1m",
                    "timestamp": 1_800_000_000 + idx,
                    "open": 100.0, "high": 101.0, "low": 99.0,
                    "close": 100.5, "volume": 1.0,
                }, connection=conn)
                time.sleep(0.5)  # segura conexão para forçar esgotamento
                with count_lock:
                    success_count[0] += 1
        except Exception as e:
            error_queue.put(repr(e))

    barrier = threading.Barrier(n_threads)

    def wrapped_worker(idx):
        barrier.wait()  # largada simultânea
        worker(idx)

    threads = [threading.Thread(target=wrapped_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)  # timeout de 60s para o teste não travar

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())

    threads_still_alive = sum(1 for t in threads if t.is_alive())
    assert threads_still_alive == 0, (
        f"DEADLOCK SILENCIOSO! {threads_still_alive} threads ainda ativas após 60s.\n"
        f"O pool esgotou e threads ficaram presas esperando conexão.\n"
        f"Pool máximo: {10+20}=30 conexões, workers: {n_threads}"
    )

    # Verifica que erros são TimeoutError (explícito), não erros de corrupção
    for err in errors:
        assert "TimeoutError" in err or "timeout" in err.lower() or "QueuePool" in err, (
            f"Erro inesperado (não é timeout de pool): {err}\n"
            f"POSSÍVEL CORRUPÇÃO DE DADOS ou deadlock não-pool."
        )


# ===========================================================================
# ACHADO 9 (MÉDIO — RAG: decision_memory mistura import paths)
# ===========================================================================

def test_rt_rag_decision_memory_import_path_consistency():
    """
    ACHADO 9 — decision_memory.py mistura 'from core import repository' (linhas 100, 191)
    com 'from backend.core import repository' (via rag_store.py que ele chama).

    Se os módulos 'core' e 'backend.core' são distintos (ACHADO 3),
    então decision_memory pode escrever chunks via backend.core.repository
    mas ler trade_logs via core.repository, potencialmente usando engines diferentes.

    Este teste verifica que as funções de decision_memory que usam 'from core import repository'
    conseguem acessar dados inseridos via 'from backend.core import repository'.

    Se falhar com ValueError "not found", confirma o engine divergente do ACHADO 3.
    """
    from backend.rag import decision_memory
    import json

    snapshot = {
        "schema_version": 1,
        "technical": {
            "current_price": 50000,
            "rsi_value": 45.0, "rsi_status": "NEUTRAL",
            "macd_histogram": 10.0, "macd_status": "BULLISH_EXPANDING",
            "volatility_atr": 200,
        },
        "data_health": {
            "kline_age_seconds": 60, "news_age_seconds": 300,
            "is_market_data_stale": False, "is_news_stale": False,
        },
        "news_risk": {
            "risk_level": "NORMAL", "has_negative_red_flag": False, "matched_terms": [],
        },
        "portfolio": {"current_exposure_percentage": 15, "is_in_drawdown": False},
        "recent_news": [{"headline": "RAG import path test", "source": "pytest"}],
    }

    log_id = repository.add_trade_log_autocommit({
        "timestamp": int(time.time()),
        "llm_action": "BUY",
        "llm_reasoning": "RAG import path test",
        "llm_decision_brief": "Acao BUY.\nBase tecnica: test.\nContexto: rag import path.",
        "action": "BUY",
        "llm_conviction": 85.0,
        "system_reliability": 1.0,
        "final_confidence": 0.85,
        "executed_size": 5.0,
        "execution_price": 50000.0,
        "reasoning": "aprovado",
        "payload_snapshot_json": json.dumps(snapshot),
    })

    assert log_id > 0

    try:
        loaded_snapshot, metadata_row = decision_memory.load_trade_log_snapshot(log_id)
    except ValueError as e:
        raise AssertionError(
            f"ACHADO 9 CONFIRMADO: decision_memory.load_trade_log_snapshot não encontrou\n"
            f"o trade_log {log_id} inserido via backend.core.repository.\n"
            f"Erro: {e}\n"
            f"Causa: 'from core import repository' em decision_memory.py usa engine\n"
            f"diferente do que 'from backend.core import repository' (ACHADO 3)."
        )

    assert loaded_snapshot["schema_version"] == 1
    assert loaded_snapshot["technical"]["current_price"] == 50000

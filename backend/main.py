import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend.agents.decision_agent import DecisionAgent, has_llm_api_key
from backend.core.audit import serialize_payload_snapshot
from backend.core.database import get_db_path, init_db, print_db_diagnostics
from backend.core import database, repository
from backend.core.runtime_safety import assess_worker_heartbeats
from backend.execution.paper_simulator import PaperExecutionConfig, empty_execution_audit, execute_paper_order
from backend.features.payload_builder import build_agent_payload
from backend.risk.risk_manager import RiskManager
from backend.risk.portfolio_guard import enrich_payload_with_daily_equity

load_dotenv()


def audit_hold_without_llm(payload: dict, reason: str):
    data = {
        "timestamp": int(time.time()),
        "llm_action": "SKIPPED",
        "llm_reasoning": reason,
        "llm_decision_brief": (
            f"Acao HOLD: {reason}\n"
            "Base operacional: preflight bloqueou consulta ao LLM.\n"
            "Contexto: dados de mercado inseguros para decisao."
        ),
        "action": "HOLD",
        "llm_conviction": 0,
        "system_reliability": 0.0,
        "final_confidence": 0.0,
        "executed_size": 0.0,
        "execution_price": payload.get("technical_context", {}).get("current_price", 0.0),
        "reasoning": reason,
        "payload_snapshot_json": serialize_payload_snapshot(payload),
    }
    repository.add_trade_log_autocommit(data)


def is_llm_technical_failure(llm_decision) -> bool:
    reasoning = (llm_decision.reasoning or "").lower()
    return (
        llm_decision.action == "HOLD"
        and llm_decision.conviction == 0
        and ("technical failure" in reasoning or "validation failed" in reasoning or "system api error" in reasoning)
    )


def run_trading_cycle():
    print("=" * 60)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Iniciando Ciclo de Trading (V2)...")
    print("[0/4] Preflight Database...")
    init_db()
    print_db_diagnostics()

    if not _workers_are_healthy():
        return False

    print("[1/4] Montando Payload do Mercado (TA + Noticias)...")
    payload = build_agent_payload()

    if payload.get("status") == "ERROR":
        print(f"[!] Ciclo abortado: {payload.get('message', 'Dados insuficientes no banco.')}")
        print(f"    DB path: {payload.get('db_path', get_db_path())}")
        print(
            "    Candles: "
            f"{payload.get('found_klines', 'unknown')}/{payload.get('required_klines', 'unknown')} "
            f"para {payload.get('asset', 'unknown')} {payload.get('timeframe', 'unknown')}"
        )
        return False

    current_price = payload["technical_context"]["current_price"]
    print(f"      -> Preco Atual: R${current_price:.2f}")
    data_health = payload.get("data_health", {})
    print(
        "      -> Data Health: "
        f"kline_age={data_health.get('kline_age_seconds')}s "
        f"news_age={data_health.get('news_age_seconds')}s "
        f"market_stale={data_health.get('is_market_data_stale')} "
        f"news_stale={data_health.get('is_news_stale')}"
    )

    if data_health.get("is_market_data_stale"):
        reason = (
            "Pre-LLM abort: market data stale "
            f"({data_health.get('kline_age_seconds')}s > "
            f"{data_health.get('market_data_stale_threshold_seconds')}s)."
        )
        print(f"[!] {reason}")
        print("      -> LLM nao consultado. HOLD tecnico auditado.")
        audit_hold_without_llm(payload, reason)
        print("=" * 60 + "\n")
        return False

    if not has_llm_api_key():
        reason = "Pre-LLM abort: nenhuma chave LLM configurada."
        print(f"[!] {reason}")
        print("      -> HOLD tecnico auditado; mocks nao participam do runtime.")
        audit_hold_without_llm(payload, reason)
        print("=" * 60 + "\n")
        return False

    rm = RiskManager(max_exposure=80.0)
    try:
        enrich_payload_with_daily_equity(payload, max_daily_drawdown=rm.max_daily_drawdown)
    except (RuntimeError, ValueError) as error:
        reason = f"Pre-LLM abort: estado de capital paper invalido ({error})."
        print(f"[!] {reason}")
        audit_hold_without_llm(payload, reason)
        print("=" * 60 + "\n")
        return False

    print("[2/4] Consultando Decision Agent (LLM)...")
    agent = DecisionAgent()
    if os.getenv("LLM_TOOLS_ENABLED", "false").strip().lower() in {"1", "true", "yes"}:
        from backend.analysis.tool_engine import DeterministicToolEngine

        print("      -> Ferramentas deterministicas habilitadas (maximo 3, somente leitura).")
        evaluation = agent.evaluate_market_with_tools(
            payload,
            DeterministicToolEngine(audit=True, persist_events=True),
            asset="BTC/BRL",
            timeframe="1m",
            as_of_timestamp=payload.get("data_health", {}).get("latest_kline_timestamp"),
        )
        payload = evaluation.enriched_payload
        llm_decision = evaluation.decision
        for result in evaluation.tool_results:
            print(f"         tool={result.tool} status={result.status} latency={result.latency_ms:.1f}ms")
    else:
        llm_decision = agent.evaluate_market(payload)

    print(f"      -> IA Sugeriu: {llm_decision.action} | Conviccao: {llm_decision.conviction}%")
    print(f"      -> Justificativa: {llm_decision.reasoning}")
    if llm_decision.decision_brief:
        print("      -> Resumo da IA:")
        for line in llm_decision.decision_brief.splitlines()[:3]:
            print(f"         {line}")

    print("[3/4] Avaliando Risco Matematico (A Muralha)...")
    current_exposure = payload["portfolio_context"]["current_exposure_percentage"]

    if is_llm_technical_failure(llm_decision):
        final_order = {
            "action": "HOLD",
            "reason": "LLM technical failure or validation failed; HOLD.",
            "executed_size": 0.0,
        }
    else:
        final_order = rm.evaluate_order(
            llm_action=llm_decision.action,
            llm_conviction=llm_decision.conviction,
            payload=payload,
            current_exposure=current_exposure,
        )

    print(f"      -> VEREDITO FINAL: {final_order['action']}")
    print(f"      -> MOTIVO: {final_order['reason']}")

    print("[4/4] Execucao e Auditoria.")
    sys_rel = rm.calculate_system_reliability(payload)

    try:
        with database.engine.begin() as conn:
            execution_audit = _execute_if_approved(conn, final_order, current_price, payload)
            _insert_trade_log(conn, llm_decision, final_order, sys_rel, current_price, payload, execution_audit)
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Error Critico] Transacao falhou e foi desfeita: {type(e).__name__}: {e}")
        raise

    if final_order["action"] == "HOLD":
        print("      -> Nenhuma ordem enviada para a Exchange.")

    print("=" * 60 + "\n")
    return True


def _workers_are_healthy() -> bool:
    health_rows = repository.get_system_health()
    assessment = assess_worker_heartbeats(health_rows)
    for worker, age in assessment.ages_seconds.items():
        print(f"[WORKER] {worker} heartbeat_age={age}s")
    if assessment.healthy:
        return True
    for failure in assessment.failures:
        print(f"[SAFE_MODE_WORKER] {failure}")
    print("      -> ABORTANDO CICLO PREVENTIVAMENTE. Reinicie e valide os workers.")
    print("=" * 60 + "\n")
    return False


def _execute_if_approved(connection, final_order: dict, current_price: float, payload: dict) -> dict:
    if final_order["action"] == "HOLD":
        return empty_execution_audit(current_price)

    print(f"      -> ENVIANDO ORDEM PAPER TRADING: Executar {final_order['executed_size']:.2f}% do Capital.")
    execution_audit = execute_paper_order(
        connection=connection,
        action=final_order["action"],
        executed_size_pct=final_order["executed_size"],
        current_price=current_price,
        payload=payload,
        config=PaperExecutionConfig.from_env(),
    )
    print(
        "      -> Execucao paper: "
        f"preco_ref=R${execution_audit['expected_price']:.2f} "
        f"preco_efetivo=R${execution_audit['effective_price']:.2f} "
        f"slippage={execution_audit['slippage_rate'] * 100:.3f}% "
        f"taxa=R${execution_audit['fee_brl']:.2f}"
    )

    if final_order["action"] == "BUY":
        print(
            "      -> BRL Gasto: "
            f"R${abs(execution_audit['brl_delta']):.2f} | "
            f"BTC Comprado: {execution_audit['btc_delta']:.8f}"
        )
    elif final_order["action"] == "SELL":
        print(
            "      -> BTC Vendido: "
            f"{abs(execution_audit['btc_delta']):.8f} | "
            f"BRL Liquido: R${execution_audit['net_notional_brl']:.2f} | "
            f"PnL realizado: R${execution_audit['realized_pnl_brl']:.2f}"
        )

    return execution_audit


def _insert_trade_log(connection, llm_decision, final_order: dict, sys_rel: float, current_price: float, payload: dict, execution_audit: dict) -> None:
    data = {
        "timestamp": int(time.time()),
        "llm_action": llm_decision.action,
        "llm_reasoning": llm_decision.reasoning,
        "llm_decision_brief": llm_decision.decision_brief,
        "action": final_order["action"],
        "llm_conviction": llm_decision.conviction,
        "system_reliability": sys_rel,
        "final_confidence": (llm_decision.conviction / 100.0) * sys_rel,
        "executed_size": final_order["executed_size"],
        "execution_price": current_price,
        "reasoning": final_order["reason"],
        "payload_snapshot_json": serialize_payload_snapshot(payload),
        "fee_rate": execution_audit["fee_rate"],
        "fee_brl": execution_audit["fee_brl"],
        "slippage_rate": execution_audit["slippage_rate"],
        "expected_price": execution_audit["expected_price"],
        "effective_price": execution_audit["effective_price"],
        "gross_notional_brl": execution_audit["gross_notional_brl"],
        "net_notional_brl": execution_audit["net_notional_brl"],
        "brl_delta": execution_audit["brl_delta"],
        "btc_delta": execution_audit["btc_delta"],
        "equity_before_brl": execution_audit["equity_before_brl"],
        "equity_after_brl": execution_audit["equity_after_brl"],
        "realized_pnl_brl": execution_audit["realized_pnl_brl"],
        "position_avg_cost_brl": execution_audit["position_avg_cost_brl"],
    }
    repository.add_trade_log(data, connection=connection)


if __name__ == "__main__":
    run_trading_cycle()

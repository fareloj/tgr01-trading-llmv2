import sys
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from agents.decision_agent import DecisionAgent, has_llm_api_key
from core.audit import serialize_payload_snapshot
from core.database import get_connection, get_db_path, init_db, print_db_diagnostics
from execution.paper_simulator import empty_execution_audit, execute_paper_order
from features.payload_builder import build_agent_payload
from risk.risk_manager import RiskManager

load_dotenv()


def audit_hold_without_llm(payload: dict, reason: str):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trade_logs (
                timestamp, llm_action, llm_reasoning, llm_decision_brief, action, llm_conviction,
                system_reliability, final_confidence, executed_size, execution_price,
                reasoning, payload_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                "SKIPPED",
                reason,
                f"Acao HOLD: {reason}\nBase operacional: preflight bloqueou consulta ao LLM.\nContexto: dados de mercado inseguros para decisao.",
                "HOLD",
                0,
                0.0,
                0.0,
                0.0,
                payload.get("technical_context", {}).get("current_price", 0.0),
                reason,
                serialize_payload_snapshot(payload),
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
    print("[0/4] Preflight SQLite...")
    init_db()
    print_db_diagnostics()

    if not _workers_are_healthy():
        return False

    print("[1/4] Montando Payload do Mercado (TA + Noticias)...")
    payload = build_agent_payload()

    if payload.get("status") == "ERROR":
        print(f"[!] Ciclo abortado: {payload.get('message', 'Dados insuficientes no SQLite.')}")
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

    if has_llm_api_key():
        print("[2/4] Consultando Decision Agent (LLM)...")
        agent = DecisionAgent()
        llm_decision = agent.evaluate_market(payload)
    else:
        print("[2/4] (MOCK) GROQ_API_KEY ausente. Simulando IA...")
        from agents.contracts import DecisionOutput

        llm_decision = DecisionOutput(
            action="BUY",
            conviction=95,
            reasoning="Simulacao: grafico otimista.",
            decision_brief=(
                "Acao BUY: simulacao local sem chave LLM.\n"
                "Base tecnica: mock otimista usado apenas em teste.\n"
                "Contexto: nao usar como decisao real."
            ),
        )

    print(f"      -> IA Sugeriu: {llm_decision.action} | Conviccao: {llm_decision.conviction}%")
    print(f"      -> Justificativa: {llm_decision.reasoning}")
    if llm_decision.decision_brief:
        print("      -> Resumo da IA:")
        for line in llm_decision.decision_brief.splitlines()[:3]:
            print(f"         {line}")

    print("[3/4] Avaliando Risco Matematico (A Muralha)...")
    rm = RiskManager(max_exposure=80.0)
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
    conn = get_connection()
    try:
        cursor = conn.cursor()
        sys_rel = rm.calculate_system_reliability(payload)
        execution_audit = _execute_if_approved(cursor, final_order, current_price, payload)
        _insert_trade_log(cursor, llm_decision, final_order, sys_rel, current_price, payload, execution_audit)
        conn.commit()
    finally:
        conn.close()

    if final_order["action"] == "HOLD":
        print("      -> Nenhuma ordem enviada para a Exchange.")

    print("=" * 60 + "\n")
    return True


def _workers_are_healthy() -> bool:
    conn_health = get_connection()
    try:
        cursor_health = conn_health.cursor()
        cursor_health.execute("SELECT * FROM system_health")
        health_rows = cursor_health.fetchall()
    finally:
        conn_health.close()

    current_time = int(time.time())
    for row in health_rows:
        worker = row["worker_name"]
        last_hb = row["last_heartbeat"]
        limit = 300 if worker == "price_worker" else 3600

        if current_time - last_hb > limit:
            print(f"[SAFE_MODE_STALE_WORKER] {worker} congelado! Ultimo sinal ha {current_time - last_hb}s.")
            print("      -> ABORTANDO CICLO PREVENTIVAMENTE. O worker morreu silenciosamente?")
            print("=" * 60 + "\n")
            return False

    return True


def _execute_if_approved(cursor, final_order: dict, current_price: float, payload: dict) -> dict:
    if final_order["action"] == "HOLD":
        return empty_execution_audit(current_price)

    print(f"      -> ENVIANDO ORDEM PAPER TRADING: Executar {final_order['executed_size']:.2f}% do Capital.")
    execution_audit = execute_paper_order(
        cursor=cursor,
        action=final_order["action"],
        executed_size_pct=final_order["executed_size"],
        current_price=current_price,
        payload=payload,
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


def _insert_trade_log(cursor, llm_decision, final_order: dict, sys_rel: float, current_price: float, payload: dict, execution_audit: dict) -> None:
    cursor.execute(
        """
        INSERT INTO trade_logs (
            timestamp, llm_action, llm_reasoning, llm_decision_brief, action, llm_conviction,
            system_reliability, final_confidence, executed_size, execution_price,
            reasoning, payload_snapshot_json, fee_rate, fee_brl, slippage_rate,
            expected_price, effective_price, gross_notional_brl, net_notional_brl,
            brl_delta, btc_delta, equity_before_brl, equity_after_brl,
            realized_pnl_brl, position_avg_cost_brl
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(time.time()),
            llm_decision.action,
            llm_decision.reasoning,
            llm_decision.decision_brief,
            final_order["action"],
            llm_decision.conviction,
            sys_rel,
            (llm_decision.conviction / 100.0) * sys_rel,
            final_order["executed_size"],
            current_price,
            final_order["reason"],
            serialize_payload_snapshot(payload),
            execution_audit["fee_rate"],
            execution_audit["fee_brl"],
            execution_audit["slippage_rate"],
            execution_audit["expected_price"],
            execution_audit["effective_price"],
            execution_audit["gross_notional_brl"],
            execution_audit["net_notional_brl"],
            execution_audit["brl_delta"],
            execution_audit["btc_delta"],
            execution_audit["equity_before_brl"],
            execution_audit["equity_after_brl"],
            execution_audit["realized_pnl_brl"],
            execution_audit["position_avg_cost_brl"],
        ),
    )


if __name__ == "__main__":
    run_trading_cycle()

import time
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from features.payload_builder import build_agent_payload
from agents.decision_agent import DecisionAgent, has_llm_api_key
from risk.risk_manager import RiskManager
from core.audit import serialize_payload_snapshot
from core.database import get_connection, get_db_path, init_db, print_db_diagnostics

load_dotenv()


def audit_hold_without_llm(payload: dict, reason: str):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trade_logs (timestamp, llm_action, llm_reasoning, action, llm_conviction, system_reliability, final_confidence, executed_size, execution_price, reasoning, payload_snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            int(time.time()),
            "SKIPPED",
            reason,
            "HOLD",
            0,
            0.0,
            0.0,
            0.0,
            payload.get("technical_context", {}).get("current_price", 0.0),
            reason,
            serialize_payload_snapshot(payload),
        ))
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
    print("="*60)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Iniciando Ciclo de Trading (V2)...")
    print("[0/4] Preflight SQLite...")
    init_db()
    print_db_diagnostics()
    
    # --- HEALTH CHECK DOS WORKERS (SAFE_MODE_STALE_WORKER) ---
    conn_health = get_connection()
    cursor_health = conn_health.cursor()
    cursor_health.execute("SELECT * FROM system_health")
    health_rows = cursor_health.fetchall()
    conn_health.close()
    
    current_time = int(time.time())
    for row in health_rows:
        worker = row['worker_name']
        last_hb = row['last_heartbeat']
        
        # Tolerâncias: 5 mins pro preço, 60 mins pras notícias.
        limit = 300 if worker == 'price_worker' else 3600 
        
        if current_time - last_hb > limit:
            print(f"[SAFE_MODE_STALE_WORKER] {worker} congelado! Último sinal há {current_time - last_hb}s.")
            print("      -> ABORTANDO CICLO PREVENTIVAMENTE. O worker morreu silenciosamente?")
            print("="*60 + "\n")
            return False
    # ---------------------------------------------------------
    
    # 1. Construir o Payload Determinístico
    print("[1/4] Montando Payload do Mercado (TA + Notícias)...")
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
        
    print(f"      -> Preço Atual: R${payload['technical_context']['current_price']:.2f}")
    data_health = payload.get("data_health", {})
    print(
        "      -> Data Health: "
        f"kline_age={data_health.get('kline_age_seconds')}s "
        f"news_age={data_health.get('news_age_seconds')}s "
        f"market_stale={data_health.get('is_market_data_stale')} "
        f"news_stale={data_health.get('is_news_stale')}"
    )
    
    # 2. IA Toma Decisão
    if data_health.get("is_market_data_stale"):
        reason = (
            "Pre-LLM abort: market data stale "
            f"({data_health.get('kline_age_seconds')}s > "
            f"{data_health.get('market_data_stale_threshold_seconds')}s)."
        )
        print(f"[!] {reason}")
        print("      -> LLM nao consultado. HOLD tecnico auditado.")
        audit_hold_without_llm(payload, reason)
        print("="*60 + "\n")
        return False

    if has_llm_api_key():
        print("[2/4] Consultando Decision Agent (LLM)...")
        agent = DecisionAgent()
        llm_decision = agent.evaluate_market(payload)
    else:
        print("[2/4] (MOCK) GROQ_API_KEY ausente. Simulando IA...")
        from agents.contracts import DecisionOutput
        # Mock de IA super otimista para testar se o Risk Manager barra
        llm_decision = DecisionOutput(action="BUY", conviction=95, reasoning="Simulação: Gráfico lindo, vou comprar tudo!")
    
    print(f"      -> IA Sugeriu: {llm_decision.action} | Convicção: {llm_decision.conviction}%")
    print(f"      -> Justificativa: {llm_decision.reasoning}")
    
    # 3. Barreira de Risco (Matemática Pura)
    print("[3/4] Avaliando Risco Matemático (A Muralha)...")
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
            current_exposure=current_exposure
        )
    
    print(f"      -> VEREDITO FINAL: {final_order['action']}")
    print(f"      -> MOTIVO: {final_order['reason']}")
    
    # 4. Execução Simulada e Logs
    print("[4/4] Execução e Auditoria.")
    
    # Registrando a decisão no banco de dados (Auditoria)
    conn = get_connection()
    cursor = conn.cursor()
    sys_rel = rm.calculate_system_reliability(payload)
    
    cursor.execute('''
        INSERT INTO trade_logs (timestamp, llm_action, llm_reasoning, action, llm_conviction, system_reliability, final_confidence, executed_size, execution_price, reasoning, payload_snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        int(time.time()),
        llm_decision.action,
        llm_decision.reasoning,
        final_order["action"],
        llm_decision.conviction,
        sys_rel,
        (llm_decision.conviction / 100.0) * sys_rel,
        final_order["executed_size"],
        payload['technical_context']['current_price'],
        final_order["reason"],
        serialize_payload_snapshot(payload),
    ))
    conn.commit()

    if final_order["action"] != "HOLD":
        print(f"      -> ENVIANDO ORDEM PAPER TRADING: Executar {final_order['executed_size']:.2f}% do Capital.")
        
        # --- Matemática de Execução Simulada (Paper Trading) ---
        current_price = payload['technical_context']['current_price']
        
        cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'")
        brl_bal = cursor.fetchone()['amount']
        cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'")
        btc_bal = cursor.fetchone()['amount']
        
        total_equity = brl_bal + (btc_bal * current_price)
        trade_brl_volume = total_equity * (final_order['executed_size'] / 100.0)
        
        taxa_corretagem = 0.003 # 0.3% taxa agressiva padrão
        
        if final_order["action"] == "BUY":
            actual_brl_spent = min(trade_brl_volume, brl_bal)
            if actual_brl_spent > 0:
                btc_bought = (actual_brl_spent * (1.0 - taxa_corretagem)) / current_price
                cursor.execute("UPDATE virtual_portfolio SET amount = amount - ? WHERE currency='BRL'", (actual_brl_spent,))
                cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BTC'", (btc_bought,))
                print(f"      -> BRL Gasto: R${actual_brl_spent:.2f} | BTC Comprado: {btc_bought:.6f}")
                
        elif final_order["action"] == "SELL":
            btc_to_sell = trade_brl_volume / current_price
            actual_btc_sold = min(btc_to_sell, btc_bal)
            if actual_btc_sold > 0:
                brl_received = (actual_btc_sold * current_price) * (1.0 - taxa_corretagem)
                cursor.execute("UPDATE virtual_portfolio SET amount = amount - ? WHERE currency='BTC'", (actual_btc_sold,))
                cursor.execute("UPDATE virtual_portfolio SET amount = amount + ? WHERE currency='BRL'", (brl_received,))
                print(f"      -> BTC Vendido: {actual_btc_sold:.6f} | BRL Recebido: R${brl_received:.2f}")

        conn.commit()
        
    else:
        print("      -> Nenhuma ordem enviada para a Exchange.")
        
    conn.close()
    print("="*60 + "\n")
    return True

if __name__ == "__main__":
    run_trading_cycle()

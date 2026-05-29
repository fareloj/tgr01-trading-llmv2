import sys
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from backend.core.database import get_connection
from backend.execution.market_data_gateway import MBDataGateway

def render_dashboard():
    print("\n" + "="*60)
    print("DASHBOARD DE ANALYTICS - PAPER TRADING (FASE 5.5)")
    print("="*60)
    
    conn = get_connection()
    
    # 1. Analisar Trade Logs
    df_logs = pd.read_sql_query("SELECT * FROM trade_logs ORDER BY timestamp ASC", conn)
    
    if df_logs.empty:
        print("Ainda não há logs de trading. Rode o orquestrador primeiro.")
        conn.close()
        return

    total_ciclos = len(df_logs)
    
    acoes_executadas = df_logs[df_logs['action'] != 'HOLD']
    holds_forçados_ou_voluntarios = df_logs[df_logs['action'] == 'HOLD']
    
    bloqueios_muralha = len(df_logs[(df_logs['action'] == 'HOLD') & (df_logs['llm_conviction'] >= 70)])
    
    primeiro_ciclo = df_logs.iloc[0]['timestamp']
    ultimo_ciclo = df_logs.iloc[-1]['timestamp']
    horas_rodando = max((ultimo_ciclo - primeiro_ciclo) / 3600.0, 0.01) # evita divisao por zero
    
    trades_per_hour = len(acoes_executadas) / horas_rodando
    
    print(f"Total de Heartbeats (Ciclos): {total_ciclos}")
    print(f"Tempo total de simulação: {horas_rodando:.2f} horas")
    print(f"Ordens executadas com sucesso: {len(acoes_executadas)} (BUY/SELL)")
    print(f"Métrica de Overtrading: {trades_per_hour:.2f} trades/hora")
    print(f"Ordens bloqueadas pelo Risk Manager: {bloqueios_muralha} "
          f"({(bloqueios_muralha/total_ciclos*100) if total_ciclos > 0 else 0:.1f}%)")

    # 2. Analisar Portfólio Atual
    cursor = conn.cursor()
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'")
    brl_bal = cursor.fetchone()['amount']
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'")
    btc_bal = cursor.fetchone()['amount']
    
    # Busca preço atual para calcular Total Equity
    try:
        gw = MBDataGateway()
        current_price = gw.fetch_latest_kline()['close']
    except:
        current_price = df_logs.iloc[-1]['execution_price']
        
    total_equity = brl_bal + (btc_bal * current_price)
    
    pnl = total_equity - 10000.0
    pnl_perc = (pnl / 10000.0) * 100
    
    print("\nRESULTADO DO PORTFOLIO (Caixa de Vidro):")
    print(f"  -> BRL Fisico: R$ {brl_bal:.2f}")
    print(f"  -> BTC Virtual: {btc_bal:.6f} BTC")
    print(f"  -> Patrimonio Total Estimado: R$ {total_equity:.2f}")
    
    if pnl >= 0:
        print(f"  -> PnL: +R$ {pnl:.2f} (+{pnl_perc:.2f}%)")
    else:
        print(f"  -> PnL: -R$ {abs(pnl):.2f} ({pnl_perc:.2f}%)")
        
    print("="*60 + "\n")
    conn.close()

if __name__ == "__main__":
    render_dashboard()

import sys
import time
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

DB_PATH = BASE_DIR / "backend" / "trading_v2.db"

def watch_ai_feed():
    print("="*60)
    print("🧠 LIVE AI FEED - MONITOR DE PENSAMENTOS DO LLM")
    print("="*60)
    print("Aguardando o Llama-3.3 começar a operar (faltam alguns minutos)...")
    
    last_timestamp = 0
    
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM trade_logs WHERE timestamp > ? ORDER BY timestamp ASC", (last_timestamp,))
            rows = cursor.fetchall()
            
            for row in rows:
                time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['timestamp']))
                print(f"\n[{time_str}] O CÉREBRO TOMOU UMA DECISÃO:")
                print(f"🔹 IA Queria: {row['llm_action']} | Convicção: {row['llm_conviction']}%")
                print(f"🔹 Justificativa da IA: {row['llm_reasoning']}")
                print(f"🔹 Veredito Final (Risk Manager): {row['action']} (Confiabilidade do Sistema: {row['system_reliability']}%)")
                print(f"🔹 Motivo da Execução/Bloqueio: {row['reasoning']}")
                print("-"*60)
                
                last_timestamp = row['timestamp']
                
            conn.close()
        except Exception as e:
            pass # Ignora erros de lock de banco se ocorrerem rapidamente
            
        time.sleep(5) # Checa o banco a cada 5 segundos

if __name__ == "__main__":
    watch_ai_feed()

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR.parent))

from backend.core import repository

def watch_ai_feed():
    print("="*60)
    print("🧠 LIVE AI FEED - MONITOR DE PENSAMENTOS DO LLM")
    print("="*60)
    print("Aguardando o Llama-3.3 começar a operar (faltam alguns minutos)...")
    
    last_timestamp = 0
    
    while True:
        try:
            # Query trade logs since last timestamp
            # Wait, the first run since last_timestamp=0 would pull all logs.
            # But the logic says: "ORDER BY timestamp ASC" and last_timestamp starts at 0.
            # So that matches get_trade_logs perfectly since get_trade_logs filters by since_timestamp and orders ASC.
            # But wait, to avoid getting everything on startup, last_timestamp can start at 0 but let's query.
            # Yes, get_trade_logs filters on trade_logs.c.timestamp >= since_timestamp.
            # Let's adjust so it only gets greater than last_timestamp.
            # Wait, in repository.py: `trade_logs.c.timestamp >= since_timestamp`.
            # If we want strictly greater, since_timestamp can be last_timestamp + 1!
            rows = repository.get_trade_logs(since_timestamp=last_timestamp + 1 if last_timestamp > 0 else 0)
            
            for row in rows:
                time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['timestamp']))
                print(f"\n[{time_str}] O CÉREBRO TOMOU UMA DECISÃO:")
                print(f"🔹 IA Queria: {row['llm_action']} | Convicção: {row['llm_conviction']}%")
                print(f"🔹 Justificativa da IA: {row['llm_reasoning']}")
                print(f"🔹 Veredito Final (Risk Manager): {row['action']} (Confiabilidade do Sistema: {row['system_reliability']}%)")
                print(f"🔹 Motivo da Execução/Bloqueio: {row['reasoning']}")
                print("-"*60)
                
                last_timestamp = row['timestamp']
                
        except Exception as e:
            pass # Ignora erros de lock de banco se ocorrerem rapidamente
            
        time.sleep(5) # Checa o banco a cada 5 segundos

if __name__ == "__main__":
    watch_ai_feed()

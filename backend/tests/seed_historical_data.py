import argparse
import sys
import time
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))

from core.database import get_connection, get_db_path, init_db


def seed_history(symbol: str = "BTC-BRL", asset: str = "BTC/BRL", timeframe: str = "1m", limit: int = 100):
    print(f"Iniciando Seed de Historico Real para {symbol}...")
    init_db()
    print(f"[Seed] DB path: {get_db_path()}")
    print(f"[Seed] Asset/timeframe: {asset} {timeframe}")

    to_ts = int(time.time())
    from_ts = to_ts - (limit * 60)

    url = (
        "https://api.mercadobitcoin.net/api/v4/candles"
        f"?symbol={symbol}&resolution={timeframe}&from={from_ts}&to={to_ts}"
    )

    conn = None
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "t" not in data:
            print(f"Erro ao buscar dados da API. Resposta: {data}")
            return

        conn = get_connection()
        cursor = conn.cursor()

        count = 0
        for i in range(len(data["t"])):
            cursor.execute('''
                INSERT OR IGNORE INTO klines (asset, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                asset,
                timeframe,
                int(data["t"][i]),
                float(data["o"][i]),
                float(data["h"][i]),
                float(data["l"][i]),
                float(data["c"][i]),
                float(data["v"][i]),
            ))

            if cursor.rowcount > 0:
                count += 1

        conn.commit()
        print(f"Sucesso! {count} novos candles historicos inseridos no SQLite.")
        print("O robo agora deve ter dados suficientes para operar no proximo ciclo.")

    except Exception as e:
        print(f"Falha no Seeding: {type(e).__name__}: {e}")

    finally:
        if conn is not None:
            conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Seed historical Mercado Bitcoin candles into SQLite.")
    parser.add_argument("--symbol", default="BTC-BRL", help="Mercado Bitcoin API symbol. Default: BTC-BRL")
    parser.add_argument("--asset", default="BTC/BRL", help="Internal DB asset name. Default: BTC/BRL")
    parser.add_argument("--timeframe", default="1m", help="Candle timeframe/resolution. Default: 1m")
    parser.add_argument("--limit", type=int, default=100, help="Approximate number of minutes/candles to fetch. Default: 100")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    seed_history(symbol=args.symbol, asset=args.asset, timeframe=args.timeframe, limit=args.limit)

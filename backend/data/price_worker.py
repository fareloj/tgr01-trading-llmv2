import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from core.database import get_connection, get_db_path, init_db
from execution.market_data_gateway import MBDataGateway, StaleDataError


def run_price_worker():
    """Worker oficial: puxa dados reais do Mercado Bitcoin e injeta no SQLite."""
    print("Iniciando Price Worker Real (Mercado Bitcoin V4)...")
    init_db()

    gw = MBDataGateway()
    asset = "BTC/BRL"
    timeframe = "1m"
    print(f"[Price Worker] DB path: {get_db_path()}")
    print(f"[Price Worker] Asset/timeframe: {asset} {timeframe}")

    while True:
        conn = None
        try:
            candle = gw.fetch_latest_kline(symbol="BTC-BRL", resolution=timeframe)

            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO klines (asset, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset, timeframe, timestamp) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume
            ''', (
                asset,
                timeframe,
                candle["timestamp"],
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                candle["volume"],
            ))

            if cursor.rowcount > 0:
                print(f"[Market Data] Real-Time: {asset} @ R${candle['close']:.2f}")

            conn.commit()

            cursor.execute(
                """
                INSERT INTO system_health (worker_name, last_heartbeat)
                VALUES ('price_worker', ?)
                ON CONFLICT(worker_name)
                DO UPDATE SET last_heartbeat=excluded.last_heartbeat
                """,
                (int(time.time()),),
            )
            conn.commit()

            time.sleep(30)

        except StaleDataError as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Safe Mode Ativado] {type(e).__name__}: {e}")
            time.sleep(15)

        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Error Critico] Price Worker Falhou: {type(e).__name__}: {e}")
            time.sleep(15)

        finally:
            if conn is not None:
                conn.close()


if __name__ == "__main__":
    run_price_worker()

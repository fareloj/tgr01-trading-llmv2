import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend.core import repository
from backend.core.database import init_db
from backend.execution.market_data_gateway import MBDataGateway, StaleDataError


def run_price_worker():
    """Worker oficial: puxa dados reais do Mercado Bitcoin e persiste no PostgreSQL."""
    print("Iniciando Price Worker Real (Mercado Bitcoin V4)...")
    init_db()

    gw = MBDataGateway()
    asset = "BTC/BRL"
    timeframe = "1m"
    print(f"[Price Worker] Asset/timeframe: {asset} {timeframe}")

    while True:
        try:
            candle = gw.fetch_latest_kline(symbol="BTC-BRL", resolution=timeframe)

            repository.add_klines([{
                "asset": asset,
                "timeframe": timeframe,
                "timestamp": candle["timestamp"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            }])

            print(f"[Market Data] Real-Time: {asset} @ R${candle['close']:.2f}")

            repository.update_system_health('price_worker', int(time.time()))

            time.sleep(30)
        except StaleDataError as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Safe Mode Ativado] {type(e).__name__}: {e}")
            time.sleep(15)
        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Error Critico] Price Worker Falhou: {type(e).__name__}: {e}")
            time.sleep(15)



if __name__ == "__main__":
    run_price_worker()

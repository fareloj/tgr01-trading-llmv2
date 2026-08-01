import time

import requests


class StaleDataError(Exception):
    pass


class MBDataGateway:
    def __init__(self):
        self.base_url_v4 = "https://api.mercadobitcoin.net/api/v4"
        self.timeout = 5.0

    def fetch_latest_kline(self, symbol="BTC-BRL", resolution="1m"):
        """Fetch the latest available Mercado Bitcoin candle using the official countback parameter."""
        to_ts = int(time.time())
        url = f"{self.base_url_v4}/candles"
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "to": to_ts,
            "countback": 5,
        }

        try:
            start_time = time.time()
            response = requests.get(url, params=params, timeout=self.timeout)
            latency = time.time() - start_time
            if latency > 3.0:
                print(f"[Gateway WARNING] Latencia alta na API: {latency:.2f}s")

            response.raise_for_status()
            data = response.json()
            required = ("t", "o", "h", "l", "c", "v")
            if not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in required):
                raise ValueError("Resposta de candles nao possui o schema UDF esperado.")
            lengths = {len(data[key]) for key in required}
            if lengths == {0}:
                raise StaleDataError("API retornou candles vazios.")
            if len(lengths) != 1 or 0 in lengths:
                raise ValueError("Arrays UDF de candles possuem tamanhos inconsistentes.")

            candle = {
                "timestamp": int(data["t"][-1]),
                "open": float(data["o"][-1]),
                "high": float(data["h"][-1]),
                "low": float(data["l"][-1]),
                "close": float(data["c"][-1]),
                "volume": float(data["v"][-1]),
            }
            if candle["close"] <= 0 or candle["high"] < candle["low"]:
                raise ValueError(
                    f"Preco malformado: close={candle['close']}, high={candle['high']}, low={candle['low']}"
                )
            if candle["volume"] < 0:
                raise ValueError(f"Volume negativo: {candle['volume']}")

            age_seconds = to_ts - candle["timestamp"]
            if age_seconds < -60:
                raise StaleDataError(f"Candle esta no futuro: age={age_seconds}s.")
            if age_seconds > 300:
                raise StaleDataError(f"Candle atrasado: age={age_seconds}s > 300s.")
            return candle
        except StaleDataError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Falha fisica ou de parse na API do Mercado Bitcoin: {exc}") from exc


if __name__ == "__main__":
    print("Testando gateway read-only na API real do Mercado Bitcoin...")
    gateway = MBDataGateway()
    try:
        latest = gateway.fetch_latest_kline()
        print(f"[OK] BTC/BRL: R${latest['close']:.2f} | volume={latest['volume']:.6f}")
        print(latest)
    except Exception as exc:
        print(f"[BLOQUEADO] {type(exc).__name__}: {exc}")

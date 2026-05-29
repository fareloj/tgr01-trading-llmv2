import time
import requests

class StaleDataError(Exception):
    pass

class MBDataGateway:
    def __init__(self):
        self.base_url_v4 = "https://api.mercadobitcoin.net/api/v4"
        self.timeout = 5.0 # Max latency permitida para não atrasar decisões críticas
        
    def fetch_latest_kline(self, symbol="BTC-BRL", resolution="1m"):
        """Busca o candle mais recente do Mercado Bitcoin API v4."""
        to_ts = int(time.time())
        from_ts = to_ts - 180 # Pega os últimos 3 minutos para garantir que o array não venha vazio
        
        url = f"{self.base_url_v4}/candles?symbol={symbol}&resolution={resolution}&from={from_ts}&to={to_ts}"
        
        try:
            start_time = time.time()
            resp = requests.get(url, timeout=self.timeout)
            latency = time.time() - start_time
            
            if latency > 3.0:
                print(f"[Gateway WARNING] Latência da API altíssima: {latency:.2f}s")
                
            resp.raise_for_status()
            data = resp.json()
            
            # Formato MB V4 (UDF): {'t': [...], 'o': [...], 'h': [...], 'l': [...], 'c': [...], 'v': [...]}
            if not data or 't' not in data or len(data['t']) == 0:
                raise StaleDataError("API retornou sem dados (array vazio). Pode ser manutenção no provedor.")
                
            # Pega sempre o último índice do array (o candle mais recente em formação ou recém-fechado)
            idx = -1
            
            candle = {
                "timestamp": int(data['t'][idx]),
                "open": float(data['o'][idx]),
                "high": float(data['h'][idx]),
                "low": float(data['l'][idx]),
                "close": float(data['c'][idx]),
                "volume": float(data['v'][idx])
            }
            
            # Validações Duras (Safe Mode contra bugs da API)
            if candle['close'] <= 0 or candle['high'] < candle['low']:
                raise ValueError(f"Preço malformado. Recebido: close={candle['close']}, high={candle['high']}, low={candle['low']}")
                
            if candle['volume'] < 0:
                raise ValueError(f"Volume anômalo (negativo). Recebido: {candle['volume']}")
                
            # Detecta se a API está com lag interno e mandou um candle velho
            if to_ts - candle['timestamp'] > 300: # Tolerância de 5 minutos
                raise StaleDataError(f"Candle severamente atrasado da Exchange. TS do candle: {candle['timestamp']}, TS Local: {to_ts}")
                
            return candle
            
        except StaleDataError:
            raise
        except Exception as e:
            raise Exception(f"Falha de conexão física/parse com a API do Mercado Bitcoin: {e}")

if __name__ == "__main__":
    print("Testando Read-Only Gateway na API Real MB...")
    gw = MBDataGateway()
    try:
        kline = gw.fetch_latest_kline()
        print(f"SUCESSO! Último Preço do BTC: R${kline['close']:.2f} | Volume: {kline['volume']:.6f}")
        print("Objeto completo:", kline)
    except Exception as e:
        print(f"[BLOQUEADO PELA MURALHA] Ocorreu um erro no Gateway: {e}")

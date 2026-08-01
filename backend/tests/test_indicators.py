import sys
import pandas as pd
from pathlib import Path

# Adiciona a raiz do projeto no path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR.parent))

from backend.features.indicators import calculate_technical_status

def test_rsi_overbought():
    """Valida se uma tendência de alta artificial força o RSI acima de 70."""
    data = []
    price = 1000.0
    for i in range(50):
        data.append({
            "timestamp": i,
            "open": price,
            "high": price + 50,
            "low": price,
            "close": price + 50, # Fechamento sempre na máxima
            "volume": 1.0
        })
        price += 50

    df = pd.DataFrame(data)
    status = calculate_technical_status(df)

    assert status["status"] == "OK"
    assert status["rsi"]["status"] == "OVERBOUGHT", f"Esperado OVERBOUGHT, recebeu {status['rsi']['status']}"
    assert status["rsi"]["value"] >= 90.0, f"RSI deveria ser extremo, mas foi {status['rsi']['value']}"
    print("[PASS] RSI Matemático: Overbought detectado com precisão.")

def test_macd_bearish():
    """Valida se uma queda acentuada reflete num histograma negativo do MACD."""
    data = []
    price = 5000.0
    for i in range(50):
        data.append({
            "timestamp": i,
            "open": price,
            "high": price,
            "low": price - 100,
            "close": price - 100,
            "volume": 1.0
        })
        price -= 100

    df = pd.DataFrame(data)
    status = calculate_technical_status(df)

    assert status["status"] == "OK"
    assert status["macd"]["histogram"] < 0, "MACD Histogram deveria ser negativo"
    print("[PASS] MACD Matemático: Queda estrutural detectada perfeitamente.")

def test_insufficient_data():
    """Garante que a falta de dados não seja passada para o LLM."""
    df = pd.DataFrame([{"timestamp": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    status = calculate_technical_status(df)
    assert status["status"] == "ERROR"
    print("[PASS] Trava de proteção: Bloqueio de dados insuficientes funciona.")

def test_bollinger_bands():
    """Valida o cálculo das Bollinger Bands e seus respectivos status."""
    # Cenário INSIDE
    data = []
    for i in range(30):
        # Alternando preço para ter um desvio padrão controlado
        close_p = 102.0 if i % 2 == 0 else 98.0
        data.append({
            "timestamp": i,
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": close_p,
            "volume": 1.0
        })
    df_inside = pd.DataFrame(data)
    status_inside = calculate_technical_status(df_inside)
    assert status_inside["status"] == "OK"
    bb = status_inside["bollinger_bands"]
    assert bb["status"] == "INSIDE"
    assert bb["middle"] == 100.0
    # std de [102, 98, 102, 98...] é aprox 2.034. Upper band = 100 + 2*2.034 = 104.07, Lower band = 95.93
    assert bb["upper"] > 103.0
    assert bb["lower"] < 97.0

    # Cenário ABOVE_UPPER
    data[-1]["close"] = 106.0
    df_above = pd.DataFrame(data)
    status_above = calculate_technical_status(df_above)
    assert status_above["bollinger_bands"]["status"] == "ABOVE_UPPER"

    # Cenário BELOW_LOWER
    data[-1]["close"] = 93.0
    df_below = pd.DataFrame(data)
    status_below = calculate_technical_status(df_below)
    assert status_below["bollinger_bands"]["status"] == "BELOW_LOWER"
    print("[PASS] Bollinger Bands: INSIDE, ABOVE_UPPER e BELOW_LOWER testados com sucesso.")


def test_ema_crossover():
    """Valida o cruzamento de EMA e seus respectivos status."""
    # Cenário BULLISH_CROSS
    data = []
    # 29 dias estáveis em 100.0 -> as EMAs convergem para 100.0
    for i in range(29):
        data.append({
            "timestamp": i,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0
        })
    # No 30º dia o preço sobe para 110.0 -> EMA9 (span=9) sobe mais rápido que EMA21 (span=21)
    data.append({
        "timestamp": 29,
        "open": 100.0,
        "high": 110.0,
        "low": 100.0,
        "close": 110.0,
        "volume": 1.0
    })
    df_bull_cross = pd.DataFrame(data)
    status_bull_cross = calculate_technical_status(df_bull_cross)
    assert status_bull_cross["ema_crossover"]["status"] == "BULLISH_CROSS"

    # Cenário BEARISH_CROSS
    data_bear = []
    for i in range(29):
        data_bear.append({
            "timestamp": i,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0
        })
    data_bear.append({
        "timestamp": 29,
        "open": 100.0,
        "high": 100.0,
        "low": 90.0,
        "close": 90.0,
        "volume": 1.0
    })
    df_bear_cross = pd.DataFrame(data_bear)
    status_bear_cross = calculate_technical_status(df_bear_cross)
    assert status_bear_cross["ema_crossover"]["status"] == "BEARISH_CROSS"

    # Cenário BULLISH (sem cross recente, já estava de alta)
    data_bull = []
    for i in range(20):
        data_bull.append({
            "timestamp": i,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0
        })
    # 9 candles subindo
    price = 100.0
    for i in range(20, 29):
        price += 2.0
        data_bull.append({
            "timestamp": i,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1.0
        })
    # Último candle continua subindo
    data_bull.append({
        "timestamp": 29,
        "open": price + 2.0,
        "high": price + 2.0,
        "low": price + 2.0,
        "close": price + 2.0,
        "volume": 1.0
    })
    df_bull = pd.DataFrame(data_bull)
    status_bull = calculate_technical_status(df_bull)
    assert status_bull["ema_crossover"]["status"] == "BULLISH"

    # Cenário BEARISH (sem cross recente, já estava de baixa)
    data_bearish = []
    for i in range(20):
        data_bearish.append({
            "timestamp": i,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0
        })
    price = 100.0
    for i in range(20, 29):
        price -= 2.0
        data_bearish.append({
            "timestamp": i,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1.0
        })
    data_bearish.append({
        "timestamp": 29,
        "open": price - 2.0,
        "high": price - 2.0,
        "low": price - 2.0,
        "close": price - 2.0,
        "volume": 1.0
    })
    df_bearish = pd.DataFrame(data_bearish)
    status_bearish = calculate_technical_status(df_bearish)
    assert status_bearish["ema_crossover"]["status"] == "BEARISH"
    print("[PASS] EMA Crossover: BULLISH_CROSS, BEARISH_CROSS, BULLISH e BEARISH testados com sucesso.")


def test_volume_profile():
    """Valida a detecção de pico de volume (spike) e o preço do POC."""
    data = []
    for i in range(30):
        # min price = 100.0, max price = 200.0 (high/low)
        # close do candle determina a categoria do POC
        close_p = 115.0 if i < 25 else 185.0
        # Volume maior na faixa de 185 (bin index 8)
        # Último candle tem volume 4.0 (não é spike), os anteriores na faixa de 185 têm volume 12.0
        vol = 1.0 if i < 25 else (12.0 if i < 29 else 4.0)
        data.append({
            "timestamp": i,
            "open": close_p,
            "high": 200.0 if i == 0 else close_p,
            "low": 100.0 if i == 0 else close_p,
            "close": close_p,
            "volume": vol
        })

    # 25 candles com vol 1.0 na faixa 115 (total vol no bin 1 = 25.0)
    # 4 candles com vol 12.0 e 1 candle com vol 4.0 na faixa 185 (total vol no bin 8 = 52.0)
    # Portanto, o POC deve ser na faixa de 185.0 (midpoint do bin 8)
    df = pd.DataFrame(data)
    status = calculate_technical_status(df)
    vp = status["volume_profile"]

    # Verificar POC
    assert vp["poc_price"] == 185.0

    # Verificar Volume Spike (volume atual = 4.0, média de 20 períodos = 3.35 -> não é > 3.35 * 2)
    assert vp["is_volume_spike"] is False

    # Agora forçar um spike de volume no último candle
    data[-1]["volume"] = 100.0
    df_spike = pd.DataFrame(data)
    status_spike = calculate_technical_status(df_spike)
    assert status_spike["volume_profile"]["is_volume_spike"] is True
    print("[PASS] Volume Profile: POC price e Volume Spike validados com sucesso.")


def test_volatility_atr_structure():
    """Valida a nova estrutura do volatility_atr (valor e status)."""
    # Caso EXTREME: ATR > 5% do preço
    data_extreme = []
    price = 1000.0
    for i in range(30):
        # amplitude grande (high - low) para gerar ATR alto
        data_extreme.append({
            "timestamp": i,
            "open": price,
            "high": price + 60.0,
            "low": price - 60.0,
            "close": price,
            "volume": 1.0
        })
    df_extreme = pd.DataFrame(data_extreme)
    status_extreme = calculate_technical_status(df_extreme)
    atr_extreme = status_extreme["volatility_atr"]
    assert isinstance(atr_extreme, dict)
    assert atr_extreme["value"] >= 60.0
    assert atr_extreme["status"] == "EXTREME"

    # Caso NORMAL: ATR <= 5% do preço
    data_normal = []
    for i in range(30):
        data_normal.append({
            "timestamp": i,
            "open": price,
            "high": price + 5.0,
            "low": price - 5.0,
            "close": price,
            "volume": 1.0
        })
    df_normal = pd.DataFrame(data_normal)
    status_normal = calculate_technical_status(df_normal)
    atr_normal = status_normal["volatility_atr"]
    assert isinstance(atr_normal, dict)
    assert atr_normal["value"] <= 15.0
    assert atr_normal["status"] == "NORMAL"
    print("[PASS] Volatility ATR: Estrutura de dict (value, status) e regras de limiar validadas.")


if __name__ == "__main__":
    print("="*50)
    print("Iniciando Bateria de Testes Matemáticos (Sem LLM)")
    print("="*50)
    test_rsi_overbought()
    test_macd_bearish()
    test_insufficient_data()
    test_bollinger_bands()
    test_ema_crossover()
    test_volume_profile()
    test_volatility_atr_structure()
    print("\n>>> TODOS OS TESTES UNITÁRIOS PASSARAM <<<\n")

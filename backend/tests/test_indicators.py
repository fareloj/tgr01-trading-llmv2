import sys
import numpy as np
import pandas as pd
from pathlib import Path

# Adiciona a raiz do projeto no path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# BASE_DIR JA e a raiz do projeto (...\backend\tests -> ...\backend -> projeto).
# Antes anexava BASE_DIR.parent (a raiz do drive), o que so funcionava por acidente
# quando o pytest rodava da raiz e colocava o cwd no sys.path.
sys.path.insert(0, str(BASE_DIR))

from backend.features.indicators import calculate_technical_status, wilder_rsi
from backend.ml.dataset import _rsi as ml_rsi

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


def _atr_referencia_pandas(df):
    """A expressao pandas original, antes da otimizacao em numpy. Nao alterar."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=14, min_periods=1).mean()


def _candles_sinteticos(n, seed=7, com_gap=False):
    rng = np.random.default_rng(seed)
    close = 100000 + np.cumsum(rng.normal(0, 40, n))
    df = pd.DataFrame({
        "timestamp": range(n),
        "open": close,
        "high": close + rng.uniform(1, 60, n),
        "low": close - rng.uniform(1, 60, n),
        "close": close,
        "volume": rng.uniform(0.5, 2.0, n),
    })
    if com_gap:
        df.loc[n // 2, "close"] = np.nan
        df.loc[n // 3, "high"] = np.nan
    return df


def test_atr_numpy_matches_pandas():
    """O ATR em numpy tem que dar EXATAMENTE o mesmo valor da expressao pandas.

    A otimizacao trocou `pd.concat([...], axis=1).max(axis=1)` por `np.fmax`. A escolha
    da funcao numpy importa: `np.maximum` (o que o PR #9 usava) PROPAGA NaN enquanto o
    pandas IGNORA. Como a linha 0 tem NaN em `close.shift()`, `np.maximum` produziria
    NaN onde o pandas produz numero -- e isso desloca a janela rolativa de 14 linhas.
    O ATR alimenta o gate `EXTREME` do Risk Manager, entao a diferenca vira decisao.
    """
    for n in (50, 200):
        for com_gap in (False, True):
            df = _candles_sinteticos(n, com_gap=com_gap)
            esperado = _atr_referencia_pandas(df)

            calculate_technical_status(df)  # escreve df["ATR"] in-place
            obtido = df["ATR"]

            assert len(obtido) == len(esperado)
            divergentes = (obtido - esperado).abs() > 1e-9
            assert not divergentes.any(), (
                f"n={n} gap={com_gap}: ATR divergiu em {int(divergentes.sum())} posicoes, "
                f"primeira em {divergentes.idxmax()} "
                f"(esperado {esperado[divergentes.idxmax()]}, veio {obtido[divergentes.idxmax()]})"
            )

    # A linha 0 e o caso que expoe np.maximum: NaN em close.shift().
    df = _candles_sinteticos(50)
    ref_row0 = float(_atr_referencia_pandas(df).iloc[0])
    calculate_technical_status(df)
    assert abs(float(df["ATR"].iloc[0]) - ref_row0) < 1e-9, (
        f"linha 0: esperado {ref_row0}, veio {df['ATR'].iloc[0]} "
        "(np.maximum deixaria NaN aqui e deslocaria a janela)"
    )
    print("[PASS] ATR: numpy == pandas, inclusive na linha 0 e com gap de NaN.")


def _oracle_wilder_rsi(closes, period=14):
    """RSI de Wilder (1978) em loop explicito, sem pandas -- o oraculo dos testes.

    avg[period] = media dos primeiros `period` ganhos/perdas   (semente SMA)
    avg[i]      = (avg[i-1]*(period-1) + x[i]) / period        (recursao)

    Escrito a mao de proposito: se a versao vetorizada em pandas divergir disto,
    o teste falha. Retorna None onde o RSI nao esta definido.
    """
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out

    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    def resolve(avg_g, avg_l):
        if avg_l == 0:
            return 100.0 if avg_g > 0 else 50.0
        if avg_g == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + avg_g / avg_l)

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out[period] = resolve(avg_g, avg_l)

    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = resolve(avg_g, avg_l)
    return out


def test_rsi_wilder_matches_oracle():
    """O RSI vetorizado tem que bater com o oraculo de Wilder em loop explicito.

    Sem isto a definicao do indicador nao e testada: a suite antiga so verificava
    que uma alta forte da OVERBOUGHT, o que media simples e Wilder satisfazem igual.
    """
    series = {
        "alta monotona": [100.0 + i for i in range(40)],
        "queda monotona": [100.0 - i for i in range(40)],
        "serie plana": [100.0] * 40,
        "sobe-desce-lateral": ([100 + i * 0.5 for i in range(20)]
                               + [110 - i * 0.8 for i in range(20)]
                               + [94 + (1.5 if i % 2 else -1.5) for i in range(20)]),
    }
    for label, closes in series.items():
        got = wilder_rsi(pd.Series(closes))
        ref = _oracle_wilder_rsi(closes)
        assert len(got) == len(closes), f"{label}: tamanho do retorno mudou"
        for i, expected in enumerate(ref):
            value = got.iloc[i]
            if expected is None:
                assert pd.isna(value), f"{label} idx {i}: esperado NaN, veio {value}"
            else:
                assert not pd.isna(value), f"{label} idx {i}: esperado {expected}, veio NaN"
                assert abs(float(value) - expected) < 1e-9, (
                    f"{label} idx {i}: esperado {expected}, veio {value}"
                )
    print("[PASS] RSI Wilder: bate com o oraculo em 4 series, valor a valor.")


def test_rsi_wilder_reference_values():
    """Casos de borda com resposta conhecida, mais a serie do proprio Wilder."""
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    # hand-check: avg_gain = 3.34/14, avg_loss = 1.40/14
    expected = 100.0 - 100.0 / (1.0 + 3.34 / 1.40)
    got = float(wilder_rsi(pd.Series(closes)).iloc[-1])
    assert abs(got - expected) < 1e-9, f"serie do Wilder: esperado {expected}, veio {got}"

    # Serie plana: sem ganho e sem perda -> 50, nao 100 nem NaN.
    flat = wilder_rsi(pd.Series([100.0] * 40))
    assert float(flat.iloc[-1]) == 50.0, f"serie plana deveria dar 50, veio {flat.iloc[-1]}"

    # Alta monotona: so ganho -> 100. Queda monotona: so perda -> 0.
    assert float(wilder_rsi(pd.Series([100.0 + i for i in range(40)])).iloc[-1]) == 100.0
    assert float(wilder_rsi(pd.Series([100.0 - i for i in range(40)])).iloc[-1]) == 0.0

    # Warmup: nao existe RSI antes de `period` variacoes.
    warm = wilder_rsi(pd.Series([100.0 + i for i in range(40)]))
    assert warm.iloc[:14].isna().all(), "as 14 primeiras posicoes deveriam ser NaN"
    assert not pd.isna(warm.iloc[14]), "a posicao 14 deveria ser o primeiro RSI definido"

    # Serie curta demais: tudo NaN, sem excecao.
    short = wilder_rsi(pd.Series([float(i) for i in range(10)]))
    assert len(short) == 10 and short.isna().all()
    print("[PASS] RSI Wilder: serie de referencia, serie plana, warmup e serie curta.")


def test_rsi_wilder_gap_behavior():
    """Gap (NaN) na serie nao pode virar NaN silencioso nem valor inventado.

    Comportamento documentado: a suavizacao de Wilder simplesmente NAO ATUALIZA na
    barra ausente, entao o valor e carregado adiante (carry-forward) em vez de virar
    NaN. E os valores depois do gap diferem dos de uma serie sem gap -- a barra que
    faltou nunca entra na media. Este teste trava as duas coisas.
    """
    base = [100.0 + (i % 7) - (i % 3) for i in range(40)]
    clean = pd.Series(base)
    gapped = clean.copy()
    gapped.iloc[20] = np.nan

    a, b = wilder_rsi(clean), wilder_rsi(gapped)

    assert len(a) == len(b) == len(clean), "o gap mudou o tamanho da serie"
    assert b.iloc[14:].notna().all(), "gap nao pode produzir NaN fora do warmup"

    # Carry-forward: a barra ausente congela o indicador.
    assert b.iloc[20] == b.iloc[19], "esperado carry-forward na barra ausente"

    # E o gap realmente remove informacao: a cauda diverge da serie limpa.
    assert abs(float(a.iloc[-1]) - float(b.iloc[-1])) > 1e-9, (
        "serie com gap deveria divergir da serie limpa"
    )
    print("[PASS] RSI Wilder: gap faz carry-forward e remove informacao, sem NaN silencioso.")


def test_live_and_ml_rsi_agree():
    """As duas implementacoes de RSI do repo tem que dar o mesmo numero.

    `features/indicators.py:wilder_rsi` alimenta o LLM e o Risk Manager;
    `ml/dataset.py:_rsi` alimenta a feature `rsi_14` do dataset e o baseline
    `rsi_mean_reversion`. Se elas divergirem, backtest e live medem coisas diferentes.

    NOTA: esta serie e sintetica de proposito. Ela prova que as duas funcoes
    concordam matematicamente, o que e o que este teste pode garantir sem banco.
    A comparacao sobre candles REAIS fica em `audit_rsi_definition.py`, que exige
    PostgreSQL e reporta tambem a divergencia de classificacao contra a SMA antiga.
    """
    closes = pd.Series([100.0 + (i % 11) * 1.3 - (i % 5) * 0.7 for i in range(120)])
    live = wilder_rsi(closes)
    ml = ml_rsi(closes)

    assert len(live) == len(ml) == len(closes)
    diff = (live - ml).abs().max()
    assert diff < 1e-9, f"as duas implementacoes divergem em {diff}"
    print("[PASS] RSI: caminho live e dataset de ML produzem o mesmo valor.")


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
    test_atr_numpy_matches_pandas()
    test_rsi_wilder_matches_oracle()
    test_rsi_wilder_reference_values()
    test_rsi_wilder_gap_behavior()
    test_live_and_ml_rsi_agree()
    print("\n>>> TODOS OS TESTES UNITÁRIOS PASSARAM <<<\n")

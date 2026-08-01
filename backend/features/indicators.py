import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend.core.database import get_connection, get_db_path

MIN_TECHNICAL_KLINES = 30


from backend.core import repository


def get_historical_klines(
    asset: str = "BTC/BRL",
    timeframe: str = "1m",
    limit: int = 100,
    as_of_timestamp: int | None = None,
) -> pd.DataFrame:
    """Busca as ultimas N klines do banco e converte para DataFrame do Pandas."""
    klines_list = repository.get_klines(asset, timeframe, limit, as_of_timestamp)
    df = pd.DataFrame(klines_list)

    if df.empty:
        return df

    return df.sort_values(by="timestamp").reset_index(drop=True)



def calculate_technical_status(df: pd.DataFrame, asset: str = "BTC/BRL", timeframe: str = "1m") -> dict:
    """Calcula indicadores nativamente em Pandas."""
    found_klines = len(df)
    if df.empty or found_klines < MIN_TECHNICAL_KLINES:
        return {
            "status": "ERROR",
            "message": (
                f"Dados insuficientes: {asset} {timeframe} encontrou "
                f"{found_klines}/{MIN_TECHNICAL_KLINES} candles em {get_db_path()}."
            ),
            "asset": asset,
            "timeframe": timeframe,
            "required_klines": MIN_TECHNICAL_KLINES,
            "found_klines": found_klines,
            "db_path": str(get_db_path()),
        }

    # RSI (14)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14, min_periods=1).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    macd_signal = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - macd_signal

    # ATR (14)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = true_range.rolling(window=14, min_periods=1).mean()

    # Bollinger Bands (20, 2)
    df["bb_middle"] = df["close"].rolling(window=20).mean()
    df["bb_std"] = df["close"].rolling(window=20).std()
    df["bb_upper"] = df["bb_middle"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_middle"] - 2 * df["bb_std"]

    # EMA Crossover (9, 21)
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    # Volume Profile: Mean Volume (20)
    df["vol_mean"] = df["volume"].rolling(window=20).mean()

    latest = df.iloc[-1]
    latest_close = float(latest["close"])

    rsi_val = latest.get("RSI", 50.0)
    macd_hist = latest.get("MACD_Hist", 0.0)
    atr_val = latest.get("ATR", 0.0)

    rsi_status = "NEUTRAL"
    if pd.notna(rsi_val):
        if rsi_val >= 70:
            rsi_status = "OVERBOUGHT"
        elif rsi_val <= 30:
            rsi_status = "OVERSOLD"

    macd_status = "NEUTRAL"
    if pd.notna(macd_hist) and len(df) > 1:
        prev_hist = df.iloc[-2].get("MACD_Hist", 0.0)
        if pd.notna(prev_hist):
            if macd_hist > 0 and macd_hist > prev_hist:
                macd_status = "BULLISH_EXPANDING"
            elif macd_hist < 0 and macd_hist < prev_hist:
                macd_status = "BEARISH_EXPANDING"

    # Bollinger Bands status & values
    bb_upper_val = latest.get("bb_upper")
    bb_middle_val = latest.get("bb_middle")
    bb_lower_val = latest.get("bb_lower")
    bb_status = "INSIDE"
    if pd.notna(bb_upper_val) and pd.notna(bb_lower_val):
        bb_upper_float = float(bb_upper_val)
        bb_middle_float = float(bb_middle_val)
        bb_lower_float = float(bb_lower_val)
        if latest_close > bb_upper_float:
            bb_status = "ABOVE_UPPER"
        elif latest_close < bb_lower_float:
            bb_status = "BELOW_LOWER"
    else:
        bb_upper_float = 0.0
        bb_middle_float = 0.0
        bb_lower_float = 0.0

    # EMA Crossover status & values
    latest_ema9 = latest.get("ema9")
    latest_ema21 = latest.get("ema21")
    ema_status = "NEUTRAL"
    if pd.notna(latest_ema9) and pd.notna(latest_ema21):
        latest_ema9_float = float(latest_ema9)
        latest_ema21_float = float(latest_ema21)
        if latest_ema9_float > latest_ema21_float:
            ema_status = "BULLISH"
        elif latest_ema9_float < latest_ema21_float:
            ema_status = "BEARISH"

        if len(df) > 1:
            prev_ema9 = df["ema9"].iloc[-2]
            prev_ema21 = df["ema21"].iloc[-2]
            if pd.notna(prev_ema9) and pd.notna(prev_ema21):
                prev_ema9_float = float(prev_ema9)
                prev_ema21_float = float(prev_ema21)
                if prev_ema9_float <= prev_ema21_float and latest_ema9_float > latest_ema21_float:
                    ema_status = "BULLISH_CROSS"
                elif prev_ema9_float >= prev_ema21_float and latest_ema9_float < latest_ema21_float:
                    ema_status = "BEARISH_CROSS"
    else:
        latest_ema9_float = 0.0
        latest_ema21_float = 0.0

    # Volume Profile: Spike & POC
    current_volume = float(latest.get("volume", 0.0))
    mean_volume_val = latest.get("vol_mean")
    mean_volume_float = float(mean_volume_val) if pd.notna(mean_volume_val) else 0.0
    is_volume_spike = current_volume > mean_volume_float * 2.0 if mean_volume_float > 0 else False

    min_price = float(df["low"].min())
    max_price = float(df["high"].max())

    if max_price > min_price:
        bin_width = (max_price - min_price) / 10.0
        bin_sums = [0.0] * 10
        for idx, row in df.iterrows():
            close_p = float(row["close"])
            vol = float(row["volume"])
            bin_idx = int((close_p - min_price) / bin_width)
            if bin_idx >= 10:
                bin_idx = 9
            elif bin_idx < 0:
                bin_idx = 0
            bin_sums[bin_idx] += vol

        max_bin_idx = 0
        max_vol_sum = -1.0
        for i in range(10):
            if bin_sums[i] > max_vol_sum:
                max_vol_sum = bin_sums[i]
                max_bin_idx = i

        poc_price = min_price + (max_bin_idx + 0.5) * bin_width
    else:
        poc_price = latest_close

    # Volatility ATR status & values
    atr_float = float(atr_val) if pd.notna(atr_val) else 0.0
    atr_status = "NORMAL"
    if latest_close > 0 and (atr_float / latest_close) > 0.05:
        atr_status = "EXTREME"

    return {
        "status": "OK",
        "current_price": latest_close,
        "rsi": {
            "value": round(float(rsi_val), 2) if pd.notna(rsi_val) else 50.0,
            "status": rsi_status,
        },
        "macd": {
            "histogram": round(float(macd_hist), 2) if pd.notna(macd_hist) else 0.0,
            "status": macd_status,
        },
        "bollinger_bands": {
            "upper": round(bb_upper_float, 2),
            "middle": round(bb_middle_float, 2),
            "lower": round(bb_lower_float, 2),
            "status": bb_status,
        },
        "ema_crossover": {
            "ema9": round(latest_ema9_float, 2),
            "ema21": round(latest_ema21_float, 2),
            "status": ema_status,
        },
        "volume_profile": {
            "current_volume": float(current_volume),
            "mean_volume": round(mean_volume_float, 2),
            "is_volume_spike": bool(is_volume_spike),
            "poc_price": round(poc_price, 2),
        },
        "volatility_atr": {
            "value": round(atr_float, 2),
            "status": atr_status,
        },
    }


if __name__ == "__main__":
    print("Testando Feature Engine...")
    df = get_historical_klines(limit=50)
    print(f"Linhas recuperadas: {len(df)}")
    status = calculate_technical_status(df)
    print("Status Qualitativo (Para o LLM):", status)

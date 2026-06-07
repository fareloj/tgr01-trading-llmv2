import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from core.database import get_connection, get_db_path

MIN_TECHNICAL_KLINES = 30


def get_historical_klines(
    asset: str = "BTC/BRL",
    timeframe: str = "1m",
    limit: int = 100,
    as_of_timestamp: int | None = None,
) -> pd.DataFrame:
    """Busca as ultimas N klines do banco e converte para DataFrame do Pandas."""
    conn = get_connection()
    as_of_filter = "AND timestamp <= ?" if as_of_timestamp is not None else ""
    params = [asset, timeframe]
    if as_of_timestamp is not None:
        params.append(as_of_timestamp)
    params.append(limit)
    query = f'''
        SELECT timestamp, open, high, low, close, volume
        FROM klines
        WHERE asset = ? AND timeframe = ?
        {as_of_filter}
        ORDER BY timestamp DESC
        LIMIT ?
    '''
    df = pd.read_sql_query(query, conn, params=tuple(params))
    conn.close()

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

    latest = df.iloc[-1]

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

    return {
        "status": "OK",
        "current_price": float(latest["close"]),
        "rsi": {
            "value": round(float(rsi_val), 2) if pd.notna(rsi_val) else 50.0,
            "status": rsi_status,
        },
        "macd": {
            "histogram": round(float(macd_hist), 2) if pd.notna(macd_hist) else 0.0,
            "status": macd_status,
        },
        "volatility_atr": round(float(atr_val), 2) if pd.notna(atr_val) else 0.0,
    }


if __name__ == "__main__":
    print("Testando Feature Engine...")
    df = get_historical_klines(limit=50)
    print(f"Linhas recuperadas: {len(df)}")
    status = calculate_technical_status(df)
    print("Status Qualitativo (Para o LLM):", status)

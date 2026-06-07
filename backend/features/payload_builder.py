import time
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
from core.database import get_connection, get_db_path
from features.indicators import get_historical_klines, calculate_technical_status

MARKET_DATA_STALE_SECONDS = 300
NEWS_STALE_SECONDS = 6 * 3600
FUTURE_NEWS_TOLERANCE_SECONDS = 300
NEGATIVE_NEWS_TERMS = {
    "ban",
    "crash",
    "hack",
    "investigacao",
    "liquidacao",
    "liquidacoes",
    "panic",
    "panico",
    "proibicao",
    "queda",
    "regulador",
    "saidas",
    "suspende",
}


def get_latest_news(hours: int = 24, limit: int = 5, as_of_timestamp: int | None = None) -> list:
    """Busca as notícias das últimas N horas no SQLite (Push cronológico, sem RAG Vectorial)."""
    conn = get_connection()
    cursor = conn.cursor()
    
    now = int(as_of_timestamp or time.time())
    time_threshold = now - (hours * 3600)
    max_allowed_timestamp = now + FUTURE_NEWS_TOLERANCE_SECONDS
    
    cursor.execute('''
        SELECT timestamp, headline, source
        FROM news
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (time_threshold, max_allowed_timestamp, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    news_list = []
    for row in rows:
        news_list.append({
            "timestamp": row["timestamp"],
            "headline": row["headline"],
            "source": row["source"]
        })
    return news_list


def build_data_health(df, recent_news: list, now: int | None = None) -> dict:
    """Resume frescor temporal dos dados usados pelo ciclo."""
    now = int(now or time.time())
    latest_kline_timestamp = int(df["timestamp"].max()) if not df.empty else None
    latest_news_timestamp = max((int(item["timestamp"]) for item in recent_news), default=None)

    kline_age_seconds = None
    if latest_kline_timestamp is not None:
        kline_age_seconds = max(0, now - latest_kline_timestamp)

    news_age_seconds = None
    if latest_news_timestamp is not None:
        news_age_seconds = max(0, now - latest_news_timestamp)

    return {
        "latest_kline_timestamp": latest_kline_timestamp,
        "kline_age_seconds": kline_age_seconds,
        "is_market_data_stale": kline_age_seconds is None or kline_age_seconds > MARKET_DATA_STALE_SECONDS,
        "market_data_stale_threshold_seconds": MARKET_DATA_STALE_SECONDS,
        "latest_news_timestamp": latest_news_timestamp,
        "news_age_seconds": news_age_seconds,
        "is_news_stale": news_age_seconds is None or news_age_seconds > NEWS_STALE_SECONDS,
        "news_stale_threshold_seconds": NEWS_STALE_SECONDS,
    }


def build_news_risk(recent_news: list) -> dict:
    """Detecta red flags simples em noticias sem usar LLM ou busca semantica."""
    matched_terms = set()
    matched_headlines = []

    for item in recent_news:
        headline = item.get("headline", "")
        normalized = headline.lower()
        headline_terms = sorted(term for term in NEGATIVE_NEWS_TERMS if term in normalized)
        if not headline_terms:
            continue
        matched_terms.update(headline_terms)
        matched_headlines.append(
            {
                "headline": headline,
                "source": item.get("source"),
                "matched_terms": headline_terms,
            }
        )

    if len(matched_headlines) >= 2:
        risk_level = "HIGH"
    elif matched_headlines:
        risk_level = "ELEVATED"
    else:
        risk_level = "NORMAL"

    return {
        "has_negative_red_flag": bool(matched_headlines),
        "risk_level": risk_level,
        "matched_terms": sorted(matched_terms),
        "matched_headlines": matched_headlines,
    }

def build_agent_payload(asset: str = "BTC/BRL", timeframe: str = "1m", as_of_timestamp: int | None = None) -> dict:
    """Monta o JSON final mastigado para injetar no LLM (O único agente mestre)."""
    
    # 1. Busca os Klines e converte matemática bruta em Status de texto
    df = get_historical_klines(asset=asset, timeframe=timeframe, limit=50, as_of_timestamp=as_of_timestamp)
    technical_context = calculate_technical_status(df, asset=asset, timeframe=timeframe)
    
    # Se der erro técnico (sem preços suficientes), nem perde tempo com notícia
    if technical_context.get("status") == "ERROR":
        return technical_context
        
    # 2. Busca as últimas notícias reais/mockadas
    recent_news = get_latest_news(hours=24, limit=5, as_of_timestamp=as_of_timestamp)
    data_health = build_data_health(df, recent_news, now=as_of_timestamp)
    news_risk = build_news_risk(recent_news)
    
    # 3. Busca saldos virtuais
    from core.database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BRL'")
    brl_row = cursor.fetchone()
    brl_balance = brl_row['amount'] if brl_row else 10000.0
    
    cursor.execute("SELECT amount FROM virtual_portfolio WHERE currency='BTC'")
    btc_row = cursor.fetchone()
    btc_balance = btc_row['amount'] if btc_row else 0.0
    conn.close()
    
    current_price = technical_context.get("current_price", 0.0)
    btc_value_in_brl = btc_balance * current_price
    total_equity = brl_balance + btc_value_in_brl
    
    current_exposure = (btc_value_in_brl / total_equity * 100) if total_equity > 0 else 0.0
    
    payload = {
        "technical_context": technical_context,
        "news_context": recent_news,
        "data_health": data_health,
        "news_risk": news_risk,
        "portfolio_context": {
            "current_exposure_percentage": round(current_exposure, 2), 
            "is_in_drawdown": total_equity < 10000.0,
            "max_allowed_risk_per_trade": 5.0 # Teto máximo (5%)
        }
    }
    return payload

if __name__ == "__main__":
    print("Testando Payload Builder...")
    print(f"DB path: {get_db_path()}")
    payload = build_agent_payload()
    import json
    print(json.dumps(payload, indent=2, ensure_ascii=False))

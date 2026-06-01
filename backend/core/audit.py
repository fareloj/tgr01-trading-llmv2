import json


def build_payload_snapshot(payload: dict) -> dict:
    """Keep the context needed to explain one trade log without storing the full prompt."""
    technical = payload.get("technical_context", {})
    data_health = payload.get("data_health", {})
    news_risk = payload.get("news_risk", {})
    portfolio = payload.get("portfolio_context", {})

    matched_headlines = []
    for item in news_risk.get("matched_headlines", [])[:5]:
        matched_headlines.append(
            {
                "headline": str(item.get("headline", ""))[:240],
                "source": item.get("source"),
                "matched_terms": item.get("matched_terms", []),
            }
        )

    recent_news = []
    for item in payload.get("news_context", [])[:5]:
        recent_news.append(
            {
                "timestamp": item.get("timestamp"),
                "source": item.get("source"),
                "headline": str(item.get("headline", ""))[:240],
            }
        )

    return {
        "schema_version": 1,
        "technical": {
            "current_price": technical.get("current_price"),
            "rsi_value": technical.get("rsi", {}).get("value"),
            "rsi_status": technical.get("rsi", {}).get("status"),
            "macd_histogram": technical.get("macd", {}).get("histogram"),
            "macd_status": technical.get("macd", {}).get("status"),
            "volatility_atr": technical.get("volatility_atr"),
        },
        "data_health": {
            "kline_age_seconds": data_health.get("kline_age_seconds"),
            "news_age_seconds": data_health.get("news_age_seconds"),
            "is_market_data_stale": data_health.get("is_market_data_stale"),
            "is_news_stale": data_health.get("is_news_stale"),
        },
        "news_risk": {
            "has_negative_red_flag": news_risk.get("has_negative_red_flag", False),
            "risk_level": news_risk.get("risk_level", "UNKNOWN"),
            "matched_terms": news_risk.get("matched_terms", []),
            "matched_headlines": matched_headlines,
        },
        "portfolio": {
            "current_exposure_percentage": portfolio.get("current_exposure_percentage"),
            "is_in_drawdown": portfolio.get("is_in_drawdown"),
            "max_allowed_risk_per_trade": portfolio.get("max_allowed_risk_per_trade"),
        },
        "recent_news": recent_news,
    }


def serialize_payload_snapshot(payload: dict) -> str:
    return json.dumps(build_payload_snapshot(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

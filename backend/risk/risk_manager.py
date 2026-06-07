import time

from core.database import get_connection


class RiskManager:
    def __init__(
        self,
        max_daily_drawdown: float = 10.0,
        max_exposure: float = 100.0,
        cooldown_minutes: int = 15,
    ):
        self.max_daily_drawdown = max_daily_drawdown
        self.max_exposure = max_exposure
        self.cooldown_minutes = cooldown_minutes

    def calculate_system_reliability(self, payload: dict) -> float:
        """
        Calcula o penalizador de confianca baseado na saude dos dados em tempo real.
        Retorna um valor entre 0.0 e 1.0.
        """
        reliability = 1.0

        news = payload.get("news_context", [])
        if len(news) == 0:
            print("[Risk] Aviso: Sem noticias recentes. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        data_health = payload.get("data_health", {})
        if data_health.get("is_market_data_stale"):
            print("[Risk] Aviso: Market data stale. Penalizando confiabilidade estrutural (x0.3).")
            reliability *= 0.3

        if data_health.get("is_news_stale"):
            print("[Risk] Aviso: Noticias stale. Penalizando confiabilidade estrutural (x0.6).")
            reliability *= 0.6

        news_risk = payload.get("news_risk", {})
        if news_risk.get("has_negative_red_flag"):
            print("[Risk] Aviso: Red flag negativa em noticias. Penalizando confiabilidade estrutural (x0.7).")
            reliability *= 0.7

        tech = payload.get("technical_context", {})
        atr = self._atr_value(tech)
        current_price = tech.get("current_price", 1.0)

        if current_price > 0 and (atr / current_price) > 0.05:
            print("[Risk] Aviso: Volatilidade extrema detectada. Penalizando (x0.5).")
            reliability *= 0.5

        return reliability

    def calculate_fractional_kelly(self, win_rate: float, risk_reward_ratio: float, fraction: float = 0.5) -> float:
        """
        Calcula o Kelly Fracionado para definir o tamanho seguro da aposta.
        Retorna a porcentagem da banca que deve ser alocada na ordem.
        """
        if win_rate <= 0 or risk_reward_ratio <= 0:
            return 0.0

        kelly_perc = win_rate - ((1 - win_rate) / risk_reward_ratio)

        if kelly_perc <= 0:
            return 0.0

        return kelly_perc * fraction * 100.0

    def evaluate_order(self, llm_action: str, llm_conviction: int, payload: dict, current_exposure: float) -> dict:
        """
        A muralha deterministica: onde o LLM e barrado pela matematica e pela saude dos dados.
        """
        action = llm_action.upper()

        if action == "HOLD":
            return {"action": "HOLD", "reason": "LLM sugeriu HOLD.", "executed_size": 0.0}

        if action not in {"BUY", "SELL"}:
            return {"action": "HOLD", "reason": f"LLM sugeriu acao invalida: {llm_action}", "executed_size": 0.0}

        directional_block = self._directional_gate(action, payload)
        if directional_block:
            return directional_block

        cooldown_block = self._cooldown_gate(action)
        if cooldown_block:
            return cooldown_block

        if llm_conviction < 70:
            return {
                "action": "HOLD",
                "reason": f"Conviccao bruta da IA insuficiente ({llm_conviction}%). Exige-se minimo de 70%.",
                "executed_size": 0.0,
            }

        news = payload.get("news_context", [])
        if len(news) == 0 and llm_conviction < 80:
            return {
                "action": "HOLD",
                "reason": f"Noticias velhas/ausentes. IA nao tem conviccao absoluta ({llm_conviction}% < 80%).",
                "executed_size": 0.0,
            }

        sys_rel = self.calculate_system_reliability(payload)
        hybrid_confidence = (llm_conviction / 100.0) * sys_rel

        if hybrid_confidence < 0.50:
            return {
                "action": "HOLD",
                "reason": f"Confianca Hibrida muito baixa ({hybrid_confidence * 100:.1f}%). Limiar e 50%.",
                "executed_size": 0.0,
            }

        if action == "BUY" and current_exposure >= self.max_exposure:
            return {
                "action": "HOLD",
                "reason": f"Teto de alocacao de portfolio ({self.max_exposure}%) atingido. Compras bloqueadas.",
                "executed_size": 0.0,
            }

        executed_size = 0.0
        max_allowed = payload.get("portfolio_context", {}).get("max_allowed_risk_per_trade", 5.0)
        if action == "BUY":
            raw_size = self.calculate_fractional_kelly(win_rate=0.55, risk_reward_ratio=1.5, fraction=0.5)
            executed_size = min(raw_size, max_allowed)

            if executed_size <= 0:
                return {"action": "HOLD", "reason": "Matematica de Kelly sugere lote nulo ou negativo.", "executed_size": 0.0}

            size_label = f"Tamanho do Kelly: {executed_size:.2f}%"

        if action == "SELL":
            executed_size = min(max_allowed, current_exposure)
            if executed_size <= 0:
                return {"action": "HOLD", "reason": "SELL bloqueado: portfolio sem exposicao em BTC.", "executed_size": 0.0}

            size_label = f"Reducao de exposicao: {executed_size:.2f}%"

        return {
            "action": action,
            "reason": f"Aprovado. Confianca Hibrida: {hybrid_confidence * 100:.1f}%. {size_label}",
            "executed_size": executed_size,
        }

    def _directional_gate(self, action: str, payload: dict) -> dict | None:
        data_health = payload.get("data_health", {})
        tech = payload.get("technical_context", {})
        rsi_status = tech.get("rsi", {}).get("status")
        macd_status = tech.get("macd", {}).get("status")
        atr_status = self._atr_status(tech)

        if data_health.get("is_market_data_stale"):
            return self._hold(f"Directional Gate: {action} bloqueado por market data stale")

        if action == "BUY":
            if data_health.get("is_news_stale"):
                return self._hold("Directional Gate: BUY bloqueado por noticias stale")
            news_risk = payload.get("news_risk", {})
            if news_risk.get("has_negative_red_flag"):
                terms = ", ".join(news_risk.get("matched_terms", [])) or "unknown"
                return self._hold(f"Directional Gate: BUY bloqueado por news red flag ({terms})")
            if rsi_status == "OVERBOUGHT":
                return self._hold("Directional Gate: BUY bloqueado por RSI OVERBOUGHT")
            if macd_status in {"BEARISH_EXPANDING", "BEARISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: BUY bloqueado por MACD {macd_status}")
            if atr_status == "EXTREME":
                return self._hold("Directional Gate: BUY bloqueado por ATR EXTREME")

        if action == "SELL":
            if rsi_status == "OVERSOLD":
                return self._hold("Directional Gate: SELL bloqueado por RSI OVERSOLD")
            if macd_status in {"BULLISH_EXPANDING", "BULLISH_DIVERGENCE"}:
                return self._hold(f"Directional Gate: SELL bloqueado por MACD {macd_status}")

        return None

    def _cooldown_gate(self, action: str) -> dict | None:
        if self.cooldown_minutes <= 0:
            return None

        cutoff = int(time.time()) - (self.cooldown_minutes * 60)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT timestamp
                FROM trade_logs
                WHERE action = ? AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (action, cutoff),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if row:
            return self._hold(f"Cooldown: {action} repetido nos ultimos {self.cooldown_minutes} minutos")

        return None

    def _hold(self, reason: str) -> dict:
        return {"action": "HOLD", "reason": reason, "executed_size": 0.0}

    def _atr_value(self, tech: dict) -> float:
        atr = tech.get("volatility_atr", 0.0)
        if isinstance(atr, dict):
            return float(atr.get("value", 0.0) or 0.0)
        return float(atr or 0.0)

    def _atr_status(self, tech: dict) -> str | None:
        atr = tech.get("volatility_atr")
        if isinstance(atr, dict):
            return atr.get("status")
        return None


if __name__ == "__main__":
    print("Testando Risk Manager e Confianca Hibrida...\n")

    mock_payload_ok = {
        "technical_context": {
            "current_price": 50000,
            "rsi": {"status": "NEUTRAL"},
            "macd": {"status": "BULLISH_EXPANDING"},
            "volatility_atr": 1000,
        },
        "news_context": [{"headline": "Noticia qualquer valendo 1"}],
        "data_health": {"is_market_data_stale": False, "is_news_stale": False},
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0},
    }

    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)

    res1 = rm.evaluate_order("BUY", 90, mock_payload_ok, current_exposure=30.0)
    print("Teste 1 (Tudo Perfeito):", res1)

    res2 = rm.evaluate_order("BUY", 95, mock_payload_ok, current_exposure=85.0)
    print("Teste 2 (Banca Cheia):", res2)

    mock_payload_empty = mock_payload_ok.copy()
    mock_payload_empty["news_context"] = []
    res3 = rm.evaluate_order("BUY", 60, mock_payload_empty, current_exposure=30.0)
    print("Teste 3 (LLM Incerto + Sem Noticia):", res3)

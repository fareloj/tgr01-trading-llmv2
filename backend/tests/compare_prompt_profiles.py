import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend.agents.contracts import DecisionOutput
from backend.agents.decision_agent import load_api_keys
from backend.features.payload_builder import build_news_risk
from backend.risk.risk_manager import RiskManager

load_dotenv(BACKEND_DIR / ".env")


PROMPT_PROFILES = {
    "conservative": """
Voce e um Decision Agent ultraconservador para BTC/BRL.
Retorne BUY ou SELL apenas quando dados tecnicos, noticias e data_health apontarem com clareza para a mesma direcao.
Se houver contradicao, noticia confusa, dado stale, RSI neutro sem confirmacao, ou MACD fraco, retorne HOLD.
Nunca calcule indicadores, sizing, Kelly, stop ou exposicao. Nao ultrapasse 20 palavras no reasoning.
Preencha decision_brief com no maximo 3 linhas curtas: decisao, base tecnica e contexto.
Retorne apenas JSON valido conforme schema.
""",
    "balanced": """
Voce e um Decision Agent balanceado para BTC/BRL, usado antes de um Risk Manager deterministico.
Sua funcao e propor a melhor acao direcional com base no payload; o Risk Manager decide se a ordem sera executada.
Market data stale e bloqueio forte: se data_health.is_market_data_stale=true, retorne HOLD.
News stale NAO e bloqueio automatico. Se data_health.is_news_stale=true, trate noticias como contexto fraco, reduza a conviction, e cite "noticias stale" no decision_brief.
Se news stale e sugerir BUY/SELL, conviction deve ser no maximo 60.
BUY pode ser sugerido quando market_data esta fresco, MACD esta BULLISH_EXPANDING, RSI nao esta OVERBOUGHT, news_risk nao e HIGH, e exposicao permite.
SELL pode ser sugerido quando market_data esta fresco, MACD esta BEARISH_EXPANDING, RSI nao esta OVERSOLD, e ha exposicao relevante.
News risk negativo HIGH contradiz BUY, mas pode confirmar SELL quando MACD esta BEARISH_EXPANDING, RSI nao esta OVERSOLD e existe exposicao.
Nesse alinhamento bearish forte e fresco, prefira SELL a HOLD.
RSI OVERSOLD sozinho nao autoriza BUY. Se MACD estiver BEARISH_EXPANDING ou BEARISH_DIVERGENCE, prefira HOLD.
RSI OVERBOUGHT sozinho nao autoriza SELL. Se MACD estiver BULLISH_EXPANDING, prefira HOLD.
Se sinais tecnicos forem neutros, conflitantes, ou dependerem apenas de noticia stale, retorne HOLD.
Use conviction 80 somente quando sinal tecnico principal, RSI, data_health e news_risk estiverem coerentes.
Use conviction 60 quando houver sinal tecnico bom mas contexto fraco, incluindo noticias stale.
Use conviction 50 ou menor para HOLD por conflito, ausencia de confirmacao, ou contexto fraco.
Nunca calcule indicadores, sizing, Kelly, stop ou exposicao. Nao ultrapasse 20 palavras no reasoning.
Preencha decision_brief com exatamente 3 linhas curtas:
Acao: diga BUY, SELL ou HOLD e o motivo principal.
Base tecnica: cite RSI valor/status, MACD hist/status e preco.
Contexto: cite market_stale, news_stale, news_risk e exposicao.
Retorne apenas JSON valido conforme schema.
""",
    "hybrid": """
Voce e um Decision Agent balanceado para BTC/BRL.
Use o contexto tecnico deterministico como sinal principal e noticias como filtro qualitativo.
BUY/SELL sao permitidos quando o sinal tecnico e coerente e nao ha red flags claras. Em duvida, HOLD.
Nunca calcule indicadores, sizing, Kelly, stop ou exposicao. Nao ultrapasse 20 palavras no reasoning.
Preencha decision_brief com no maximo 3 linhas curtas: decisao, base tecnica e contexto.
Retorne apenas JSON valido conforme schema.
""",
    "aggressive": """
Voce e um Decision Agent agressivo, mas ainda controlado por Risk Manager deterministico.
Pode sugerir BUY/SELL com sinais tecnicos iniciais, desde que data_health esteja fresco e nao exista red flag direta.
Nao invente dado ausente; se o payload for contraditorio ou stale, retorne HOLD.
Nunca calcule indicadores, sizing, Kelly, stop ou exposicao. Nao ultrapasse 20 palavras no reasoning.
Preencha decision_brief com no maximo 3 linhas curtas: decisao, base tecnica e contexto.
Retorne apenas JSON valido conforme schema.
""",
}


def base_payload() -> dict:
    return {
        "technical_context": {
            "status": "OK",
            "current_price": 400000.0,
            "rsi": {"value": 50.0, "status": "NEUTRAL"},
            "macd": {"histogram": 0.0, "status": "NEUTRAL"},
            "volatility_atr": 800.0,
        },
        "news_context": [
            {"timestamp": 1778379000, "headline": "Mercado cripto aguarda novos dados macroeconomicos.", "source": "Synthetic"}
        ],
        "data_health": {
            "latest_kline_timestamp": 1778379000,
            "kline_age_seconds": 60,
            "is_market_data_stale": False,
            "market_data_stale_threshold_seconds": 300,
            "latest_news_timestamp": 1778379000,
            "news_age_seconds": 120,
            "is_news_stale": False,
            "news_stale_threshold_seconds": 21600,
        },
        "news_risk": {"has_negative_red_flag": False, "risk_level": "NORMAL", "matched_terms": [], "matched_headlines": []},
        "portfolio_context": {
            "current_exposure_percentage": 20.0,
            "is_in_drawdown": False,
            "max_allowed_risk_per_trade": 5.0,
        },
    }


def synthetic_scenarios() -> dict:
    scenarios = {}

    bullish = base_payload()
    bullish["technical_context"]["rsi"] = {"value": 42.0, "status": "NEUTRAL"}
    bullish["technical_context"]["macd"] = {"histogram": 25.0, "status": "BULLISH_EXPANDING"}
    bullish["news_context"] = [
        {"timestamp": 1778379000, "headline": "Bitcoin ganha fluxo institucional apos alivio regulatorio.", "source": "Synthetic"},
        {"timestamp": 1778378900, "headline": "ETFs de cripto registram entradas liquidas pelo terceiro dia.", "source": "Synthetic"},
    ]
    scenarios["bullish_clean"] = bullish

    bearish = base_payload()
    bearish["technical_context"]["rsi"] = {"value": 58.0, "status": "NEUTRAL"}
    bearish["technical_context"]["macd"] = {"histogram": -28.0, "status": "BEARISH_EXPANDING"}
    bearish["news_context"] = [
        {"timestamp": 1778379000, "headline": "Regulador anuncia investigacao ampla contra grandes exchanges.", "source": "Synthetic"},
        {"timestamp": 1778378900, "headline": "Saidas de fundos cripto aceleram durante aversao a risco.", "source": "Synthetic"},
    ]
    bearish["portfolio_context"]["current_exposure_percentage"] = 45.0
    scenarios["bearish_clean"] = bearish

    contradictory = base_payload()
    contradictory["technical_context"]["rsi"] = {"value": 46.0, "status": "NEUTRAL"}
    contradictory["technical_context"]["macd"] = {"histogram": 18.0, "status": "BULLISH_EXPANDING"}
    contradictory["news_context"] = [
        {"timestamp": 1778379000, "headline": "Bitcoin sobe com forte volume institucional.", "source": "Synthetic"},
        {"timestamp": 1778378900, "headline": "Rumores de proibicao regional de criptomoedas elevam incerteza.", "source": "Synthetic"},
    ]
    scenarios["contradictory"] = contradictory

    no_news = base_payload()
    no_news["technical_context"]["macd"] = {"histogram": 22.0, "status": "BULLISH_EXPANDING"}
    no_news["news_context"] = []
    no_news["data_health"]["latest_news_timestamp"] = None
    no_news["data_health"]["news_age_seconds"] = None
    no_news["data_health"]["is_news_stale"] = True
    scenarios["no_news"] = no_news

    flash_crash = base_payload()
    flash_crash["technical_context"]["rsi"] = {"value": 22.0, "status": "OVERSOLD"}
    flash_crash["technical_context"]["macd"] = {"histogram": -90.0, "status": "BEARISH_EXPANDING"}
    flash_crash["technical_context"]["volatility_atr"] = {"value": 26000.0, "status": "EXTREME"}
    flash_crash["news_context"] = [
        {"timestamp": 1778379000, "headline": "BTC cai 12% em uma hora apos liquidacoes em cascata.", "source": "Synthetic"},
    ]
    scenarios["flash_crash"] = flash_crash

    for payload in scenarios.values():
        payload["news_risk"] = build_news_risk(payload.get("news_context", []))

    return scenarios


class PromptProfileRunner:
    def __init__(self):
        self.api_keys = load_api_keys()
        if not self.api_keys:
            raise RuntimeError("Nenhuma chave Groq configurada em backend/.env; este harness precisa chamar o LLM.")
        self.key_index = 0
        self.base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self.client = self._build_client()
        self.model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        self.schema_instructions = (
            "Retorne APENAS um JSON valido seguindo este schema:\n"
            f"{json.dumps(DecisionOutput.model_json_schema())}"
        )

    def _build_client(self):
        return OpenAI(api_key=self.api_keys[self.key_index], base_url=self.base_url)

    def _is_local_provider(self) -> bool:
        """Local OpenAI-compatible servers (e.g. LM Studio) require a strict json_schema
        response_format; hosted Groq/OpenAI-style endpoints accept the looser json_object mode."""
        base_url = (self.base_url or "").lower()
        return "localhost" in base_url or "127.0.0.1" in base_url

    def _response_format(self) -> dict:
        if self._is_local_provider():
            return {
                "type": "json_schema",
                "json_schema": {"name": "decision_output", "schema": DecisionOutput.model_json_schema()},
            }
        return {"type": "json_object"}

    def _rotate_key(self) -> bool:
        if self.key_index + 1 >= len(self.api_keys):
            return False
        self.key_index += 1
        self.client = self._build_client()
        return True

    def _request_limits(self) -> dict:
        # gpt-oss is a heavy chain-of-thought reasoner: without reasoning_effort=low and a
        # larger completion budget it reliably spends its whole token budget on internal
        # reasoning and returns empty/truncated content. Match both naming conventions --
        # "openai/gpt-oss-*" (LM Studio/HF style) and "gpt-oss:*" (Ollama style) -- so this
        # doesn't silently regress to the 450-token default when only the model tag differs.
        if self.model.startswith("openai/gpt-oss") or self.model.startswith("gpt-oss:"):
            return {"max_completion_tokens": 600, "reasoning_effort": "low"}
        if self._is_local_provider():
            # Every locally/self-hosted reasoning model tried so far (Nemotron, Qwen,
            # Bonsai, gpt-oss under any tag) reliably spends the whole 450-token hosted
            # default on hidden chain-of-thought and returns empty/truncated content --
            # this is not gpt-oss-specific. Hosted Groq keeps the smaller default since it
            # has not exhibited this failure mode in this harness. reasoning_effort is a
            # gpt-oss-only parameter, so it is intentionally omitted here.
            return {"max_tokens": 1500}
        return {"max_tokens": 450}

    def run(self, profile: str, payload: dict) -> DecisionOutput:
        messages = [
            {"role": "system", "content": PROMPT_PROFILES[profile]},
            {
                "role": "user",
                "content": (
                    f"{self.schema_instructions}\n\n"
                    f"Payload de avaliacao:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ]
        last_error = None
        for _ in range(max(1, len(self.api_keys))):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=self._response_format(),
                    temperature=0.0,
                    **self._request_limits(),
                )
                return DecisionOutput.model_validate_json(response.choices[0].message.content)
            except Exception as error:
                last_error = error
                if type(error).__name__ in {"RateLimitError", "AuthenticationError"} and self._rotate_key():
                    continue
                raise
        raise RuntimeError(f"Prompt profile failed: {type(last_error).__name__}") from last_error


def evaluate_profile_matrix(profiles: list[str], scenarios: dict, include_payload: bool):
    runner = PromptProfileRunner()
    rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    results = []

    for scenario_name, scenario_payload in scenarios.items():
        for profile in profiles:
            payload = deepcopy(scenario_payload)
            decision = runner.run(profile, payload)
            final_order = rm.evaluate_order(
                llm_action=decision.action,
                llm_conviction=decision.conviction,
                payload=payload,
                current_exposure=payload["portfolio_context"]["current_exposure_percentage"],
            )
            results.append(
                {
                    "scenario": scenario_name,
                    "profile": profile,
                    "llm_action": decision.action,
                    "llm_conviction": decision.conviction,
                    "llm_reasoning": decision.reasoning,
                    "llm_decision_brief": decision.decision_brief,
                    "risk_action": final_order["action"],
                    "risk_reason": final_order["reason"],
                    "executed_size": final_order["executed_size"],
                    **({"payload": payload} if include_payload else {}),
                }
            )

    return results


def print_results(results: list[dict], as_json: bool):
    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    for row in results:
        print(
            f"{row['scenario']:<16} | {row['profile']:<12} | "
            f"LLM={row['llm_action']:<4} {row['llm_conviction']:>3}% | "
            f"RISK={row['risk_action']:<4} | "
            f"{row['llm_reasoning']} | {row['risk_reason']}"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Compare conservative/hybrid/aggressive prompts on synthetic dry-run scenarios.")
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=sorted(PROMPT_PROFILES),
        default=["conservative", "balanced", "hybrid", "aggressive"],
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON output.")
    parser.add_argument("--include-payload", action="store_true", help="Include synthetic payloads in JSON output.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    results = evaluate_profile_matrix(args.profiles, synthetic_scenarios(), include_payload=args.include_payload)
    print_results(results, as_json=args.json)

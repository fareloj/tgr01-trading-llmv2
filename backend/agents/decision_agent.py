import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from agents.contracts import DecisionOutput
from features.payload_builder import build_agent_payload

load_dotenv(BASE_DIR / ".env")

GENERIC_HOLD_REASONS = {
    "noticias confusas",
    "notícias confusas",
    "indicadores neutros",
    "mercado confuso",
    "rsi neutro",
    "sem direcao clara",
    "sem direção clara",
}

LLM_COOLDOWN_UNTIL = 0


def load_api_keys() -> list[str]:
    """Read LLM keys without printing secrets."""
    keys = []

    raw_keys = os.getenv("GROQ_API_KEYS", "")
    if raw_keys:
        keys.extend(part.strip() for part in re.split(r"[,;\n]+", raw_keys) if part.strip())

    single_key = os.getenv("GROQ_API_KEY", "").strip()
    if single_key:
        keys.append(single_key)

    for index in range(1, 11):
        key = os.getenv(f"GROQ_API_KEY_{index}", "").strip()
        if key:
            keys.append(key)

    unique_keys = []
    seen = set()
    for key in keys:
        if key not in seen:
            unique_keys.append(key)
            seen.add(key)
    return unique_keys


def has_llm_api_key() -> bool:
    return bool(load_api_keys())


def build_specific_hold_reason(payload: dict) -> str:
    """Build an auditable reason when the LLM returns a generic HOLD."""
    data_health = payload.get("data_health", {})
    if data_health.get("is_market_data_stale"):
        age = data_health.get("kline_age_seconds")
        return f"HOLD: market data stale ({age}s)."

    if data_health.get("is_news_stale"):
        age = data_health.get("news_age_seconds")
        return f"HOLD: noticias stale ({age}s)."

    news_risk = payload.get("news_risk", {})
    if news_risk.get("has_negative_red_flag"):
        terms = ", ".join(news_risk.get("matched_terms", [])[:3]) or "red flag"
        return f"HOLD: news risk {news_risk.get('risk_level', 'ELEVATED')} ({terms})."

    technical = payload.get("technical_context", {})
    rsi_status = technical.get("rsi", {}).get("status", "UNKNOWN")
    macd_status = technical.get("macd", {}).get("status", "UNKNOWN")
    return f"HOLD: RSI {rsi_status}; MACD {macd_status}; sem alinhamento direcional."


def build_specific_decision_brief(payload: dict, action: str, reasoning: str) -> str:
    technical = payload.get("technical_context", {})
    data_health = payload.get("data_health", {})
    news_risk = payload.get("news_risk", {})
    portfolio = payload.get("portfolio_context", {})

    rsi = technical.get("rsi", {})
    macd = technical.get("macd", {})
    price = technical.get("current_price", "unknown")
    line_1 = f"Acao {action}: {reasoning}"
    line_2 = (
        f"Base tecnica: preco={price}, RSI={rsi.get('value')} {rsi.get('status')}, "
        f"MACD={macd.get('histogram')} {macd.get('status')}."
    )
    line_3 = (
        f"Contexto: market_stale={data_health.get('is_market_data_stale')}, "
        f"news_stale={data_health.get('is_news_stale')}, "
        f"news_risk={news_risk.get('risk_level')}, "
        f"exposure={portfolio.get('current_exposure_percentage')}%."
    )
    return "\n".join([line_1, line_2, line_3])


def replace_generic_hold_reason(decision: DecisionOutput, payload: dict) -> DecisionOutput:
    normalized = " ".join(decision.reasoning.strip().lower().rstrip(".").split())
    if decision.action == "HOLD" and normalized in GENERIC_HOLD_REASONS:
        reasoning = build_specific_hold_reason(payload)
        return decision.model_copy(
            update={
                "reasoning": reasoning,
                "decision_brief": build_specific_decision_brief(payload, decision.action, reasoning),
            }
        )
    if not decision.decision_brief.strip():
        return decision.model_copy(
            update={
                "decision_brief": build_specific_decision_brief(payload, decision.action, decision.reasoning),
            }
        )
    return decision


def format_llm_error(error: Exception) -> str:
    """Summarize LLM technical errors without storing payloads, keys, or long responses."""
    return f"LLM technical failure: {type(error).__name__}"


def parse_retry_seconds(error: Exception, default_seconds: int = 300) -> int:
    message = str(error)
    minute_second_match = re.search(r"try again in ([0-9.]+)m([0-9.]+)s", message, re.IGNORECASE)
    if minute_second_match:
        minutes = float(minute_second_match.group(1))
        seconds = float(minute_second_match.group(2))
        return max(1, int((minutes * 60) + seconds))

    match = re.search(r"try again in ([0-9.]+)(ms|s|m|h)", message, re.IGNORECASE)
    if not match:
        return default_seconds

    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "ms":
        return max(1, int(value / 1000))
    if unit == "s":
        return max(1, int(value))
    if unit == "m":
        return max(1, int(value * 60))
    if unit == "h":
        return max(1, int(value * 3600))
    return default_seconds


def set_llm_cooldown(error: Exception) -> int:
    global LLM_COOLDOWN_UNTIL
    retry_seconds = parse_retry_seconds(error)
    LLM_COOLDOWN_UNTIL = int(time.time()) + retry_seconds
    return retry_seconds


class DecisionAgent:
    def __init__(self):
        self.api_keys = load_api_keys()
        self.api_key_index = 0
        self.base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        self.model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        self.client = self._build_client()

    def _build_client(self):
        api_key = self.api_keys[self.api_key_index] if self.api_keys else ""
        return OpenAI(api_key=api_key, base_url=self.base_url)

    def _rotate_key(self) -> bool:
        if self.api_key_index + 1 >= len(self.api_keys):
            return False
        self.api_key_index += 1
        self.client = self._build_client()
        print(f"[Decision Agent] Alternando para chave LLM #{self.api_key_index + 1}.")
        return True

    def evaluate_market(self, payload: dict) -> DecisionOutput:
        """Send payload to the LLM and validate strict Pydantic output."""
        now = int(time.time())
        if now < LLM_COOLDOWN_UNTIL:
            remaining = LLM_COOLDOWN_UNTIL - now
            return DecisionOutput(
                action="HOLD",
                conviction=0,
                reasoning=f"LLM technical failure: RateLimitCooldown {remaining}s",
                decision_brief=(
                    "Acao HOLD: chamada LLM suspensa por rate limit.\n"
                    f"Base operacional: cooldown restante de {remaining}s.\n"
                    "Contexto: decisao defensiva para evitar operar sem validacao recente."
                ),
            )

        system_prompt = """
        Voce e o Decision Agent Mestre de um fundo quantitativo ultraconservador.
        Sua unica funcao e ler o Payload JSON contendo dados tecnicos mastigados e manchetes de noticias, e decidir entre BUY, SELL ou HOLD.
        Voce NUNCA opera no escuro. Se as noticias forem confusas ou os indicadores nao mostrarem direcao clara, devolva HOLD.
        NUNCA use reasoning generico como "noticias confusas", "indicadores neutros" ou "sem direcao clara".
        Para HOLD, cite pelo menos dois fatores objetivos: RSI, MACD, news_risk, data_health ou conflito entre sinais.
        O campo reasoning deve ser curto, com no maximo 20 palavras.
        O campo decision_brief deve ter no maximo 3 linhas curtas e explicar:
        1) por que escolheu a acao;
        2) quais dados tecnicos sustentam a acao;
        3) quais dados de noticias/saude/exposicao influenciaram a acao.
        RSI OVERSOLD sozinho NAO autoriza BUY. Se MACD estiver BEARISH_EXPANDING ou BEARISH_DIVERGENCE, prefira HOLD.
        Voce deve SEMPRE retornar um JSON perfeito respeitando o schema exigido.
        """

        schema_instructions = (
            "Retorne APENAS um JSON valido seguindo este formato:\n"
            f"{json.dumps(DecisionOutput.model_json_schema())}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"{schema_instructions}\n\nPayload do Mercado atual:\n{json.dumps(payload, ensure_ascii=False)}",
            },
        ]

        attempts = max(1, len(self.api_keys))
        last_error = None

        for _ in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.0,
                )
                raw_json = response.choices[0].message.content
                parsed_output = DecisionOutput.model_validate_json(raw_json)
                return replace_generic_hold_reason(parsed_output, payload)

            except Exception as error:
                last_error = error
                print(f"[Decision Agent] Erro ao consultar LLM ou parsing falhou: {error}")
                if type(error).__name__ in {"RateLimitError", "AuthenticationError"} and self._rotate_key():
                    continue
                break

        if last_error and type(last_error).__name__ == "RateLimitError":
            retry_seconds = set_llm_cooldown(last_error)
            print(
                "[Decision Agent] Rate limit ativo em todas as chaves disponiveis. "
                f"Pulando novas chamadas por {retry_seconds}s."
            )

        return DecisionOutput(
            action="HOLD",
            conviction=0,
            reasoning=format_llm_error(last_error or Exception("UnknownLLMError")),
            decision_brief=(
                "Acao HOLD: falha tecnica na chamada ou validacao do LLM.\n"
                "Base operacional: resposta ausente, invalida, rate limited ou erro de API.\n"
                "Contexto: decisao defensiva para impedir ordem sem decisao validada."
            ),
        )


if __name__ == "__main__":
    print("Testando o Decision Agent (requer chave no backend/.env)...")
    agent = DecisionAgent()
    mock_payload = build_agent_payload()

    print("\n[Payload mastigado que a IA vai ler]:")
    print(json.dumps(mock_payload, indent=2, ensure_ascii=False))

    if has_llm_api_key():
        print("\n[Consultando LLM...]")
        decisao = agent.evaluate_market(mock_payload)
        print("\n[Decisao retornada pelo Pydantic]:")
        print(decisao.model_dump_json(indent=2))
    else:
        print("\n[Aviso] Nenhuma chave LLM configurada. Teste offline finalizado.")

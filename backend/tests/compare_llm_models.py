import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "reports"
sys.path.insert(0, str(BACKEND_DIR))

from agents.contracts import DecisionOutput
from agents.decision_agent import load_api_keys, replace_generic_hold_reason
from features.payload_builder import build_agent_payload
from risk.risk_manager import RiskManager

load_dotenv(BACKEND_DIR / ".env")


CURRENT_SYSTEM_PROMPT = """
Voce e o Decision Agent Mestre de um fundo quantitativo ultraconservador.
Sua unica funcao e ler o Payload JSON contendo dados tecnicos mastigados e manchetes de noticias, e decidir entre BUY, SELL ou HOLD.
Voce NUNCA opera no escuro. Se as noticias forem confusas ou os indicadores nao mostrarem direcao clara, devolva HOLD.
NUNCA use reasoning generico como "noticias confusas", "indicadores neutros" ou "sem direcao clara".
Para HOLD, cite pelo menos dois fatores objetivos: RSI, MACD, news_risk, data_health ou conflito entre sinais.
NUNCA ultrapasse 20 palavras no reasoning.
Voce deve SEMPRE retornar um JSON perfeito respeitando o schema exigido.
"""


HARDENED_SYSTEM_PROMPT = (
    CURRENT_SYSTEM_PROMPT
    + "\nRSI OVERSOLD sozinho NAO autoriza BUY. Se MACD estiver BEARISH_EXPANDING ou BEARISH_DIVERGENCE, prefira HOLD.\n"
)


def build_messages(payload: dict, prompt_mode: str) -> list[dict]:
    system_prompt = HARDENED_SYSTEM_PROMPT if prompt_mode == "hardened" else CURRENT_SYSTEM_PROMPT
    schema_instructions = (
        "Retorne APENAS um JSON valido seguindo este formato:\n"
        f"{json.dumps(DecisionOutput.model_json_schema())}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"{schema_instructions}\n\nPayload do Mercado atual:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]


def provider_client(provider: str) -> tuple[OpenAI | None, str]:
    provider = provider.lower()
    if provider == "groq":
        keys = load_api_keys()
        key = keys[0] if keys else ""
        base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    elif provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    else:
        raise ValueError(f"Provider desconhecido: {provider}")

    if not key:
        return None, f"Sem API key para provider {provider}"

    return OpenAI(api_key=key, base_url=base_url), ""


def extract_json_object(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            _, end = decoder.raw_decode(text[match.start() :])
            return text[match.start() : match.start() + end]
        except json.JSONDecodeError:
            continue

    raise ValueError("Nenhum objeto JSON encontrado na resposta do modelo.")


def scenario_payloads(base_payload: dict) -> dict[str, dict]:
    scenarios = {"real_current": json.loads(json.dumps(base_payload))}

    oversold_bearish = json.loads(json.dumps(base_payload))
    oversold_bearish["technical_context"]["rsi"]["value"] = 29.5
    oversold_bearish["technical_context"]["rsi"]["status"] = "OVERSOLD"
    oversold_bearish["technical_context"]["macd"]["histogram"] = -25.0
    oversold_bearish["technical_context"]["macd"]["status"] = "BEARISH_EXPANDING"
    oversold_bearish["data_health"]["is_market_data_stale"] = False
    oversold_bearish["data_health"]["is_news_stale"] = False
    scenarios["synthetic_oversold_bearish"] = oversold_bearish

    oversold_bullish = json.loads(json.dumps(base_payload))
    oversold_bullish["technical_context"]["rsi"]["value"] = 29.5
    oversold_bullish["technical_context"]["rsi"]["status"] = "OVERSOLD"
    oversold_bullish["technical_context"]["macd"]["histogram"] = 25.0
    oversold_bullish["technical_context"]["macd"]["status"] = "BULLISH_EXPANDING"
    oversold_bullish["data_health"]["is_market_data_stale"] = False
    oversold_bullish["data_health"]["is_news_stale"] = False
    scenarios["synthetic_oversold_bullish"] = oversold_bullish

    return scenarios


def call_model(provider: str, model: str, payload: dict, prompt_mode: str) -> dict:
    client, error = provider_client(provider)
    if not client:
        return {
            "provider": provider,
            "model": model,
            "prompt_mode": prompt_mode,
            "status": "SKIPPED",
            "error": error,
        }

    started = time.time()
    try:
        is_gpt_oss = model.startswith("openai/gpt-oss")
        request = {
            "model": model,
            "messages": build_messages(payload, prompt_mode),
            "temperature": 0.0,
        }
        if is_gpt_oss:
            # Groq's GPT-OSS models spend completion budget on reasoning before
            # emitting content. A small budget can produce no JSON at all.
            # Keep the default below the common 8k TPM tier; override with
            # GPT_OSS_MAX_COMPLETION_TOKENS if your Groq tier changes.
            request["max_completion_tokens"] = int(os.getenv("GPT_OSS_MAX_COMPLETION_TOKENS", "6911"))
            request["reasoning_effort"] = os.getenv("GPT_OSS_REASONING_EFFORT", "low")
        else:
            request["max_tokens"] = 220

        used_json_mode = True
        try:
            response = client.chat.completions.create(
                **request,
                response_format={"type": "json_object"},
            )
        except Exception as json_error:
            if "json_validate_failed" not in str(json_error):
                raise
            used_json_mode = False
            response = client.chat.completions.create(**request)

        raw = response.choices[0].message.content or ""
        json_text = raw if used_json_mode else extract_json_object(raw)
        decision = DecisionOutput.model_validate_json(json_text)
        decision = replace_generic_hold_reason(decision, payload)
        return {
            "provider": provider,
            "model": model,
            "prompt_mode": prompt_mode,
            "status": "OK",
            "latency_seconds": round(time.time() - started, 3),
            "used_json_mode": used_json_mode,
            "max_completion_tokens": request.get("max_completion_tokens", request.get("max_tokens")),
            "reasoning_effort": request.get("reasoning_effort"),
            "raw": raw,
            "decision": decision.model_dump(),
        }
    except Exception as exc:
        return {
            "provider": provider,
            "model": model,
            "prompt_mode": prompt_mode,
            "status": "ERROR",
            "latency_seconds": round(time.time() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def parse_model_spec(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        raise ValueError(f"Modelo precisa estar no formato provider:model. Recebido: {spec}")
    provider, model = spec.split(":", 1)
    return provider.strip(), model.strip()


def evaluate_with_risk(payload: dict, decision: dict) -> dict:
    risk = RiskManager(max_exposure=80.0, cooldown_minutes=0)
    exposure = payload.get("portfolio_context", {}).get("current_exposure_percentage", 0.0)
    return risk.evaluate_order(
        decision.get("action", "HOLD"),
        int(decision.get("conviction", 0) or 0),
        payload,
        current_exposure=float(exposure or 0.0),
    )


def write_markdown(results: list[dict], output: Path) -> None:
    lines = [
        "# Comparacao de Modelos LLM",
        "",
        "Teste isolado: nenhum trade foi executado. O Risk Manager foi simulado com cooldown desligado.",
        "",
    ]
    for item in results:
        lines.append(f"## {item['scenario']} / {item['provider']} / {item['model']} / {item.get('prompt_mode')}")
        lines.append("")
        if item["status"] != "OK":
            lines.append(f"- Status: {item['status']}")
            lines.append(f"- Erro: `{item.get('error', '')}`")
            lines.append("")
            continue
        decision = item["decision"]
        risk = item["risk_result"]
        lines.append(f"- LLM: {decision.get('action')} {decision.get('conviction')}%")
        lines.append(f"- Reasoning LLM: {decision.get('reasoning')}")
        lines.append(f"- Risk final: {risk.get('action')}")
        lines.append(f"- Reasoning Risk: {risk.get('reason')}")
        lines.append(f"- JSON mode: {item.get('used_json_mode')}")
        lines.append(f"- Max completion tokens: {item.get('max_completion_tokens')}")
        if item.get("reasoning_effort"):
            lines.append(f"- Reasoning effort: {item.get('reasoning_effort')}")
        lines.append(f"- Latencia: {item.get('latency_seconds')}s")
        lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Compare Decision Agent outputs across LLM providers/models.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "groq:llama-3.3-70b-versatile",
            "groq:openai/gpt-oss-120b",
        ],
        help="Model specs in provider:model format. Providers: groq, openrouter.",
    )
    parser.add_argument("--output-json", default=str(REPORTS_DIR / "last_model_comparison.json"))
    parser.add_argument("--output-md", default=str(REPORTS_DIR / "last_model_comparison.md"))
    parser.add_argument(
        "--prompt-mode",
        choices=["current", "hardened"],
        default="current",
        help="current reproduces the production prompt; hardened adds an explicit oversold/MACD rule.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    base_payload = build_agent_payload()
    if base_payload.get("status") == "ERROR":
        raise RuntimeError(f"Payload invalido: {base_payload}")

    scenarios = scenario_payloads(base_payload)
    results = []
    for scenario, payload in scenarios.items():
        print(f"\n[Scenario] {scenario}")
        for spec in args.models:
            provider, model = parse_model_spec(spec)
            print(f"  -> {provider}:{model}")
            result = call_model(provider, model, payload, args.prompt_mode)
            result["scenario"] = scenario
            if result["status"] == "OK":
                result["risk_result"] = evaluate_with_risk(payload, result["decision"])
            results.append(result)

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    output_md = Path(args.output_md)
    write_markdown(results, output_md)
    print(f"\nJSON salvo em: {output_json.resolve()}")
    print(f"Markdown salvo em: {output_md.resolve()}")


if __name__ == "__main__":
    main()

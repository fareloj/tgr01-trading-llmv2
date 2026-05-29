import argparse
from collections import Counter
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BACKEND_DIR / "reports"
sys.path.insert(0, str(BACKEND_DIR))

from agents.decision_agent import load_api_keys

load_dotenv(BACKEND_DIR / ".env")


def build_client():
    fallback_keys = load_api_keys()
    api_key = (
        os.getenv("REVIEW_LLM_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or (fallback_keys[0] if fallback_keys else "")
    )
    base_url = os.getenv("REVIEW_LLM_BASE_URL") or os.getenv("OPENROUTER_BASE_URL") or os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    return OpenAI(api_key=api_key, base_url=base_url), bool(api_key)


def _short_eval(item: dict) -> dict:
    horizons = item.get("horizons", {})
    return {
        "id": item.get("id"),
        "llm_action": item.get("llm_action"),
        "final_action": item.get("action"),
        "llm_reasoning": item.get("llm_reasoning"),
        "risk_reasoning": item.get("reasoning"),
        "price": item.get("execution_price"),
        "confidence": item.get("final_confidence"),
        "horizons": horizons,
    }


def build_compact_report(report: dict, max_examples: int = 12) -> dict:
    evaluations = report.get("evaluations", [])
    final_actions = Counter(item.get("action") for item in evaluations)
    llm_to_final = Counter((item.get("llm_action"), item.get("action")) for item in evaluations)
    reasons = Counter(item.get("reasoning") for item in evaluations)
    llm_reasons = Counter(item.get("llm_reasoning") for item in evaluations)

    interesting = []
    for item in evaluations:
        reason = str(item.get("reasoning") or "")
        horizons = item.get("horizons", {})
        statuses = {str(k): (v or {}).get("status") for k, v in horizons.items()}
        if (
            item.get("action") == "BUY"
            or item.get("llm_action") != item.get("action")
            or any(v in {"bad", "missed_upside"} for v in statuses.values())
            or "Directional Gate" in reason
            or "Cooldown" in reason
        ):
            interesting.append(_short_eval(item))

    recent = [_short_eval(item) for item in evaluations[-min(8, len(evaluations)):]]

    return {
        "db_path": report.get("db_path"),
        "since_id": report.get("since_id"),
        "threshold_pct": report.get("threshold_pct"),
        "horizons_minutes": report.get("horizons_minutes"),
        "logs_evaluated": report.get("logs_evaluated"),
        "summary": report.get("summary"),
        "final_actions": dict(final_actions.most_common()),
        "llm_to_final": {f"{k[0]}->{k[1]}": v for k, v in llm_to_final.most_common()},
        "top_risk_reasons": dict(reasons.most_common(12)),
        "top_llm_reasons": dict(llm_reasons.most_common(12)),
        "interesting_examples": interesting[:max_examples],
        "recent_examples": recent,
        "notes": [
            "Este e um resumo compacto; o JSON completo fica com a avaliacao deterministica.",
            "O revisor LLM nao deve recalcular indicadores nem precos.",
        ],
    }


def offline_markdown(report: dict, error_message: str) -> str:
    summary = report.get("summary", {})
    return "\n".join(
        [
            "# Revisao LLM nao executada",
            "",
            f"A chamada ao LLM falhou: `{error_message}`",
            "",
            "O relatorio deterministico foi gerado com sucesso. Use este resumo enquanto a cota/API nao estiver disponivel.",
            "",
            "## Resumo deterministico",
            "",
            f"- Logs avaliados: {report.get('logs_evaluated')}",
            f"- Threshold: +/-{report.get('threshold_pct')}%",
            f"- Horizontes: {report.get('horizons_minutes')}",
            f"- 5m: {summary.get('5')}",
            f"- 15m: {summary.get('15')}",
            f"- 30m: {summary.get('30')}",
            f"- 60m: {summary.get('60')}",
            "",
            "## Leitura preliminar",
            "",
            "- O teste favoreceu HOLD em queda/mercado fraco.",
            "- O Directional Gate bloqueou varios BUY contra MACD bearish.",
            "- Houve BUY aprovado quando o bloqueio tecnico deixou de existir.",
            "- A revisao qualitativa deve focar se `RSI oversold` esta gerando BUY cedo demais.",
        ]
    )


def review_report(report: dict, *, full_report: bool = False, max_examples: int = 12) -> str:
    client, has_key = build_client()
    if not has_key:
        raise RuntimeError("Nenhuma chave REVIEW_LLM_API_KEY, OPENROUTER_API_KEY ou GROQ_API_KEY configurada.")

    model = os.getenv("REVIEW_LLM_MODEL") or os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    review_input = report if full_report else build_compact_report(report, max_examples=max_examples)
    system_prompt = """
Voce e um auditor critico de um bot de trading paper BTC/BRL.
Voce NAO recalcula indicadores, precos ou metricas. Use apenas o relatorio deterministico fornecido.
Seu papel e avaliar se as conclusoes parecem coerentes, apontar limitacoes da metrica e sugerir melhorias de prompt/risk manager.
Se a amostra for pequena ou nao maturada, diga isso claramente.
Nao recomende trading real.
Responda em portugues, curto e estruturado.
"""
    user_prompt = (
        "Relatorio deterministico de decisoes em formato compacto:\n"
        f"{json.dumps(review_input, indent=2, ensure_ascii=False)}\n\n"
        "Gere uma revisao com: resumo, pontos fortes, possiveis erros, onde a IA poderia melhorar, "
        "onde o Risk Manager poderia melhorar, e proximos testes."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=1200,
    )
    return response.choices[0].message.content or ""


def parse_args():
    parser = argparse.ArgumentParser(description="Ask a review LLM to critique deterministic decision evaluation.")
    parser.add_argument("--input", required=True, help="JSON report generated by evaluate_decisions.py")
    parser.add_argument("--output", default="", help="Optional markdown output path.")
    parser.add_argument("--full-report", action="store_true", help="Send the complete JSON to the review LLM. Expensive; default sends a compact summary.")
    parser.add_argument("--max-examples", type=int, default=12, help="Max interesting examples to send in compact mode.")
    parser.add_argument("--offline", action="store_true", help="Do not call an LLM; write a deterministic fallback markdown.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report_path = Path(args.input)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if args.offline:
        markdown = offline_markdown(report, "modo offline solicitado")
    else:
        try:
            markdown = review_report(report, full_report=args.full_report, max_examples=args.max_examples)
        except OpenAIError as exc:
            markdown = offline_markdown(report, str(exc))
    print(markdown)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        print(f"\nReview salvo em: {output.resolve()}")

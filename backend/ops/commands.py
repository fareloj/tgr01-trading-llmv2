from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_DIR / "backend" / "reports"


@dataclass(frozen=True)
class CommandSpec:
    label: str
    args: tuple[str, ...]
    description: str
    requires_preflight: bool = False


def safe_since_id(value: str) -> str:
    return str(int(value)) if value.isdigit() and int(value) > 0 else "1"


def command_catalog(since_id: str = "1") -> dict[str, CommandSpec]:
    since_id = safe_since_id(since_id)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "diagnostics": CommandSpec(
            "Diagnostico SQLite",
            ("backend/core/database.py",),
            "Schema, contagens e heartbeats persistidos.",
        ),
        "start_workers": CommandSpec(
            "Iniciar workers",
            ("backend/tests/start_workers.py",),
            "Inicia workers reais ausentes em background.",
        ),
        "preflight": CommandSpec(
            "Preflight estrito",
            (
                "backend/tests/preflight_data_date.py",
                "--require-news-today",
                "--require-workers",
                "--require-clock-sync",
                "--max-kline-age-seconds",
                "300",
            ),
            "Valida relogio, candles, noticias e workers.",
        ),
        "paper10": CommandSpec(
            "Paper 10 ciclos",
            ("backend/tests/run_paper_trading.py", "--cycles", "10", "--sleep", "30"),
            "Teste curto em dados reais com intervalo de 30 segundos.",
            requires_preflight=True,
        ),
        "paper30": CommandSpec(
            "Paper 30 ciclos",
            ("backend/tests/run_paper_trading.py", "--cycles", "30", "--sleep", "30"),
            "Rodada intermediaria em dados reais.",
            requires_preflight=True,
        ),
        "paper30_60": CommandSpec(
            "Paper 30 ciclos / 60s",
            ("backend/tests/run_paper_trading.py", "--cycles", "30", "--sleep", "60"),
            "Rodada intermediaria conservadora em dados reais.",
            requires_preflight=True,
        ),
        "paper100": CommandSpec(
            "Paper 100 ciclos",
            ("backend/tests/run_paper_trading.py", "--cycles", "100", "--sleep", "60"),
            "Experimento longo. Use somente apos preflight.",
            requires_preflight=True,
        ),
        "experiment100_30": CommandSpec(
            "Experimento 100 / 30s",
            ("backend/ops/run_experiment.py", "--cycles", "100", "--sleep", "30"),
            "Executa 100 ciclos e gera o pacote completo de relatorios.",
        ),
        "experiment100_60": CommandSpec(
            "Experimento 100 / 60s",
            ("backend/ops/run_experiment.py", "--cycles", "100", "--sleep", "60"),
            "Executa 100 ciclos conservadores e gera todos os relatorios.",
        ),
        "logs": CommandSpec(
            "Analisar logs",
            ("backend/tests/analyze_trade_logs.py", "--since-id", since_id, "--limit", "30"),
            "Resumo limpo de decisoes e snapshots.",
        ),
        "entries": CommandSpec(
            "Avaliar entradas",
            (
                "backend/tests/analyze_entry_decisions.py",
                "--since-id",
                since_id,
                "--json-out",
                "backend/reports/last_entry_decisions.json",
            ),
            "Analisa BUY/SELL aprovados e bloqueados.",
        ),
        "future": CommandSpec(
            "Movimento futuro",
            (
                "backend/tests/evaluate_decisions.py",
                "--since-id",
                since_id,
                "--horizons",
                "5,15,30,60",
                "--threshold",
                "0.20",
                "--limit",
                "30",
                "--json-out",
                "backend/reports/last_decision_evaluation.json",
            ),
            "Compara decisoes com variacoes futuras sem verdade absoluta.",
        ),
        "readiness": CommandSpec(
            "Relatorio operacional",
            ("backend/tests/trading_readiness_report.py",),
            "Relatorio consolidado de prontidao.",
        ),
        "llm_review": CommandSpec(
            "Revisao LLM",
            (
                "backend/tests/llm_review_decisions.py",
                "--input",
                "backend/reports/last_decision_evaluation.json",
                "--output",
                "backend/reports/last_llm_review.md",
            ),
            "Segunda leitura do ultimo relatorio deterministico.",
        ),
        "rag_docs": CommandSpec(
            "RAG documentos",
            ("backend/tests/ingest_rag_sources.py", "--project-docs"),
            "Atualiza memoria local com documentacao curada do projeto.",
        ),
        "rag_news": CommandSpec(
            "RAG noticias 24h",
            ("backend/tests/ingest_rag_sources.py", "--news-hours", "24", "--news-limit", "50"),
            "Atualiza memoria local com noticias recentes persistidas.",
        ),
    }

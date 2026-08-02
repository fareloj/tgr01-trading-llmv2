import asyncio
import json
import os
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ops.commands import command_catalog
from backend.ops.process_control import terminate_process_tree


TUI_ACTION_ROWS = (
    (
        ("Diagnostico", "diagnostics"),
        ("Workers", "start_workers"),
        ("Preflight", "preflight"),
        ("Paper 10", "paper10"),
        ("Paper 30/30", "paper30"),
        ("Paper 30/60", "paper30_60"),
        ("Paper 100", "paper100"),
    ),
    (
        ("Eval 100/30", "experiment100_30"),
        ("Eval 100/60", "experiment100_60"),
        ("Logs", "logs"),
        ("Entradas", "entries"),
        ("Futuro", "future"),
        ("Readiness", "readiness"),
        ("Revisao LLM", "llm_review"),
    ),
    (
        ("RAG Docs", "rag_docs"),
        ("RAG News", "rag_news"),
        ("RAG Oficial", "external_rag"),
    ),
)


def age(seconds: int | None) -> str:
    if seconds is None:
        return "--"
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m"


class TradingOpsTui(App):
    TITLE = "TGR-01 Trading LLM V2"
    SUB_TITLE = "Operational TUI"
    CSS = """
    Screen { background: #0d1117; color: #d8e1e8; }
    Header { background: #101820; color: #67e8b1; }
    #health { height: 7; layout: horizontal; }
    .metric { width: 1fr; border: round #34414e; padding: 0 1; margin: 0 1 0 0; }
    #actions { height: 13; padding: 1 0; }
    .action-row { height: 3; }
    Button { margin: 0 1 0 0; min-width: 16; }
    Button.-primary { background: #176b4a; }
    #since { width: 22; margin: 0 1 1 0; }
    #content { height: 1fr; }
    #output { width: 3fr; border: round #34414e; background: #0a0f14; }
    #recent { width: 2fr; border: round #34414e; }
    #status { height: 3; border: round #34414e; padding: 0 1; }
    Footer { background: #101820; }
    """
    BINDINGS = [
        ("r", "refresh", "Atualizar"),
        ("x", "stop", "Parar processo"),
        ("q", "quit", "Sair"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.active_process: asyncio.subprocess.Process | None = None
        self.active_action = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("Carregando health operacional...", id="status")
            with Horizontal(id="health"):
                yield Static("PRICE WORKER\n--", classes="metric", id="price")
                yield Static("NEWS WORKER\n--", classes="metric", id="news")
                yield Static("CANDLE\n--", classes="metric", id="candle")
                yield Static("CLOCK SKEW\n--", classes="metric", id="clock")
                yield Static("PAPER RISK\n--", classes="metric", id="exposure")
                yield Static("RAG MEMORY\n--", classes="metric", id="rag")
            with Vertical(id="actions"):
                for row_index, row in enumerate(TUI_ACTION_ROWS):
                    with Horizontal(classes="action-row", id=f"action-row-{row_index}"):
                        if row_index == 1:
                            yield Input(value="1", placeholder="since-id", id="since", type="integer")
                        for label, action in row:
                            variant = "primary" if action == "preflight" else "default"
                            yield Button(label, id=action, variant=variant)
                        if row_index == 2:
                            yield Button("Parar", id="stop", variant="error", disabled=True)
            with Horizontal(id="content"):
                yield RichLog(id="output", highlight=True, markup=True, wrap=True)
                yield DataTable(id="recent", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#recent", DataTable)
        table.add_columns("ID", "LLM", "Final", "Conv.", "Reliab.", "Preco")
        self.query_one("#output", RichLog).write("[green][READY][/green] TUI operacional inicializada.")
        self.refresh_state()
        self.set_interval(30, self.refresh_state)

    async def action_refresh(self) -> None:
        self.refresh_state()

    async def action_stop(self) -> None:
        await self.stop_process()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop":
            await self.stop_process()
            return
        self.run_command(event.button.id or "")

    @work(exclusive=True, group="dashboard")
    async def refresh_state(self) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "backend/tests/dashboard_state.py",
                cwd=PROJECT_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(stderr.decode(errors="replace"))
            state = json.loads(stdout.decode("utf-8"))
            self.render_state(state)
        except Exception as exc:
            self.query_one("#status", Static).update(f"[red]Falha ao atualizar dashboard: {exc}[/red]")

    def render_state(self, state: dict) -> None:
        workers = state.get("workers", {})
        price = workers.get("price_worker", {})
        news = workers.get("news_worker", {})
        kline = state.get("latest_kline", {})
        clock = state.get("clock", {})
        portfolio = state.get("portfolio", {})
        rag = state.get("rag", {})
        self.query_one("#price", Static).update(f"PRICE WORKER\n{price.get('status', '--')} | {age(price.get('age_seconds'))}")
        self.query_one("#news", Static).update(f"NEWS WORKER\n{news.get('status', '--')} | {age(news.get('age_seconds'))}")
        self.query_one("#candle", Static).update(f"CANDLE BTC/BRL\nR$ {kline.get('close', 0):,.0f} | {age(kline.get('age_seconds'))}")
        self.query_one("#clock", Static).update(f"CLOCK SKEW\n{clock.get('skew_seconds', '--')}s | {clock.get('status', '--')}")
        drawdown = portfolio.get("daily_drawdown_pct")
        drawdown_label = "--" if drawdown is None else f"{drawdown:.2f}%"
        self.query_one("#exposure", Static).update(
            "PAPER RISK\n"
            f"DD {drawdown_label} | Exp {portfolio.get('exposure_pct', 0):.2f}% | "
            f"R$ {portfolio.get('equity_brl', 0):,.2f}"
        )
        self.query_one("#rag", Static).update(f"RAG MEMORY\n{rag.get('documents', 0)} docs | {rag.get('chunks', 0)} chunks")
        self.query_one("#status", Static).update(
            f"[green]Estado atualizado[/green] | DB {state.get('db_path')} | Use Preflight antes de paper trading."
        )
        table = self.query_one("#recent", DataTable)
        table.clear()
        for log in state.get("logs", [])[:12]:
            table.add_row(
                str(log.get("id", "")),
                str(log.get("llm_action", "")),
                str(log.get("action", "")),
                f"{log.get('llm_conviction', 0):.0f}%",
                f"{log.get('system_reliability', 0):.2f}",
                f"R$ {log.get('execution_price', 0):,.0f}",
            )

    @work(exclusive=True, group="command")
    async def run_command(self, action: str) -> None:
        if self.active_process:
            self.query_one("#output", RichLog).write("[yellow][WARN][/yellow] Ja existe um processo em execucao.")
            return
        since_id = self.query_one("#since", Input).value
        spec = command_catalog(since_id).get(action)
        if not spec:
            return
        log = self.query_one("#output", RichLog)
        log.write(f"\n[cyan][RUN][/cyan] {spec.label}\n[dim]{spec.description}[/dim]")
        self.active_action = action
        self.query_one("#stop", Button).disabled = False
        try:
            self.active_process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                "backend/ops/run_action.py",
                action,
                "--since-id",
                since_id,
                cwd=PROJECT_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            assert self.active_process.stdout
            async for raw_line in self.active_process.stdout:
                log.write(raw_line.decode("utf-8", errors="replace").rstrip())
            code = await self.active_process.wait()
            color = "green" if code == 0 else "red"
            log.write(f"[{color}][EXIT {code}][/{color}] {spec.label}")
        except Exception as exc:
            log.write(f"[red][ERROR][/red] {exc}")
        finally:
            self.active_process = None
            self.active_action = ""
            self.query_one("#stop", Button).disabled = True
            self.refresh_state()

    async def stop_process(self) -> None:
        if not self.active_process:
            return
        process = self.active_process
        self.query_one("#output", RichLog).write(f"[yellow][STOP][/yellow] Encerrando {self.active_action}...")
        await asyncio.to_thread(terminate_process_tree, process.pid)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()


if __name__ == "__main__":
    TradingOpsTui().run()

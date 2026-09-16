from __future__ import annotations

import asyncio

from textual.widgets import Button, DataTable, Input, Static

from backend.tui import TUI_ACTION_ROWS, TradingOpsTui


class TuiHarness(TradingOpsTui):
    def __init__(self) -> None:
        self.selected_action = None
        self.refresh_count = 0
        super().__init__()

    def refresh_state(self) -> None:
        self.refresh_count += 1

    def run_command(self, action: str) -> None:
        self.selected_action = action


def test_tui_mounts_actions_routes_clicks_and_renders_state():
    async def exercise() -> None:
        app = TuiHarness()
        async with app.run_test(size=(190, 52)) as pilot:
            await pilot.pause()
            expected_actions = {action for row in TUI_ACTION_ROWS for _, action in row}
            rendered_actions = {
                button.id for button in app.query(Button) if button.id not in {"stop"}
            }
            assert rendered_actions == expected_actions
            assert app.query_one("#since", Input).value == "1"
            assert app.query_one("#stop", Button).disabled is True

            await pilot.click("#diagnostics")
            assert app.selected_action == "diagnostics"

            app.render_state(
                {
                    "db_path": "paper.db",
                    "workers": {
                        "price_worker": {"status": "healthy", "age_seconds": 2},
                        "news_worker": {"status": "healthy", "age_seconds": 3},
                    },
                    "latest_kline": {"close": 400000.0, "age_seconds": 4},
                    "clock": {"skew_seconds": 1, "status": "OK"},
                    "portfolio": {
                        "exposure_pct": 5.0,
                        "equity_brl": 10000.0,
                        "daily_drawdown_pct": 1.25,
                    },
                    "rag": {"documents": 10, "chunks": 20},
                    "logs": [
                        {
                            "id": 1,
                            "llm_action": "HOLD",
                            "action": "HOLD",
                            "llm_conviction": 60.0,
                            "system_reliability": 1.0,
                            "execution_price": 400000.0,
                        }
                    ],
                }
            )
            assert app.query_one("#recent", DataTable).row_count == 1
            assert "paper.db" in str(app.query_one("#status", Static).render())
            assert "DD 1.25%" in str(app.query_one("#exposure", Static).render())

    asyncio.run(exercise())


def test_tui_renders_nullable_market_and_risk_fields():
    """A dashboard may legitimately report no candle and an unknown exposure.

    `dashboard_state` emits close=None when there is no candle, and
    exposure_pct=None when BTC is held without a price. `.get(key, default)`
    does not protect against a present-but-None value, so both must be handled
    explicitly or the TUI refresh raises.
    """
    async def exercise() -> None:
        app = TuiHarness()
        async with app.run_test(size=(190, 52)) as pilot:
            await pilot.pause()
            app.render_state(
                {
                    "db_path": "paper.db",
                    "workers": {
                        "price_worker": {"status": "missing", "age_seconds": None},
                        "news_worker": {"status": "missing", "age_seconds": None},
                    },
                    "latest_kline": {"close": None, "age_seconds": None},
                    "clock": {"skew_seconds": None, "status": "unknown"},
                    "portfolio": {
                        "exposure_pct": None,
                        "equity_brl": None,
                        "daily_drawdown_pct": None,
                    },
                    "rag": {"documents": 0, "chunks": 0},
                    "logs": [],
                }
            )
            candle = str(app.query_one("#candle", Static).render())
            exposure = str(app.query_one("#exposure", Static).render())
            assert "R$ --" in candle
            assert "Exp --" in exposure
            assert "DD --" in exposure

    asyncio.run(exercise())


def test_tui_renders_nullable_recent_log_columns():
    """Audit columns are nullable in the database, so a partial row can be None.

    llm_conviction, system_reliability and execution_price are all nullable in
    db_models. Formatting a None with a numeric spec raises and would abort the
    whole refresh.
    """
    async def exercise() -> None:
        app = TuiHarness()
        async with app.run_test(size=(190, 52)) as pilot:
            await pilot.pause()
            app.render_state(
                {
                    "db_path": "paper.db",
                    "workers": {},
                    "latest_kline": {"close": 400000.0, "age_seconds": 1},
                    "clock": {"skew_seconds": 0, "status": "OK"},
                    "portfolio": {"exposure_pct": 5.0, "equity_brl": 10000.0, "daily_drawdown_pct": 0.0},
                    "rag": {"documents": 0, "chunks": 0},
                    "logs": [
                        {
                            "id": 1,
                            "llm_action": "SKIPPED",
                            "action": "HOLD",
                            "llm_conviction": None,
                            "system_reliability": None,
                            "execution_price": None,
                        }
                    ],
                }
            )
            table = app.query_one("#recent", DataTable)
            assert table.row_count == 1
            # Assert the actual cells, not just that rendering did not raise.
            cells = [str(table.get_row_at(0)[index]) for index in (3, 4, 5)]
            assert cells == ["--", "--", "--"], cells

    asyncio.run(exercise())


def test_tui_still_renders_a_real_zero_close_and_exposure():
    """Zero is a legitimate value and must not be shown as unknown."""
    async def exercise() -> None:
        app = TuiHarness()
        async with app.run_test(size=(190, 52)) as pilot:
            await pilot.pause()
            app.render_state(
                {
                    "db_path": "paper.db",
                    "workers": {},
                    "latest_kline": {"close": 0.0, "age_seconds": 0},
                    "clock": {"skew_seconds": 0, "status": "OK"},
                    "portfolio": {"exposure_pct": 0.0, "equity_brl": 0.0, "daily_drawdown_pct": 0.0},
                    "rag": {"documents": 0, "chunks": 0},
                    "logs": [],
                }
            )
            candle = str(app.query_one("#candle", Static).render())
            exposure = str(app.query_one("#exposure", Static).render())
            assert "R$ 0" in candle
            assert "Exp 0.00%" in exposure

    asyncio.run(exercise())

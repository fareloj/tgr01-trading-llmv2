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

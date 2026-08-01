import assert from "node:assert/strict";
import test from "node:test";

import { escapeCsvCell, evaluationsToCsv } from "./csv.mjs";

test("CSV cells escape commas, quotes and newlines", () => {
  assert.equal(escapeCsvCell('MACD bullish, RSI "neutral"\nreview'), '"MACD bullish, RSI ""neutral""\nreview"');
});

test("evaluation CSV preserves zero moves and auditable reasons", () => {
  const csv = evaluationsToCsv([{
    id: 7,
    timestamp: 123,
    action: "HOLD",
    execution_price: 400000,
    reasoning: "Blocked, stale news",
    technical: { rsi_value: 50, macd_status: "NEUTRAL", volatility_atr: 12 },
    horizons: { "5": { status: "neutral", move_pct: 0 } }
  }], ["5"], value => `ts:${value}`);

  assert.match(csv, /5m Status,5m Move %/);
  assert.match(csv, /"Blocked, stale news"/);
  assert.match(csv, /neutral,0$/);
});

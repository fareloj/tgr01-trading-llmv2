import assert from "node:assert/strict";
import test from "node:test";

import {
  convictionOutlook,
  costHurdles,
  describeModelRoles,
  formatPercent,
  hurdleCoverage,
  summarizeDecisions,
  toFiniteNumber
} from "./cost.mjs";

test("toFiniteNumber treats null, blank and boolean as absent, not zero", () => {
  assert.equal(toFiniteNumber(null), null);
  assert.equal(toFiniteNumber(undefined), null);
  assert.equal(toFiniteNumber(""), null);
  assert.equal(toFiniteNumber(" "), null);
  assert.equal(toFiniteNumber("abc"), null);
  assert.equal(toFiniteNumber(true), null);
  assert.equal(toFiniteNumber(Symbol("x")), null);
  assert.equal(toFiniteNumber(NaN), null);
  assert.equal(toFiniteNumber(Infinity), null);
  assert.equal(toFiniteNumber(0), 0);
  assert.equal(toFiniteNumber("0.35"), 0.35);
  assert.equal(toFiniteNumber(-1), -1);
});

test("helpers accept null config instead of throwing", () => {
  assert.deepEqual(costHurdles(null), { oneSidePct: null, buyPct: null, sellPct: null });
  assert.deepEqual(convictionOutlook(null, true), { required: null, source: "unknown" });
  assert.deepEqual(describeModelRoles(null), []);
  assert.deepEqual(summarizeDecisions(null).total, 0);
  assert.equal(hurdleCoverage(0.47, null).ratio, null);
});

test("hurdleCoverage reports unknown for an extreme ratio instead of covered", () => {
  // A finite ratio check matters: MAX_VALUE / MIN_VALUE is Infinity, which must
  // not be reported as "covers the cost".
  const result = hurdleCoverage(Number.MAX_VALUE, Number.MIN_VALUE);

  assert.equal(result.ratio, null);
  assert.equal(result.covered, null);
});

test("formatPercent never prints NaN and keeps zero", () => {
  assert.equal(formatPercent(null), "--");
  assert.equal(formatPercent(undefined), "--");
  assert.equal(formatPercent(""), "--");
  assert.equal(formatPercent("abc"), "--");
  assert.equal(formatPercent(0), "0.00%");
  assert.equal(formatPercent(0.35), "0.35%");
  assert.equal(formatPercent(0.355, 3), "0.355%");
});

test("costHurdles derives BUY round-trip and SELL exit from the per-side cost", () => {
  const hurdles = costHurdles({
    one_side_cost_pct: 0.35,
    buy_round_trip_cost_pct: 0.7,
    sell_exit_cost_pct: 0.35
  });

  assert.equal(hurdles.buyPct, 0.7);
  assert.equal(hurdles.sellPct, 0.35);
  assert.equal(hurdles.buyPct, 2 * hurdles.oneSidePct);
});

test("costHurdles falls back to the per-side cost when totals are absent", () => {
  const hurdles = costHurdles({ one_side_cost_pct: 0.35 });

  assert.equal(hurdles.buyPct, 0.7);
  assert.equal(hurdles.sellPct, 0.35);
});

test("costHurdles treats explicit null totals as absent, not as zero cost", () => {
  const hurdles = costHurdles({
    one_side_cost_pct: 0.35,
    buy_round_trip_cost_pct: null,
    sell_exit_cost_pct: null
  });

  assert.equal(hurdles.buyPct, 0.7);
  assert.equal(hurdles.sellPct, 0.35);
});

test("costHurdles reports unknown instead of guessing when cost is missing", () => {
  const hurdles = costHurdles({});

  assert.equal(hurdles.oneSidePct, null);
  assert.equal(hurdles.buyPct, null);
  assert.equal(hurdles.sellPct, null);
});

test("hurdleCoverage flags a typical move that does not pay the cost", () => {
  // The 60m median move from the development diagnostic against the BUY hurdle.
  const uncovered = hurdleCoverage(0.47, 0.7);
  assert.equal(uncovered.covered, false);
  assert.ok(Math.abs(uncovered.ratio - 0.671428) < 1e-5);

  const covered = hurdleCoverage(3.0, 0.7);
  assert.equal(covered.covered, true);
});

test("hurdleCoverage refuses to divide by a zero hurdle", () => {
  const result = hurdleCoverage(0.47, 0);
  assert.equal(result.ratio, null);
  assert.equal(result.covered, null);
});

test("summarizeDecisions separates approved, blocked and honest HOLD", () => {
  const summary = summarizeDecisions([
    { action: "BUY", llm_action: "BUY" },
    { action: "HOLD", llm_action: "SELL", reasoning: "Conviccao bruta da IA insuficiente (60%)." },
    { action: "HOLD", llm_action: "BUY", reasoning: "Conviccao bruta da IA insuficiente (60%)." },
    { action: "HOLD", llm_action: "HOLD", reasoning: "LLM sugeriu HOLD." }
  ]);

  assert.equal(summary.total, 4);
  assert.equal(summary.approved, 1);
  assert.equal(summary.blocked, 2);
  assert.equal(summary.hold, 1);
  assert.deepEqual(summary.reasons, [
    { reason: "Conviccao bruta da IA insuficiente (60%).", count: 2 }
  ]);
});

test("summarizeDecisions handles empty input and missing reasons", () => {
  assert.deepEqual(summarizeDecisions([]), {
    total: 0, approved: 0, blocked: 0, hold: 0, reasons: []
  });

  const summary = summarizeDecisions([{ action: "HOLD", llm_action: "BUY" }]);
  assert.equal(summary.blocked, 1);
  assert.equal(summary.reasons[0].reason, "motivo nao registrado");
});

test("summarizeDecisions tolerates a null log list instead of throwing", () => {
  assert.deepEqual(summarizeDecisions(null), {
    total: 0, approved: 0, blocked: 0, hold: 0, reasons: []
  });
});

test("hurdleCoverage reports unknown when the move is absent", () => {
  const result = hurdleCoverage(null, 0.7);
  assert.equal(result.ratio, null);
  assert.equal(result.covered, null);
});

test("convictionOutlook raises the bar when news is unavailable", () => {
  const gates = { minimum_conviction_pct: 70, no_news_minimum_conviction_pct: 80 };

  assert.deepEqual(convictionOutlook(gates, true), { required: 70, source: "standard" });
  assert.deepEqual(convictionOutlook(gates, false), { required: 80, source: "no_news" });
});

test("convictionOutlook reports unknown rather than inventing a threshold", () => {
  assert.deepEqual(convictionOutlook({}, true), { required: null, source: "unknown" });
  // An unknown news state must not fall back to the lower bar.
  const gates = { minimum_conviction_pct: 70, no_news_minimum_conviction_pct: 80 };
  assert.deepEqual(convictionOutlook(gates, null), { required: null, source: "unknown" });
});

test("describeModelRoles lists the three roles in order without credentials", () => {
  const described = describeModelRoles({
    roles: {
      decision: { model: "kimi-k2.7-code:cloud", provider: "ollama", max_tokens: 8000, reasoning_effort: null },
      news: { model: "glm-5.3:cloud", provider: "ollama", max_tokens: 5000, reasoning_effort: "low" },
      technical: { model: "glm-5.3:cloud", provider: "ollama", max_tokens: 5000, reasoning_effort: "low" }
    }
  });

  assert.deepEqual(described.map(item => item.role), ["news", "technical", "decision"]);
  assert.equal(described[0].reasoning_effort, "low");
  assert.equal(described[2].reasoning_effort, "default");
  assert.equal(JSON.stringify(described).includes("api_key"), false);
});

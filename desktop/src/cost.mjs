/**
 * Pure helpers for the cost-reality panel.
 *
 * Kept out of main.jsx so they can be unit-tested with `node --test`, the same
 * pattern the repository already uses for csv.mjs. Nothing here calls the API or
 * reaches into the DOM.
 */

/**
 * Coerce to a finite number, treating null, undefined, blank strings, booleans
 * and symbols as absent rather than as zero. `Number(null)` is 0 and
 * `Number(" ")` is 0, which would silently turn a missing value into a zero-cost
 * hurdle; `Number(Symbol())` throws.
 */
export function toFiniteNumber(value) {
  if (value == null) return null;
  const kind = typeof value;
  if (kind === "boolean" || kind === "symbol") return null;
  if (kind === "string" && value.trim() === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

/**
 * Format a percentage that may be null/undefined without printing "NaN".
 * Returns "--" when there is no number to show.
 */
export function formatPercent(value, digits = 2) {
  const numeric = toFiniteNumber(value);
  if (numeric == null) return "--";
  return `${numeric.toFixed(digits)}%`;
}

/**
 * Turn the configured per-side cost into the two hurdles an action must clear.
 * BUY pays both sides of the round trip, SELL only the exit, because a SELL in
 * this system reduces an existing long rather than opening a short.
 */
export function costHurdles(executionConfig = {}) {
  const source = executionConfig && typeof executionConfig === "object" ? executionConfig : {};
  const oneSide = toFiniteNumber(source.one_side_cost_pct);
  if (oneSide == null) {
    return { oneSidePct: null, buyPct: null, sellPct: null };
  }
  const buy = toFiniteNumber(source.buy_round_trip_cost_pct);
  const sell = toFiniteNumber(source.sell_exit_cost_pct);
  return {
    oneSidePct: oneSide,
    buyPct: buy == null ? oneSide * 2 : buy,
    sellPct: sell == null ? oneSide : sell
  };
}

/**
 * Compare an observed typical move against the hurdle, and report the ratio.
 * A ratio below 1 means the typical move does not even cover the cost, which is
 * the condition the diagnostic measured on the development partition.
 */
export function hurdleCoverage(medianMovePct, hurdlePct) {
  const move = toFiniteNumber(medianMovePct);
  const hurdle = toFiniteNumber(hurdlePct);
  if (move == null || hurdle == null || hurdle <= 0) {
    return { ratio: null, covered: null };
  }
  const ratio = move / hurdle;
  // A non-finite ratio carries no information about coverage; report unknown.
  if (!Number.isFinite(ratio)) {
    return { ratio: null, covered: null };
  }
  return { ratio, covered: ratio >= 1 };
}

/**
 * Summarize the decision log into approved/blocked counts and the block reasons.
 * Reasons come from the audit rows, so the panel explains why nothing executed
 * instead of only showing that nothing executed.
 */
export function summarizeDecisions(logs = []) {
  const summary = { total: 0, approved: 0, blocked: 0, hold: 0, reasons: [] };
  const counts = new Map();
  const rows = Array.isArray(logs) ? logs : [];
  for (const log of rows) {
    summary.total += 1;
    const finalAction = String(log.action || "").toUpperCase();
    const llmAction = String(log.llm_action || "").toUpperCase();
    if (finalAction === "BUY" || finalAction === "SELL") {
      summary.approved += 1;
    } else if (llmAction === "BUY" || llmAction === "SELL") {
      // The model wanted direction but the Risk Manager did not approve it.
      summary.blocked += 1;
      const reason = String(log.reasoning || "motivo nao registrado").trim();
      counts.set(reason, (counts.get(reason) || 0) + 1);
    } else {
      summary.hold += 1;
    }
  }
  summary.reasons = [...counts.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((left, right) => right.count - left.count);
  return summary;
}

/**
 * Describe the conviction the model must reach for its proposal to be
 * executable, so the operator can see the gate the prompt is calibrated against.
 */
/**
 * Describe the conviction the model must reach for its proposal to be
 * executable. `newsAvailable` is true/false when the snapshot shows the news
 * rows the gate consumed, and null when that is unknown; null must report
 * "unknown" rather than silently assuming the lower bar.
 */
export function convictionOutlook(riskGates = {}, newsAvailable = true) {
  const source = riskGates && typeof riskGates === "object" ? riskGates : {};
  const base = toFiniteNumber(source.minimum_conviction_pct);
  const noNews = toFiniteNumber(source.no_news_minimum_conviction_pct);
  if (base == null) return { required: null, source: "unknown" };
  if (newsAvailable == null) return { required: null, source: "unknown" };
  if (!newsAvailable && noNews != null && noNews > base) {
    return { required: noNews, source: "no_news" };
  }
  return { required: base, source: "standard" };
}

/**
 * Build a compact, non-secret description of the resolved role models.
 */
export function describeModelRoles(modelRoles = {}) {
  const source = modelRoles && typeof modelRoles === "object" ? modelRoles : {};
  const roles = source.roles && typeof source.roles === "object" ? source.roles : {};
  const order = ["news", "technical", "decision"];
  return order
    .filter(role => roles[role])
    .map(role => ({
      role,
      model: roles[role].model,
      provider: roles[role].provider,
      max_tokens: roles[role].max_tokens,
      reasoning_effort: roles[role].reasoning_effort || "default"
    }));
}

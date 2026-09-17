// The `volatility_atr` indicator changed shape over time. Older fixtures and the
// in-browser preview use a bare number, while the live payload from
// `backend/features/indicators.py` returns an object:
//
//   "volatility_atr": { "value": 1043.93, "status": "NORMAL" }
//
// Rendering that object directly as a React child throws (React error #31) and
// blanks the whole console, and CSV export would write "[object Object]". These
// normalisers accept both shapes so the UI and the export stay correct.
//
// They are intentionally strict: a malformed value must read as unknown ("--"),
// never as a fabricated 0 or a benign status. `Number(false)` is 0 and
// `Number([])` is 0, so a plain Number() coercion would turn bad data into a
// real-looking reading.

// Accepts a finite number, or a non-blank numeric string. Rejects booleans,
// arrays, objects and blank strings, all of which coerce to a misleading number.
function finiteOrNull(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    if (value.trim() === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }
  return null;
}

function statusOrNull(value) {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

export function atrValue(technical) {
  const atr = technical?.volatility_atr;
  if (atr != null && typeof atr === "object") return finiteOrNull(atr.value);
  return finiteOrNull(atr);
}

export function atrStatus(technical) {
  const atr = technical?.volatility_atr;
  if (atr == null || typeof atr !== "object") return null;
  // A status is only meaningful alongside a usable value. Reporting a healthy
  // status for an unknown ATR would paint absent safety data as benign.
  if (finiteOrNull(atr.value) == null) return null;
  return statusOrNull(atr.status);
}

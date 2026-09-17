import assert from "node:assert/strict";
import test from "node:test";

import { atrStatus, atrValue } from "./indicators.mjs";

// The live payload returns an object; older fixtures and the in-browser preview
// use a bare number. Rendering the object as a React child throws and blanks the
// console, so both shapes must normalise.
test("ATR value reads the object shape from the live payload", () => {
  assert.equal(atrValue({ volatility_atr: { value: 1043.93, status: "NORMAL" } }), 1043.93);
  assert.equal(atrStatus({ volatility_atr: { value: 1043.93, status: "NORMAL" } }), "NORMAL");
});

test("ATR value still reads the legacy scalar shape", () => {
  assert.equal(atrValue({ volatility_atr: 364.64 }), 364.64);
  assert.equal(atrStatus({ volatility_atr: 364.64 }), null);
});

test("ATR value is null when absent or unusable instead of zero", () => {
  assert.equal(atrValue({}), null);
  assert.equal(atrValue({ volatility_atr: null }), null);
  assert.equal(atrValue({ volatility_atr: { status: "EXTREME" } }), null);
  assert.equal(atrValue({ volatility_atr: { value: null } }), null);
  assert.equal(atrValue({ volatility_atr: { value: "NaN" } }), null);
  assert.equal(atrValue({ volatility_atr: "not-a-number" }), null);
  // A blank string coerces to 0 under Number(); it must stay unknown.
  assert.equal(atrValue({ volatility_atr: { value: "" } }), null);
  assert.equal(atrValue({ volatility_atr: "   " }), null);
});

// Number(false) is 0 and Number([]) is 0, so a loose coercion would render bad
// data as a real ATR reading. Only numbers and numeric strings are accepted.
test("ATR value rejects booleans, arrays and objects instead of coercing them", () => {
  assert.equal(atrValue({ volatility_atr: false }), null);
  assert.equal(atrValue({ volatility_atr: true }), null);
  assert.equal(atrValue({ volatility_atr: { value: false } }), null);
  assert.equal(atrValue({ volatility_atr: { value: true } }), null);
  assert.equal(atrValue({ volatility_atr: { value: [] } }), null);
  assert.equal(atrValue({ volatility_atr: { value: {} } }), null);
  assert.equal(atrValue({ volatility_atr: [] }), null);
});

test("ATR value keeps a real zero and a numeric string", () => {
  assert.equal(atrValue({ volatility_atr: 0 }), 0);
  assert.equal(atrValue({ volatility_atr: { value: 0, status: "NORMAL" } }), 0);
  assert.equal(atrValue({ volatility_atr: "364.64" }), 364.64);
});

test("ATR status is null for the scalar shape rather than invented", () => {
  assert.equal(atrStatus({ volatility_atr: 12 }), null);
  assert.equal(atrStatus({}), null);
});

// An unknown ATR must never display a healthy-looking status. Absent safety data
// is rendered as "--", never "NORMAL".
test("ATR status is suppressed when the value is unusable", () => {
  assert.equal(atrStatus({ volatility_atr: { value: null, status: "NORMAL" } }), null);
  assert.equal(atrStatus({ volatility_atr: { value: "NaN", status: "NORMAL" } }), null);
  assert.equal(atrStatus({ volatility_atr: { status: "NORMAL" } }), null);
});

test("ATR status rejects a non-string status instead of rendering an object", () => {
  assert.equal(atrStatus({ volatility_atr: { value: 12, status: { state: "NORMAL" } } }), null);
  assert.equal(atrStatus({ volatility_atr: { value: 12, status: [] } }), null);
  assert.equal(atrStatus({ volatility_atr: { value: 12, status: "  " } }), null);
});

test("ATR status is kept when the value is real", () => {
  assert.equal(atrStatus({ volatility_atr: { value: 1043.93, status: "NORMAL" } }), "NORMAL");
  assert.equal(atrStatus({ volatility_atr: { value: 26000, status: "EXTREME" } }), "EXTREME");
});

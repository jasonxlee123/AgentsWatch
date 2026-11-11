import assert from "node:assert/strict";
import test from "node:test";
import { authFailureState, demoDataAllowed, formatFreshness } from "../src/runtimeMode.js";

test("demo data is available in development builds", () => {
  assert.equal(demoDataAllowed({ DEV: true }), true);
  assert.equal(demoDataAllowed({ DEV: false }), false);
  assert.equal(demoDataAllowed(undefined), false);
});

test("demo data requires an explicit opt-in for production builds", () => {
  assert.equal(demoDataAllowed({ DEV: false, VITE_ALLOW_DEMO: "true" }), true);
  assert.equal(demoDataAllowed({ DEV: false, VITE_ALLOW_DEMO: " TRUE " }), true);
  assert.equal(demoDataAllowed({ DEV: false, VITE_ALLOW_DEMO: "false" }), false);
  assert.equal(demoDataAllowed({ DEV: false, VITE_ALLOW_DEMO: "" }), false);
});

test("only a 401 means unauthenticated; other failures fail closed", () => {
  assert.equal(authFailureState({ status: 401 }, { DEV: false }), "unauthenticated");
  assert.equal(authFailureState({ status: 401 }, { DEV: true }), "unauthenticated");
  assert.equal(authFailureState({ status: 500 }, { DEV: true }), "offline");
  assert.equal(authFailureState({ status: 500 }, { DEV: false }), "unavailable");
  assert.equal(authFailureState(new Error("network"), { DEV: false }), "unavailable");
  assert.equal(authFailureState(new Error("network"), { DEV: false, VITE_ALLOW_DEMO: "true" }), "offline");
});

test("freshness formatting reports the last successful sync instead of a fixed clock", () => {
  const value = new Date(2026, 10, 11, 9, 5, 0);
  assert.deepEqual(formatFreshness(value), { label: "更新于 09:05", date: "2026-11-11" });
  assert.deepEqual(formatFreshness(null), { label: "正在同步", date: "" });
  assert.deepEqual(formatFreshness(new Date("invalid")), { label: "正在同步", date: "" });
});

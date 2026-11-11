import assert from "node:assert/strict";
import test from "node:test";

import { normalizeBasePath, resolveAppRequestPath } from "../src/basePathUtils.js";

test("normalizes a configurable frontend base path", () => {
  assert.equal(normalizeBasePath(), "/");
  assert.equal(normalizeBasePath("agentswatch"), "/agentswatch/");
  assert.equal(normalizeBasePath("/agentswatch"), "/agentswatch/");
  assert.equal(normalizeBasePath("//platform//agentswatch//"), "/platform/agentswatch/");
  assert.throws(() => normalizeBasePath("/agentswatch/../grafana"), /relative path segments/);
});

test("keeps managed requests unchanged for root deployments", () => {
  assert.equal(resolveAppRequestPath("/api/v1/overview", "/"), "/api/v1/overview");
  assert.equal(resolveAppRequestPath("/healthz", "/"), "/healthz");
});

test("prefixes API and health requests for a subpath deployment", () => {
  assert.equal(
    resolveAppRequestPath("/api/v1/runtimes?limit=100", "/agentswatch/"),
    "/agentswatch/api/v1/runtimes?limit=100",
  );
  assert.equal(resolveAppRequestPath("/healthz", "/agentswatch"), "/agentswatch/healthz");
});

test("does not rewrite unrelated, external, or already-prefixed paths", () => {
  assert.equal(resolveAppRequestPath("/assets/logo.svg", "/agentswatch/"), "/assets/logo.svg");
  assert.equal(resolveAppRequestPath("https://example.com/api", "/agentswatch/"), "https://example.com/api");
  assert.equal(resolveAppRequestPath("/agentswatch/api/v1/overview", "/agentswatch/"), "/agentswatch/api/v1/overview");
});

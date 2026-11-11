import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoServerDetail, displayRuntimeName, filterServers, formatServerConfig, formatServerMetric, serverConnectionDefaults } from "../src/serverUtils.js";

const servers = [
  { server_id: "demo-server-001", name: "示例服务器 001", internal_ip: "192.0.2.1", lifecycle_status: "active", provider_code: "demo-cloud" },
  { server_id: "demo-server-002", name: "示例服务器 002", internal_ip: "192.0.2.2", lifecycle_status: "maintenance", provider_code: "demo-private-cloud" },
];

test("filters server assets by keyword and lifecycle independently", () => {
  assert.deepEqual(filterServers(servers, "示例服务器 001", "all").map((item) => item.server_id), ["demo-server-001"]);
  assert.deepEqual(filterServers(servers, "192.0.2", "maintenance").map((item) => item.server_id), ["demo-server-002"]);
  assert.deepEqual(filterServers(servers, "demo-private-cloud", "all").map((item) => item.server_id), ["demo-server-002"]);
});

test("localizes runtime names without modifying stored identifiers", () => {
  assert.equal(displayRuntimeName("OpenClaw Runtime"), "OpenClaw 运行时");
  assert.equal(displayRuntimeName("Hermes Runtimes"), "Hermes 运行时");
});

test("builds stable standalone detail data for local demo review", () => {
  const detail = buildDemoServerDetail(servers[0], [{ runtime_id: "runtime-1", name: "OpenClaw Runtime" }]);
  assert.equal(detail.cpu_cores, 16);
  assert.equal(detail.runtimes.length, 1);
  assert.equal(detail.groups.length, 2);
});

test("formats database numeric values without leaking trailing zeros into table columns", () => {
  assert.equal(formatServerMetric("32.00"), "32");
  assert.equal(formatServerMetric("12.50"), "12.5");
  assert.equal(
    formatServerConfig({ cpu_cores: "32.00", memory_gib: "128.00", disk_gib: "1000.00" }),
    "32C / 128G / 1000G",
  );
});

test("defaults a new server connection to the asset management address and account", () => {
  assert.deepEqual(
    serverConnectionDefaults({ internal_ip: "192.0.2.15", work_account: "demo-operator" }),
    { host: "192.0.2.15", username: "demo-operator" },
  );
});

test("preserves explicit connection values while editing", () => {
  const server = { internal_ip: "192.0.2.15", work_account: "demo-operator" };
  assert.deepEqual(
    serverConnectionDefaults(server, { host: "bastion.internal", username: "ops-user" }),
    { host: "bastion.internal", username: "ops-user" },
  );
  assert.deepEqual(
    serverConnectionDefaults(server, { host: "", username: "" }),
    { host: "", username: "" },
  );
});

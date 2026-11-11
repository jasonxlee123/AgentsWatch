import test from "node:test";
import assert from "node:assert/strict";
import { groupOperationsByCategory, operationsForScope, prepareOperationParameters } from "../src/serverOperationUtils.js";

test("converts catalog-driven operation parameters and omits empty optional values", () => {
  const operation = {
    parameters: {
      paths: { type: "array", items: "string", required: false, label: "搜索路径" },
      retries: { type: "integer", required: false },
      dry_run: { type: "boolean", required: false },
    },
  };
  assert.deepEqual(
    prepareOperationParameters(operation, { paths: "/opt/a\n\n/opt/b ", retries: "2", dry_run: true }),
    { paths: ["/opt/a", "/opt/b"], retries: 2, dry_run: true },
  );
  assert.deepEqual(prepareOperationParameters(operation, { paths: "" }), {});
});

test("rejects a missing required catalog parameter", () => {
  assert.throws(
    () => prepareOperationParameters({ parameters: { value: { type: "string", required: true, label: "参数" } } }, {}),
    /参数为必填项/,
  );
});

test("rejects invalid numeric task parameters before submission", () => {
  assert.throws(
    () => prepareOperationParameters({ parameters: { retries: { type: "integer", label: "重试次数" } } }, { retries: "1.5" }),
    /重试次数必须是整数/,
  );
});

test("groups task choices using catalog categories", () => {
  assert.deepEqual(
    Object.keys(groupOperationsByCategory([{ category: "诊断" }, { category: "采集" }, { category: "诊断" }])),
    ["诊断", "采集"],
  );
});

test("keeps every operation in its matching asset scope without fixed exclusions", () => {
  const operations = [
    { code: "runtime.collect_info", scope: "runtime" },
    { code: "server.bootstrap_ssh", scope: "server" },
    { code: "server.check_connection", scope: "server" },
    { code: "server.collect_facts", scope: "server" },
    { code: "server.discover_runtimes", scope: "server" },
    { code: "server.custom_future", scope: "server" },
    { code: "runtime.refresh_status", scope: "runtime" },
    { code: "runtime.restart", scope: "runtime" },
    { code: "runtime.start", scope: "runtime" },
    { code: "runtime.stop", scope: "runtime" },
    { code: "runtime.custom_future", scope: "runtime" },
    { code: "agent.apply_deployment", scope: "agent" },
  ];

  assert.equal(operations.length, 12);
  assert.deepEqual(
    operationsForScope(operations, "server").map((operation) => operation.code),
    ["server.bootstrap_ssh", "server.check_connection", "server.collect_facts", "server.discover_runtimes", "server.custom_future"],
  );
  assert.deepEqual(
    operationsForScope(operations, "runtime").map((operation) => operation.code),
    ["runtime.collect_info", "runtime.refresh_status", "runtime.restart", "runtime.start", "runtime.stop", "runtime.custom_future"],
  );
  assert.deepEqual(
    operationsForScope(operations, "agent").map((operation) => operation.code),
    ["agent.apply_deployment"],
  );
});

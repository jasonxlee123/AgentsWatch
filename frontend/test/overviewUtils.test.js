import assert from "node:assert/strict";
import test from "node:test";
import { buildAgentMapFromCatalog, buildAgentOptions, buildRelationshipWire, completedTaskCount, filterOverviewItems, taskProgress, taskSummaryTotal } from "../src/overviewUtils.js";

test("filterOverviewItems searches all supplied fields without truncating the source", () => {
  const servers = [
    { name: "示例服务器 001", internal_ip: "192.0.2.1" },
    { name: "示例服务器 002", internal_ip: "192.0.2.2" },
    { name: "示例服务器 003", internal_ip: "192.0.2.3" },
  ];
  const result = filterOverviewItems(servers, "192.0.2.2", (item) => `${item.name} ${item.internal_ip}`);
  assert.deepEqual(result, [servers[1]]);
  assert.equal(filterOverviewItems(servers, "", (item) => item.name).length, 3);
});

test("buildAgentOptions exposes both the primary agent and every subagent", () => {
  const options = buildAgentOptions({
    primary: { agent_id: "primary", name: "主智能体" },
    subagents: [{ agent_id: "sub-1", name: "子智能体" }],
  });
  assert.deepEqual(options.map((item) => [item.agent_id, item.agent_kind]), [["primary", "primary"], ["sub-1", "subagent"]]);
});

test("taskProgress reports active progress and clamps terminal tasks to complete", () => {
  assert.deepEqual(taskProgress({ status: "running", target_count: 10, succeeded_count: 3, failed_count: 1 }), { completed: 4, percent: 40 });
  assert.deepEqual(taskProgress({ status: "succeeded", target_count: 2, succeeded_count: 1, failed_count: 0 }), { completed: 2, percent: 100 });
  assert.deepEqual(taskProgress({ status: "queued", target_count: 0 }), { completed: 0, percent: 0 });
});

test("taskSummaryTotal adds known and future status buckets", () => {
  assert.equal(taskSummaryTotal({ queued: 2, running: 1, succeeded: 4, skipped: 3 }), 10);
});

test("completedTaskCount includes every terminal task outcome", () => {
  assert.equal(completedTaskCount({ queued: 2, running: 1, succeeded: 4, partial: 1, failed: 2, cancelled: 1 }), 8);
});

test("buildAgentMapFromCatalog groups every stable subagent under its deployed primary runtime", () => {
  const map = buildAgentMapFromCatalog([
    { agent_id: "primary-1", name: "主智能体", structure_role: "primary", lifecycle_status: "active", active_runtime_id: "runtime-1" },
    { agent_id: "sub-1", name: "子智能体一", structure_role: "subagent", lifecycle_status: "active", parent_agent_id: "primary-1" },
    { agent_id: "sub-retired", name: "已退役", structure_role: "subagent", lifecycle_status: "retired", parent_agent_id: "primary-1" },
  ]);
  assert.equal(map["runtime-1"].primary.agent_id, "primary-1");
  assert.deepEqual(map["runtime-1"].subagents.map((agent) => agent.agent_id), ["sub-1"]);
});

test("buildRelationshipWire returns a bounded lane only when both cards are visible", () => {
  const gridRect = { left: 100, top: 100, width: 900, height: 480 };
  const serverViewport = { left: 110, right: 380, top: 180, bottom: 500 };
  const runtimeViewport = { left: 410, right: 680, top: 180, bottom: 500 };
  const visible = buildRelationshipWire({
    gridRect,
    fromRect: { left: 120, right: 370, top: 210, bottom: 310, width: 250, height: 100 },
    toRect: { left: 420, right: 670, top: 260, bottom: 380, width: 250, height: 120 },
    fromViewportRect: serverViewport,
    toViewportRect: runtimeViewport,
    key: "server-runtime",
    startColor: "teal",
    endColor: "blue",
  });
  assert.deepEqual({ startX: visible.startX, startY: visible.startY, endX: visible.endX, endY: visible.endY, midX: visible.midX }, { startX: 270, startY: 160, endX: 320, endY: 220, midX: 295 });

  const offscreen = buildRelationshipWire({
    gridRect,
    fromRect: { left: 120, right: 370, top: 90, bottom: 170, width: 250, height: 80 },
    toRect: { left: 420, right: 670, top: 260, bottom: 380, width: 250, height: 120 },
    fromViewportRect: serverViewport,
    toViewportRect: runtimeViewport,
    key: "server-runtime",
    startColor: "teal",
    endColor: "blue",
  });
  assert.equal(offscreen, null);
});

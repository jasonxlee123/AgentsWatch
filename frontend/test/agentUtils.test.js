import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoAgentDetail, filterAgents, flattenAgentMap } from "../src/agentUtils.js";

const runtimeMap = { s1: [{ runtime_id: "r1", name: "OpenClaw Runtime" }] };
const agentMap = { r1: {
  primary: { agent_id: "a1", name: "示例合规智能体", lifecycle_status: "active", service_archetype: "department" },
  subagents: [{ agent_id: "a2", name: "示例合同审查", lifecycle_status: "active" }],
} };

test("flattens primary and subagent identities without creating child deployments", () => {
  const agents = flattenAgentMap(agentMap, runtimeMap);
  assert.equal(agents.length, 2);
  assert.equal(agents[1].parent_agent_id, "a1");
  assert.equal(agents[1].structure_role, "subagent");
});

test("filters agents by structure, service archetype and relationship text", () => {
  const agents = flattenAgentMap(agentMap, runtimeMap);
  assert.deepEqual(filterAgents(agents, "示例合规智能体", "subagent", "department").map((item) => item.agent_id), ["a2"]);
  assert.deepEqual(filterAgents(agents, "示例合规部", "primary", "all").map((item) => item.agent_id), ["a1"]);
});

test("keeps single administrators and multiple accessible people in demo detail", () => {
  const agents = flattenAgentMap(agentMap, runtimeMap);
  const detail = buildDemoAgentDetail(agents[0], agents);
  assert.equal(detail.business_admin_name, "示例业务管理员甲");
  assert.equal(detail.it_admin_name, "示例技术管理员甲");
  assert.equal(detail.accessible_people.length, 3);
  assert.equal(detail.subagents.length, 1);
});

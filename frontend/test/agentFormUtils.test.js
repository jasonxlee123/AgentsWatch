import test from "node:test";
import assert from "node:assert/strict";
import { agentFormDefaults, buildAgentPayload, feishuSecretRequired } from "../src/agentFormUtils.js";

test("Feishu application name defaults to the agent name", () => {
  const form = agentFormDefaults();
  Object.assign(form, {
    name: "法律助手",
    channel_type: "feishu",
    channel_application_name: "",
    channel_app_id: "cli_legal",
    channel_app_secret: "secret-value",
    business_domain: "法律",
    business_scenario: "合同审查",
  });

  const payload = buildAgentPayload(form);
  assert.equal(payload.channel.application_name, "法律助手");
  assert.equal(payload.channel.app_secret, "secret-value");
});

test("editing a configured channel keeps the encrypted secret when the password is blank", () => {
  const agent = {
    agent_id: "agent-1",
    name: "财务助手",
    structure_role: "primary",
    service_archetype: "business",
    lifecycle_status: "requested",
    row_version: 3,
    business_domain: "财务",
    business_scenario: "报表分析",
    channel: { channel_type: "feishu", application_name: "财务助手", app_id: "cli_finance", credential_configured: true },
  };
  const form = agentFormDefaults(agent);
  const payload = buildAgentPayload(form, agent);

  assert.equal(payload.row_version, 3);
  assert.equal(payload.channel.app_id, "cli_finance");
  assert.equal(Object.hasOwn(payload.channel, "app_secret"), false);
  assert.equal(feishuSecretRequired(form, agent), false);
  form.channel_app_id = "cli_finance_next";
  assert.equal(feishuSecretRequired(form, agent), true);
});

test("subagents keep inherited service ownership but can write an independent channel", () => {
  const form = agentFormDefaults();
  Object.assign(form, {
    name: "合同检索",
    structure_role: "subagent",
    parent_agent_id: "primary-1",
    service_archetype: "business",
    channel_type: "feishu",
    channel_application_name: "合同检索飞书应用",
    channel_app_id: "cli_contract_search",
    channel_app_secret: "child-secret",
  });
  const payload = buildAgentPayload(form);

  assert.equal(payload.parent_agent_id, "primary-1");
  assert.equal(payload.owning_department_id, null);
  assert.deepEqual(payload.channel, {
    channel_type: "feishu",
    application_name: "合同检索飞书应用",
    app_id: "cli_contract_search",
    app_secret: "child-secret",
  });
});

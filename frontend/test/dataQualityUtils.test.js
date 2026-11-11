import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoIssues, buildIssueCategories, filterIssues, valueAtField } from "../src/dataQualityUtils.js";

test("contract category totals reproduce the 169 pending-data baseline", () => {
  const issues = buildDemoIssues();
  const categories = buildIssueCategories(issues, true);
  assert.equal(categories.reduce((sum, category) => sum + category.count, 0), 169);
});

test("filters issues by severity, category, batch and relationship text", () => {
  const issues = buildDemoIssues();
  const batch = issues[0].import_batch_id;
  const result = filterIssues(issues, "示例合规智能体", "error", "BUSINESS_ADMIN_EMPLOYEE_NO_MISSING", batch);
  assert.equal(result.length, 1);
  assert.equal(result[0].field_path, "business_admin_employee_no");
});

test("reads nested raw and normalized values without mutating payloads", () => {
  const payload = { agent: { admin: { employee_no: "DEMO-EMP-001" } } };
  assert.equal(valueAtField(payload, "agent.admin.employee_no"), "DEMO-EMP-001");
  assert.equal(valueAtField(payload, "agent.missing"), undefined);
});

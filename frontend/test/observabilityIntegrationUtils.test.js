import assert from "node:assert/strict";
import test from "node:test";
import {
  buildApplyPayload,
  buildPreviewPayload,
  isTerminalJob,
  normalizeIntegrationStatus,
  normalizeJob,
  normalizePreview,
  summaryMutationCount,
} from "../src/observabilityIntegrationUtils.js";

const HASH = "a".repeat(64);

test("normalizes the flat adapter status without requiring credentials or nested fields", () => {
  const status = normalizeIntegrationStatus({
    data: {
      healthy: true,
      source: "management-db",
      active_revision_id: "rev-8",
      last_known_good_revision_id: "rev-7",
      snapshot_hash: HASH,
      last_sync_at: "2026-07-16T10:00:00Z",
      summary: { total: 34, deployable: 32, blocked: 2, orphan: 1, native_scrape_enabled: 18 },
      jobs: [{ job_id: "job-1", status: "succeeded", summary: { add: 1, unchanged: 33 } }],
    },
  });

  assert.equal(status.adapter_status, "ready");
  assert.equal(status.source_kind, "management-db");
  assert.equal(status.contract, "integration.observability_gateway_v2");
  assert.equal(status.active_revision.revision_id, "rev-8");
  assert.equal(status.active_revision.snapshot_hash, HASH);
  assert.equal(status.gateway_count, 34);
  assert.equal(status.deployable_count, 32);
  assert.equal(status.blocked_count, 2);
  assert.equal(status.orphan_count, 1);
  assert.equal(status.native_scrape_enabled_count, 18);
  assert.equal(status.last_known_good_revision_id, "rev-7");
  assert.deepEqual(status.jobs[0].summary, {
    add: 1, update: 0, disable: 0, retain_blocked: 0, orphan: 0, noop: 33,
  });
  assert.equal("database_url" in status, false);
});

test("normalizes per-runtime preview rows and preserves 龙虾名-服务器ID display", () => {
  const preview = normalizePreview({
    data: {
      snapshot_hash: HASH,
      source: "management-db",
      summary: { added: 1, changed: 1, blocked: 1, no_change: 4 },
      changes: [
        {
          runtime_id: "runtime-34",
          gateway_id: "lobster-34",
          action: "add",
          after: {
            primary_agent_id: "agent-34",
            lobster_name: "示例采购智能体",
            server_display_id: "demo-server-034",
          },
          changed_fields: ["gateway_id", "primary_agent_id"],
        },
      ],
    },
  });

  assert.equal(preview.apply_allowed, true);
  assert.deepEqual(preview.summary, {
    add: 1, update: 1, disable: 0, retain_blocked: 1, orphan: 0, noop: 4,
  });
  assert.equal(preview.changes[0].runtime_id, "runtime-34");
  assert.equal(preview.changes[0].primary_agent_id, "agent-34");
  assert.equal(preview.changes[0].display_name, "示例采购智能体-demo-server-034");
});

test("accepts nested action counts and action-grouped change arrays", () => {
  const preview = normalizePreview({
    data: {
      snapshot_hash: HASH,
      summary: { actions: { add: 1, update: 2, noop: 6 } },
      actions: {
        add: [{ runtime_id: "demo-runtime-add", lobster_name: "示例新增智能体", server_display_id: "demo-server-099" }],
      },
    },
  });

  assert.equal(preview.summary.add, 1);
  assert.equal(preview.summary.update, 2);
  assert.equal(preview.summary.noop, 6);
  assert.equal(preview.changes[0].action, "add");
  assert.equal(preview.changes[0].display_name, "示例新增智能体-demo-server-099");
});

test("builds exact preview and confirmed apply proxy payloads", () => {
  assert.deepEqual(buildPreviewPayload(), { source: "management-db" });
  assert.deepEqual(buildApplyPayload({ snapshot_hash: HASH }), {
    source: "management-db",
    expected_snapshot_hash: HASH,
    confirmed: true,
  });
  assert.throws(
    () => buildApplyPayload({ snapshot_hash: "A".repeat(64) }),
    /有效的快照校验值/,
  );
});

test("counts only mutating actions and identifies terminal adapter jobs", () => {
  assert.equal(summaryMutationCount({ add: 2, update: 3, disable: 1, noop: 9, orphan: 4 }), 6);
  assert.equal(isTerminalJob("succeeded"), true);
  assert.equal(isTerminalJob("rolled_back"), true);
  assert.equal(isTerminalJob("rollback_failed"), true);
  assert.equal(isTerminalJob("validating"), false);
});

test("reads revision and counts from the adapter job result", () => {
  const job = normalizeJob({
    job_id: "job-9",
    status: "succeeded",
    request: { source: "management-db" },
    result: { revision_id: "rev-9", snapshot_hash: HASH, summary: { add: 2, noop: 8 } },
  });

  assert.equal(job.source, "management-db");
  assert.equal(job.revision_id, "rev-9");
  assert.equal(job.snapshot_hash, HASH);
  assert.equal(job.summary.add, 2);
  assert.equal(job.summary.noop, 8);
});

const SUMMARY_KEYS = ["add", "update", "disable", "retain_blocked", "orphan", "noop"];

export const OBSERVABILITY_SOURCE = "management-db";
export const SNAPSHOT_HASH_PATTERN = /^[0-9a-f]{64}$/;

export const SYNC_ACTION_LABELS = {
  add: "新增接入",
  update: "更新配置",
  disable: "停止采集",
  retain_blocked: "保留并阻断",
  orphan: "来源缺失",
  noop: "无需变更",
};

export const SYNC_JOB_STATUS_LABELS = {
  queued: "排队中",
  running: "执行中",
  reading: "读取数据",
  planned: "待应用",
  applying: "生成配置",
  validating: "校验配置",
  activating: "激活版本",
  verifying: "验证生效",
  rolling_back: "正在回滚",
  succeeded: "已完成",
  failed: "失败",
  blocked: "已阻断",
  rolled_back: "已回滚",
  rollback_failed: "回滚失败",
  cancelled: "已取消",
  superseded: "已失效",
};

const TERMINAL_JOB_STATUSES = new Set([
  "succeeded", "failed", "blocked", "rolled_back", "rollback_failed", "cancelled", "superseded",
]);

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function count(value) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) && number > 0 ? Math.trunc(number) : 0;
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

/**
 * @typedef {Object} ObservabilitySyncSummary
 * @property {number} add
 * @property {number} update
 * @property {number} disable
 * @property {number} retain_blocked
 * @property {number} orphan
 * @property {number} noop
 */

/**
 * @typedef {Object} ObservabilitySyncChange
 * @property {string} runtime_id
 * @property {string} gateway_id
 * @property {string} primary_agent_id
 * @property {string} display_name
 * @property {string} action
 * @property {string[]} changed_fields
 * @property {string} reason
 */

/**
 * @typedef {Object} ObservabilitySyncPreview
 * @property {string} snapshot_hash
 * @property {string} source
 * @property {ObservabilitySyncSummary} summary
 * @property {ObservabilitySyncChange[]} changes
 * @property {boolean} apply_allowed
 */

export function responseData(response) {
  return object(response).data ?? response ?? {};
}

export function normalizeSummary(value) {
  const raw = object(value);
  const values = { ...raw, ...object(raw.actions) };
  const aliases = {
    add: ["add", "added", "create", "created"],
    update: ["update", "updated", "change", "changed"],
    disable: ["disable", "disabled", "remove", "removed"],
    retain_blocked: ["retain_blocked", "retained_blocked", "blocked", "retain"],
    orphan: ["orphan", "orphaned"],
    noop: ["noop", "unchanged", "no_change"],
  };
  return Object.fromEntries(SUMMARY_KEYS.map((key) => [
    key,
    count(first(...aliases[key].map((alias) => values[alias]))),
  ]));
}

function normalizeRevision(value) {
  if (!value) return null;
  if (typeof value === "string") return { revision_id: value, snapshot_hash: "", activated_at: null };
  const raw = object(value);
  return {
    revision_id: String(first(raw.revision_id, raw.id, raw.version, "")),
    snapshot_hash: String(first(raw.snapshot_hash, raw.source_hash, raw.artifact_hash, "")),
    activated_at: first(raw.activated_at, raw.created_at, raw.updated_at, null),
    gateway_count: count(first(raw.gateway_count, raw.runtime_count)),
  };
}

export function normalizeJob(value) {
  const raw = object(value);
  const result = object(raw.result);
  const request = object(raw.request);
  return {
    ...raw,
    job_id: String(first(raw.job_id, raw.run_id, raw.id, "")),
    status: String(first(raw.status, raw.state, "unknown")),
    source: String(first(raw.source, raw.source_kind, request.source, result.source, OBSERVABILITY_SOURCE)),
    requested_by: String(first(raw.requested_by, raw.actor, raw.created_by, "系统")),
    created_at: first(raw.created_at, raw.requested_at, raw.started_at, null),
    finished_at: first(raw.finished_at, raw.completed_at, null),
    revision_id: String(first(raw.revision_id, raw.candidate_revision_id, raw.active_revision_id, result.revision_id, result.active_revision_id, "")),
    snapshot_hash: String(first(raw.snapshot_hash, raw.source_hash, result.snapshot_hash, result.source_hash, "")),
    error_message: String(first(raw.error_message, raw.last_error, raw.error, "")),
    summary: normalizeSummary(first(raw.summary, raw.counts, result.summary, result.counts, {})),
  };
}

export function normalizeIntegrationStatus(response) {
  const raw = object(responseData(response));
  const adapter = object(raw.adapter);
  const database = object(raw.database);
  const source = typeof raw.source === "string" ? { kind: raw.source } : object(raw.source);
  const statusSummary = object(raw.summary);
  const inferredAdapterStatus = raw.healthy === true ? "ready" : raw.healthy === false ? "failed" : undefined;
  const inferredSourceStatus = raw.healthy === true ? "connected" : undefined;
  const flatRevision = raw.active_revision_id ? {
    revision_id: raw.active_revision_id,
    snapshot_hash: raw.snapshot_hash,
    activated_at: raw.last_sync_at,
    gateway_count: first(raw.gateway_count, statusSummary.total),
  } : null;
  const activeRevision = normalizeRevision(first(raw.active_revision, raw.revision, flatRevision));
  const jobs = (Array.isArray(raw.jobs) ? raw.jobs : Array.isArray(raw.recent_jobs) ? raw.recent_jobs : [])
    .map(normalizeJob)
    .filter((job) => job.job_id);
  return {
    adapter_status: String(first(raw.adapter_status, adapter.status, inferredAdapterStatus, raw.status, "unknown")),
    adapter_name: String(first(raw.adapter_name, adapter.name, "独立运维适配器")),
    adapter_version: String(first(raw.adapter_version, adapter.version, "")),
    source_status: String(first(raw.source_status, source.status, database.status, inferredSourceStatus, "unknown")),
    source_kind: String(first(source.kind, raw.source_kind, OBSERVABILITY_SOURCE)),
    source_label: String(first(source.label, raw.source_label, "管理模块只读视图")),
    contract: String(first(raw.contract, source.contract, database.contract, "integration.observability_gateway_v2")),
    database_label: String(first(raw.database_label, database.label, database.name, "管理模块 PostgreSQL")),
    database_mode: String(first(raw.database_mode, database.mode, database.read_only === false ? "权限异常" : "只读连接")),
    active_revision: activeRevision,
    last_success_at: first(raw.last_success_at, raw.last_sync_at, activeRevision?.activated_at, null),
    gateway_count: count(first(raw.gateway_count, statusSummary.total, activeRevision?.gateway_count)),
    deployable_count: count(first(raw.deployable_count, statusSummary.deployable)),
    blocked_count: count(first(raw.blocked_count, statusSummary.blocked)),
    orphan_count: count(first(raw.orphan_count, statusSummary.orphan)),
    native_scrape_enabled_count: count(first(raw.native_scrape_enabled_count, statusSummary.native_scrape_enabled)),
    last_known_good_revision_id: String(first(raw.last_known_good_revision_id, "")),
    jobs,
  };
}

function displayName(raw, before, after) {
  const explicit = first(raw.display_name, after.display_name, before.display_name);
  if (explicit) return String(explicit);
  const lobsterName = String(first(raw.lobster_name, after.lobster_name, before.lobster_name, "未关联主智能体"));
  const serverDisplayId = String(first(
    raw.server_display_id, after.server_display_id, before.server_display_id, "未配置服务器ID",
  ));
  return `${lobsterName}-${serverDisplayId}`;
}

export function normalizeChange(value, index = 0) {
  const raw = object(value);
  const before = object(raw.before);
  const after = object(raw.after);
  const fieldChanges = object(raw.field_changes);
  const fields = Array.isArray(raw.changed_fields)
    ? raw.changed_fields
    : Array.isArray(raw.fields)
      ? raw.fields
      : Object.keys(fieldChanges);
  return {
    ...raw,
    change_id: String(first(raw.change_id, raw.item_id, `${first(raw.runtime_id, after.runtime_id, before.runtime_id, "runtime")}-${index}`)),
    runtime_id: String(first(raw.runtime_id, after.runtime_id, before.runtime_id, "")),
    gateway_id: String(first(raw.gateway_id, after.gateway_id, before.gateway_id, "")),
    primary_agent_id: String(first(raw.primary_agent_id, after.primary_agent_id, before.primary_agent_id, "")),
    lobster_name: String(first(raw.lobster_name, after.lobster_name, before.lobster_name, "")),
    server_display_id: String(first(raw.server_display_id, after.server_display_id, before.server_display_id, "")),
    display_name: displayName(raw, before, after),
    action: String(first(raw.action, raw.operation, raw.change_type, "noop")),
    changed_fields: fields.map(String),
    reason: String(first(raw.reason, raw.reason_message, raw.message, "")),
    before,
    after,
  };
}

/** @returns {ObservabilitySyncPreview} */
export function normalizePreview(response) {
  const raw = object(responseData(response));
  const snapshotHash = String(first(raw.snapshot_hash, raw.source_hash, raw.plan_hash, ""));
  const nestedSummary = object(raw.summary);
  const summary = normalizeSummary({
    ...raw,
    ...object(raw.counts),
    ...nestedSummary,
    ...object(nestedSummary.actions),
    ...(Array.isArray(raw.actions) ? {} : object(raw.actions)),
  });
  const actionItems = Array.isArray(raw.actions)
    ? raw.actions
    : Object.entries(object(raw.actions)).flatMap(([action, items]) => (
      Array.isArray(items) ? items.map((item) => ({ action, ...object(item) })) : []
    ));
  const changes = (Array.isArray(raw.changes) ? raw.changes : Array.isArray(raw.items) ? raw.items : actionItems)
    .map(normalizeChange);
  return {
    ...raw,
    snapshot_hash: snapshotHash,
    source: String(first(raw.source, raw.source_kind, OBSERVABILITY_SOURCE)),
    summary,
    changes,
    apply_allowed: raw.apply_allowed !== false && raw.blocked !== true && SNAPSHOT_HASH_PATTERN.test(snapshotHash),
    created_at: first(raw.created_at, raw.previewed_at, null),
  };
}

export function buildPreviewPayload() {
  return { source: OBSERVABILITY_SOURCE };
}

export function buildApplyPayload(preview) {
  const snapshotHash = String(preview?.snapshot_hash || "");
  if (!SNAPSHOT_HASH_PATTERN.test(snapshotHash)) throw new Error("同步预览缺少有效的快照校验值");
  return { source: OBSERVABILITY_SOURCE, expected_snapshot_hash: snapshotHash, confirmed: true };
}

export function summaryMutationCount(summary) {
  const normalized = normalizeSummary(summary);
  return normalized.add + normalized.update + normalized.disable;
}

export function isTerminalJob(status) {
  return TERMINAL_JOB_STATUSES.has(String(status || ""));
}

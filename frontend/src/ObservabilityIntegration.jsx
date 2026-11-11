import { useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise,
  CheckCircle,
  ClockCounterClockwise,
  Cube,
  Database,
  GitDiff,
  HardDrives,
  Info,
  Play,
  PlugsConnected,
  Robot,
  ShieldCheck,
  Stack,
  TreeStructure,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import {
  SYNC_ACTION_LABELS,
  SYNC_JOB_STATUS_LABELS,
  buildApplyPayload,
  buildPreviewPayload,
  isTerminalJob,
  normalizeIntegrationStatus,
  normalizeJob,
  normalizePreview,
  summaryMutationCount,
} from "./observabilityIntegrationUtils.js";

const SUMMARY_CARDS = [
  ["add", "新增", "准备接入的新 Gateway"],
  ["update", "更新", "运行时或展示字段变化"],
  ["disable", "停用", "明确停止采集"],
  ["retain_blocked", "保留阻断", "保留上一有效配置"],
  ["orphan", "来源缺失", "需要人工核对"],
  ["noop", "不变", "无需重复生成"],
];

const DEMO_HASH = "8c2a807c244ca124e15c98db286e28f647a549fe3d9a5bb16534fe531a2f0c7e";

const DEMO_STATUS = normalizeIntegrationStatus({
  data: {
    adapter_status: "ready",
    source_status: "connected",
    adapter: { name: "AgentsWatch 运维适配器", version: "v1" },
    source: { kind: "management-db", label: "管理模块只读视图" },
    database: { name: "管理模块 PostgreSQL", mode: "只读连接", contract: "integration.observability_gateway_v2" },
    active_revision: { revision_id: "rev-20260716-008", snapshot_hash: "f".repeat(64), activated_at: "2026-07-16T10:42:00+08:00", gateway_count: 33 },
    last_success_at: "2026-07-16T10:42:00+08:00",
    gateway_count: 33,
    blocked_count: 1,
    jobs: [
      { job_id: "sync-20260716-008", status: "succeeded", requested_by: "admin", created_at: "2026-07-16T10:41:12+08:00", finished_at: "2026-07-16T10:42:00+08:00", revision_id: "rev-20260716-008", summary: { add: 1, update: 2, noop: 30 } },
      { job_id: "sync-20260715-007", status: "rolled_back", requested_by: "operator", created_at: "2026-07-15T16:18:00+08:00", finished_at: "2026-07-15T16:19:21+08:00", revision_id: "rev-20260715-007", summary: { update: 3, retain_blocked: 1 } },
    ],
  },
});

const DEMO_PREVIEW = normalizePreview({
  data: {
    snapshot_hash: DEMO_HASH,
    source: "management-db",
    summary: { add: 1, update: 2, disable: 1, retain_blocked: 1, orphan: 0, noop: 29 },
    changes: [
      { runtime_id: "demo-runtime-034", gateway_id: "demo-gateway-034", primary_agent_id: "demo-agent-034", lobster_name: "示例采购智能体", server_display_id: "demo-server-034", action: "add", changed_fields: ["gateway_id", "monitor_host", "primary_agent_id"] },
      { runtime_id: "demo-runtime-012", gateway_id: "demo-gateway-012", primary_agent_id: "demo-agent-012", display_name: "示例合规智能体-demo-server-012", action: "update", changed_fields: ["lobster_name", "display_name"] },
      { runtime_id: "demo-runtime-018", gateway_id: "demo-gateway-018", primary_agent_id: "demo-agent-018", display_name: "示例研发智能体-demo-server-018", action: "update", changed_fields: ["monitor_host", "monitor_port"] },
      { runtime_id: "demo-runtime-006", gateway_id: "demo-gateway-006", primary_agent_id: "demo-agent-006", display_name: "示例人事智能体-demo-server-006", action: "disable", changed_fields: ["monitoring_enabled"], reason: "运行时已停用" },
      { runtime_id: "demo-runtime-027", gateway_id: "demo-gateway-027", primary_agent_id: "demo-agent-027", display_name: "示例预算智能体-demo-server-027", action: "retain_blocked", changed_fields: ["monitor_host"], reason: "管理数据暂缺监控地址，继续保留上一有效配置" },
      { runtime_id: "demo-runtime-001", gateway_id: "demo-gateway-001", primary_agent_id: "demo-agent-001", display_name: "示例个人数字助手-demo-server-001", action: "noop", changed_fields: [] },
    ],
  },
});

const STATUS_LABELS = {
  ready: "就绪",
  connected: "已连接",
  healthy: "正常",
  succeeded: "正常",
  degraded: "异常",
  unreachable: "不可达",
  disconnected: "未连接",
  failed: "失败",
  not_configured: "未配置",
  unknown: "未知",
};

const FIELD_LABELS = {
  gateway_id: "Gateway ID",
  monitor_host: "监控地址",
  monitor_port: "监控端口",
  monitoring_enabled: "采集状态",
  primary_agent_id: "主智能体",
  lobster_name: "龙虾名",
  display_name: "显示名称",
  server_display_id: "服务器 ID",
};

const REASON_LABELS = {
  missing_from_source: "管理数据来源中已不存在，继续保留上一有效配置",
  missing_gateway_id: "缺少 Gateway ID",
  missing_primary_agent: "缺少主智能体",
  missing_monitor_host: "缺少监控地址",
  missing_monitor_port: "缺少监控端口",
};

function formatTime(value) {
  if (!value) return "尚无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function shortValue(value, length = 13) {
  const text = String(value || "");
  if (!text) return "-";
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function statusTone(status) {
  if (["ready", "connected", "healthy", "succeeded"].includes(status)) return "healthy";
  if (["failed", "unreachable", "disconnected"].includes(status)) return "failed";
  if (["degraded", "blocked", "rolling_back", "rollback_failed"].includes(status)) return "warning";
  return "neutral";
}

function ConnectionCard({ icon: Icon, label, value, detail, status }) {
  const tone = statusTone(status);
  return <article className={`observability-status-card observability-status-card--${tone}`}>
    <span className="observability-status-icon"><Icon size={20} /></span>
    <div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div>
    <em><i />{STATUS_LABELS[status] || status || "未知"}</em>
  </article>;
}

function JobStatus({ status }) {
  return <span className={`observability-job-status observability-job-status--${status}`}><i />{SYNC_JOB_STATUS_LABELS[status] || status || "未知"}</span>;
}

export function ObservabilityIntegration({ dataSource, requestJson, canManage = false }) {
  const [status, setStatus] = useState(dataSource === "api" ? null : DEMO_STATUS);
  const [preview, setPreview] = useState(null);
  const [activeJob, setActiveJob] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function loadStatus(silent = false) {
    if (dataSource !== "api") { setStatus(DEMO_STATUS); return DEMO_STATUS; }
    if (!silent) setBusy("status");
    try {
      const next = normalizeIntegrationStatus(await requestJson("/api/v1/observability-integration/status"));
      setStatus(next);
      return next;
    } catch (loadError) {
      setError(loadError.message);
      return null;
    } finally {
      if (!silent) setBusy("");
    }
  }

  useEffect(() => {
    let cancelled = false;
    if (dataSource !== "api") {
      setStatus(DEMO_STATUS);
      setError("");
      return undefined;
    }
    setBusy("status"); setError("");
    requestJson("/api/v1/observability-integration/status")
      .then((response) => { if (!cancelled) setStatus(normalizeIntegrationStatus(response)); })
      .catch((loadError) => { if (!cancelled) setError(loadError.message); })
      .finally(() => { if (!cancelled) setBusy(""); });
    return () => { cancelled = true; };
  }, [dataSource, requestJson]);

  useEffect(() => {
    if (dataSource !== "api" || !activeJob?.job_id || isTerminalJob(activeJob.status)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const job = normalizeJob((await requestJson(`/api/v1/observability-integration/jobs/${activeJob.job_id}`)).data);
        if (cancelled) return;
        setActiveJob(job);
        if (isTerminalJob(job.status)) await loadStatus(true);
      } catch (pollError) {
        if (!cancelled) setError(pollError.message);
      }
    }, 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeJob?.job_id, activeJob?.status, dataSource, requestJson]);

  async function createPreview() {
    if (!canManage) return;
    setBusy("preview"); setError(""); setActiveJob(null);
    try {
      if (dataSource !== "api") { setPreview(DEMO_PREVIEW); return; }
      const response = await requestJson("/api/v1/observability-integration/sync/preview", {
        method: "POST",
        body: JSON.stringify(buildPreviewPayload()),
      });
      setPreview(normalizePreview(response));
      await loadStatus(true);
    } catch (previewError) {
      setError(previewError.message);
    } finally {
      setBusy("");
    }
  }

  async function applyPreview() {
    if (!canManage || !preview?.apply_allowed) return;
    const mutations = summaryMutationCount(preview.summary);
    const message = `确认将本次预览应用到独立运维模块吗？\n新增 ${preview.summary.add}、更新 ${preview.summary.update}、停用 ${preview.summary.disable}，共 ${mutations} 项运行时变更。`;
    if (!window.confirm(message)) return;
    setBusy("apply"); setError("");
    try {
      if (dataSource !== "api") {
        const job = normalizeJob({ job_id: "sync-demo-current", status: "succeeded", requested_by: "当前用户", created_at: new Date().toISOString(), finished_at: new Date().toISOString(), revision_id: `rev-${preview.snapshot_hash.slice(0, 8)}`, summary: preview.summary });
        setActiveJob(job);
        setStatus((current) => ({ ...current, active_revision: { revision_id: job.revision_id, snapshot_hash: preview.snapshot_hash, activated_at: job.finished_at, gateway_count: current.gateway_count + preview.summary.add - preview.summary.disable }, last_success_at: job.finished_at, jobs: [job, ...current.jobs] }));
        return;
      }
      const response = await requestJson("/api/v1/observability-integration/sync/apply", {
        method: "POST",
        body: JSON.stringify(buildApplyPayload(preview)),
        confirmed: true,
      });
      const job = normalizeJob(response.data);
      setActiveJob(job);
      await loadStatus(true);
    } catch (applyError) {
      setError(applyError.message);
    } finally {
      setBusy("");
    }
  }

  async function openJob(job) {
    setActiveJob(job); setError("");
    if (dataSource !== "api") return;
    setBusy(`job:${job.job_id}`);
    try {
      setActiveJob(normalizeJob((await requestJson(`/api/v1/observability-integration/jobs/${job.job_id}`)).data));
    } catch (jobError) {
      setError(jobError.message);
    } finally {
      setBusy("");
    }
  }

  const jobs = useMemo(() => {
    const items = [...(status?.jobs || [])];
    if (activeJob?.job_id && !items.some((item) => item.job_id === activeJob.job_id)) items.unshift(activeJob);
    return items;
  }, [activeJob, status?.jobs]);
  const mutations = preview ? summaryMutationCount(preview.summary) : 0;
  const adapterReady = statusTone(status?.adapter_status) === "healthy";

  return <section className="asset-page observability-integration-page" aria-labelledby="observability-integration-title">
    <header className="asset-page-heading observability-integration-heading">
      <div><span className="eyebrow">独立模块 · 运行时数据契约</span><h2 id="observability-integration-title">可观测接入</h2><p>从管理数据库预览 Gateway 运行时变化，确认后由独立运维适配器生成并激活配置。</p></div>
      <div className="observability-heading-actions"><button type="button" className="secondary-action" disabled={busy === "status"} onClick={() => { setError(""); loadStatus(); }}><ArrowClockwise size={17} className={busy === "status" ? "spin" : ""} />刷新状态</button><button type="button" className="primary-action" disabled={!canManage || Boolean(busy) || !adapterReady} onClick={createPreview}><GitDiff size={17} />{busy === "preview" ? "正在检查" : "检查数据变化"}</button></div>
    </header>

    {!canManage && <div className="permission-banner"><Info size={17} />当前账号可以查看接入状态和同步记录，但不能创建预览或应用变更。</div>}
    {error && <div className="observability-error"><XCircle size={17} /><span>{error}</span><button type="button" onClick={() => setError("")} aria-label="关闭错误">×</button></div>}

    <div className="observability-status-grid">
      <ConnectionCard icon={PlugsConnected} label="运维适配器" value={status?.adapter_name || "正在读取"} detail={status?.adapter_version ? `接口版本 ${status.adapter_version}` : "独立部署的同步执行端"} status={status?.adapter_status || "unknown"} />
      <ConnectionCard icon={Database} label="管理数据库" value={status?.database_label || "正在读取"} detail={status ? `${status.database_mode} · ${status.contract}` : "检查只读数据边界"} status={status?.source_status || "unknown"} />
      <ConnectionCard icon={TreeStructure} label="数据来源" value={status?.source_label || "正在读取"} detail={status ? `${status.source_kind} · 可部署 ${status.deployable_count || Math.max(0, status.gateway_count - status.blocked_count)}/${status.gateway_count}` : "management-db"} status={status?.source_status || "unknown"} />
      <ConnectionCard icon={Stack} label="当前生效版本" value={status?.active_revision?.revision_id || "尚未同步"} detail={status?.active_revision ? `${status.gateway_count} 个 Gateway · ${status.blocked_count} 个阻断 · ${shortValue(status.active_revision.snapshot_hash)}` : "等待首次确认同步"} status={status?.active_revision ? "succeeded" : "unknown"} />
    </div>

    <div className="observability-boundary-note"><ShieldCheck size={19} /><div><strong>保持部署与凭据边界</strong><span>管理模块只读取状态、发起预览和确认同步；页面不嵌入 Grafana 或运维控制台，也不读取数据库密码、SSH 凭据和 Gateway Token。</span></div><em>最近成功：{formatTime(status?.last_success_at)}</em></div>

    <section className="observability-preview-card">
      <div className="observability-section-heading"><div><GitDiff size={18} /><span><strong>同步预览</strong><small>{preview ? `快照 ${shortValue(preview.snapshot_hash, 18)} · 来源 ${preview.source}` : "先比较管理数据库与当前运维版本，再决定是否应用"}</small></span></div>{preview && <button type="button" onClick={createPreview} disabled={Boolean(busy) || !canManage}><ArrowClockwise size={15} />重新检查</button>}</div>
      {!preview ? <div className="observability-preview-empty"><HardDrives size={32} /><strong>尚未生成同步预览</strong><span>检查操作不会改动运维配置；系统会列出每个 Runtime 的新增、更新、停用和保留项。</span><button type="button" className="secondary-action" disabled={!canManage || Boolean(busy) || !adapterReady} onClick={createPreview}><GitDiff size={16} />检查数据变化</button></div> : <>
        <div className="observability-summary-grid">{SUMMARY_CARDS.map(([key, label, description]) => <article key={key} className={`observability-summary observability-summary--${key}`}><span>{label}</span><strong>{preview.summary[key]}</strong><small>{description}</small></article>)}</div>
        {(preview.summary.retain_blocked > 0 || preview.summary.orphan > 0) && <div className="observability-plan-warning"><WarningCircle size={17} /><span>发现 {preview.summary.retain_blocked + preview.summary.orphan} 个需要核对的 Runtime。它们会保留上一有效配置，不会因基础数据暂缺而自动删除。</span></div>}
        <div className="observability-change-table">
          <div className="observability-change-head"><span>运行时 / Gateway</span><span>主智能体展示</span><span>动作</span><span>变化字段</span><span>说明</span></div>
          {preview.changes.map((change) => <article className={`observability-change-row observability-change-row--${change.action}`} key={change.change_id}>
            <span className="observability-runtime-cell"><Cube size={17} /><span><strong>{change.runtime_id || "未提供 Runtime ID"}</strong><small>{change.gateway_id || "Gateway ID 待补齐"}</small></span></span>
            <span className="observability-agent-cell"><Robot size={16} /><span><strong>{change.display_name}</strong><small>{change.primary_agent_id || "主智能体 ID 待补齐"}</small></span></span>
            <span><em className={`observability-action observability-action--${change.action}`}>{SYNC_ACTION_LABELS[change.action] || change.action}</em></span>
            <span className="observability-fields">{change.changed_fields.length ? change.changed_fields.map((field) => <code key={field}>{FIELD_LABELS[field] || field}</code>) : <small>{change.action === "noop" ? "无字段变化" : "由适配器判定"}</small>}</span>
            <span className="observability-reason">{REASON_LABELS[change.reason] || change.reason || (change.action === "noop" ? "当前配置一致" : "按预览应用")}</span>
          </article>)}
          {!preview.changes.length && <div className="observability-change-empty"><CheckCircle size={19} />当前快照没有运行时变化</div>}
        </div>
        <footer className="observability-preview-actions"><span><ShieldCheck size={16} />应用时会再次校验这份 64 位快照哈希；来源变化后必须重新预览。</span><button type="button" className="primary-action" disabled={!canManage || Boolean(busy) || !preview.apply_allowed || mutations === 0} onClick={applyPreview}><Play size={16} />{busy === "apply" ? "正在提交" : mutations ? `确认同步 ${mutations} 项` : "无需同步"}</button></footer>
      </>}
    </section>

    <section className="observability-jobs-card">
      <div className="observability-section-heading"><div><ClockCounterClockwise size={18} /><span><strong>最近同步任务</strong><small>任务由独立运维适配器执行；管理模块只展示状态与结果。</small></span></div><span>{jobs.length} 条</span></div>
      <div className="observability-job-head"><span>任务</span><span>状态</span><span>变化</span><span>生效版本</span><span>发起人与时间</span><span /></div>
      {jobs.map((job) => <button type="button" className={`observability-job-row ${activeJob?.job_id === job.job_id ? "observability-job-row--active" : ""}`} key={job.job_id} onClick={() => openJob(job)} disabled={busy === `job:${job.job_id}`}>
        <span><strong>{job.job_id}</strong><small>{job.source}</small></span><JobStatus status={activeJob?.job_id === job.job_id ? activeJob.status : job.status} /><span>新增 {job.summary.add} · 更新 {job.summary.update} · 停用 {job.summary.disable}</span><code>{job.revision_id || "未激活"}</code><span><strong>{job.requested_by}</strong><small>{formatTime(job.created_at)}</small></span><ArrowClockwise size={15} className={busy === `job:${job.job_id}` ? "spin" : ""} />
      </button>)}
      {!jobs.length && <div className="observability-jobs-empty"><ClockCounterClockwise size={21} />尚无同步任务</div>}
      {activeJob && <div className={`observability-job-detail observability-job-detail--${statusTone(activeJob.status)}`}><span><JobStatus status={activeJob.status} /><strong>{activeJob.job_id}</strong><code>{activeJob.snapshot_hash ? shortValue(activeJob.snapshot_hash, 20) : activeJob.revision_id || "等待生成版本"}</code></span><p>{activeJob.error_message || (isTerminalJob(activeJob.status) ? `任务已结束，完成时间 ${formatTime(activeJob.finished_at)}` : "任务正在运维模块执行，页面会自动刷新状态。")}</p></div>}
    </section>
  </section>;
}

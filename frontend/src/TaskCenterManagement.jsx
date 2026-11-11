import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  CaretRight,
  CheckCircle,
  Clock,
  ListChecks,
  MagnifyingGlass,
  Prohibit,
  X,
  XCircle,
} from "@phosphor-icons/react";


export const DEMO_TASKS = [
  { run_id: "task-demo-001", operation_code: "server.collect_facts", operation_name: "采集服务器信息", category: "资产采集", target_type: "server", status: "running", target_count: 12, succeeded_count: 8, failed_count: 0, requested_by: "demo-user", target_names: ["示例服务器 A", "示例宿主服务器 B"], version_label: "1.0.0", playbook_version: "1.0.0+demo001", parameters: {}, created_at: "2026-01-15T10:26:00+08:00" },
  { run_id: "task-demo-002", operation_code: "runtime.restart", operation_name: "重启运行时", category: "生命周期", target_type: "runtime", status: "succeeded", target_count: 1, succeeded_count: 1, failed_count: 0, requested_by: "demo-user", target_names: ["示例运行时 A"], version_label: "1.2.0", playbook_version: "1.2.0+demo002", parameters: {}, created_at: "2026-01-15T09:48:00+08:00", finished_at: "2026-01-15T09:48:18+08:00" },
  { run_id: "task-demo-003", operation_code: "agent.apply_deployment", operation_name: "部署或同步智能体", category: "部署管理", target_type: "agent", status: "failed", target_count: 2, succeeded_count: 1, failed_count: 1, requested_by: "demo-user", target_names: ["示例合规智能体", "示例资金洞察智能体"], version_label: "1.1.0", playbook_version: "1.1.0+demo003", parameters: {}, created_at: "2026-01-15T09:17:00+08:00", finished_at: "2026-01-15T09:18:12+08:00" },
  { run_id: "task-demo-004", operation_code: "server.check_connection", operation_name: "检查服务器连接", category: "连接与诊断", target_type: "server", status: "queued", target_count: 5, succeeded_count: 0, failed_count: 0, requested_by: "demo-user", target_names: ["示例规划服务器"], version_label: "1.0.0", playbook_version: "1.0.0+demo004", parameters: {}, created_at: "2026-01-15T08:52:00+08:00" },
];

const STATUS_LABELS = { queued: "等待执行", running: "执行中", succeeded: "成功", partial: "部分成功", failed: "失败", cancelled: "已取消", unreachable: "不可达", skipped: "已跳过" };
const TARGET_LABELS = { server: "服务器", runtime: "运行时", agent: "智能体" };
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const RETRYABLE_STATUSES = new Set(["failed", "partial", "cancelled"]);
const EVENT_LABELS = {
  task_queued: "进入队列", task_recovered: "恢复队列任务", task_started: "开始执行", preflight_failed: "预检失败",
  ansible_started: "启动 Ansible", playbook_on_start: "开始 Playbook",
  playbook_on_play_start: "开始 Play", playbook_on_task_start: "开始 Task",
  runner_on_start: "目标开始", runner_on_ok: "执行成功",
  runner_on_failed: "执行失败", runner_on_unreachable: "目标不可达",
  runner_on_skipped: "跳过", playbook_on_stats: "执行统计",
  ansible_error: "Ansible 异常", log_truncated: "过程日志已截断", task_completed: "执行结束", task_cancelled: "任务取消", task_failed: "执行器异常终止",
};

function TaskStatus({ status }) {
  const Icon = status === "succeeded" ? CheckCircle : ACTIVE_STATUSES.has(status) ? ArrowsClockwise : status === "cancelled" || status === "skipped" ? Prohibit : XCircle;
  return <span className={`task-center-status task-center-status--${status}`}><Icon size={14} className={status === "running" ? "spin" : ""} />{STATUS_LABELS[status] || status}</span>;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function targetName(host) {
  return host.agent_name || host.runtime_name || host.server_name || "未知目标";
}

export function TaskCenterManagement({ dataSource, requestJson, initialRunId = null, focusRequest = 0, canExecute = true }) {
  const [items, setItems] = useState(DEMO_TASKS);
  const [summary, setSummary] = useState({ queued: 1, running: 1, succeeded: 1, failed: 1 });
  const [selectedId, setSelectedId] = useState(DEMO_TASKS[0].run_id);
  const [detail, setDetail] = useState(DEMO_TASKS[0]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [targetType, setTargetType] = useState("all");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailTab, setDetailTab] = useState("overview");
  const [events, setEvents] = useState([]);
  const [eventCursor, setEventCursor] = useState({ next_sequence: 0, terminal: true });
  const [eventsLoading, setEventsLoading] = useState(false);

  const loadList = useCallback(async (preferredId = null) => {
    if (dataSource !== "api") return;
    const response = await requestJson("/api/v1/automation/runs?limit=100");
    setItems(response.data);
    setSummary(response.summary || {});
    setSelectedId((current) => preferredId || (response.data.some((item) => item.run_id === current) ? current : response.data[0]?.run_id || null));
  }, [dataSource, requestJson]);

  const loadDetail = useCallback(async (runId = selectedId) => {
    if (!runId) { setDetail(null); return; }
    if (dataSource !== "api" || String(runId).startsWith("task-")) {
      const selected = items.find((item) => item.run_id === runId);
      if (selected) setDetail(selected);
      return;
    }
    const response = await requestJson(`/api/v1/automation/runs/${runId}`);
    setDetail(response.data);
  }, [dataSource, items, requestJson, selectedId]);

  const loadEvents = useCallback(async (runId = selectedId) => {
    if (!runId) { setEvents([]); return; }
    if (dataSource !== "api" || String(runId).startsWith("task-")) {
      setEvents([]);
      setEventCursor({ next_sequence: 0, terminal: !ACTIVE_STATUSES.has(detail?.status) });
      return;
    }
    setEventsLoading(true);
    try {
      const collected = [];
      let cursor = { next_sequence: 0, has_more: true, terminal: false };
      while (cursor.has_more && collected.length < 5000) {
        const response = await requestJson(`/api/v1/automation/runs/${runId}/events?after_sequence=${cursor.next_sequence}&limit=500`);
        collected.push(...(response.data || []));
        cursor = response.cursor || { next_sequence: cursor.next_sequence, has_more: false, terminal: false };
      }
      setEvents(collected);
      setEventCursor(cursor);
    } finally {
      setEventsLoading(false);
    }
  }, [dataSource, detail?.status, requestJson, selectedId]);

  const refresh = useCallback(async (runId = selectedId) => {
    await loadList(runId);
    await Promise.all([loadDetail(runId), loadEvents(runId)]);
  }, [loadDetail, loadEvents, loadList, selectedId]);

  useEffect(() => {
    loadList().catch((loadError) => setNotice(`任务接口暂不可用：${loadError.message}`));
  }, [loadList]);

  useEffect(() => {
    loadDetail().catch((loadError) => setError(loadError.message));
  }, [loadDetail]);

  useEffect(() => {
    loadEvents().catch((loadError) => setError(loadError.message));
  }, [loadEvents]);

  useEffect(() => {
    if (!initialRunId) return;
    setSelectedId(initialRunId);
    setDetailTab("events");
  }, [focusRequest, initialRunId]);

  useEffect(() => {
    if (dataSource !== "api" || !items.some((item) => ACTIVE_STATUSES.has(item.status))) return undefined;
    const timer = window.setInterval(() => {
      refresh(selectedId).catch((loadError) => setError(loadError.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [dataSource, items, refresh, selectedId]);

  const performAction = async (action, successMessage) => {
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后执行任务操作。"); return; }
    setBusy(true); setError("");
    try {
      const response = await requestJson(`/api/v1/automation/runs/${detail.run_id}/${action}`, { method: "POST", body: "{}" });
      const runId = response.data.run_id;
      setSelectedId(runId);
      await refresh(runId);
      setNotice(successMessage);
    } catch (actionError) {
      setError(actionError.message);
    } finally {
      setBusy(false);
    }
  };

  const filtered = useMemo(() => items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    return (status === "all" || item.status === status)
      && (targetType === "all" || item.target_type === targetType)
      && (!keyword || `${item.operation_name || ""} ${item.operation_code} ${item.run_id} ${(item.target_names || []).join(" ")}`.toLowerCase().includes(keyword));
  }), [items, query, status, targetType]);
  const terminalHosts = (detail?.hosts || []).filter((host) => !ACTIVE_STATUSES.has(host.status)).length;
  const completed = detail?.hosts?.length ? terminalHosts : (detail?.succeeded_count || 0) + (detail?.failed_count || 0);
  const progress = detail?.target_count ? Math.round((completed / detail.target_count) * 100) : 0;
  const eventOutput = events.map((event) => event.stdout).filter(Boolean).join("\n");

  return <section className="automation-page task-center-management">
    <header className="asset-page-heading">
      <div><span className="eyebrow">执行管理</span><h2>任务中心</h2><p>统一查看服务器、运行时和智能体的任务进度、逐目标结果与重试关系。</p></div>
      <div className="automation-heading-actions"><button disabled={busy} onClick={() => refresh().catch((loadError) => setError(loadError.message))}><ArrowsClockwise size={15} />刷新</button><div className="asset-page-total asset-page-total--runtime"><ListChecks size={24} /><div><strong>{items.length}</strong><span>个任务</span></div></div></div>
    </header>

    <div className="task-summary-grid">
      {["queued", "running", "succeeded", "failed"].map((value) => <button key={value} className={status === value ? `task-summary task-summary--${value} task-summary--active` : `task-summary task-summary--${value}`} onClick={() => setStatus(status === value ? "all" : value)}><span>{STATUS_LABELS[value]}</span><strong>{summary[value] || 0}</strong></button>)}
    </div>

    <div className="asset-toolbar automation-toolbar">
      <label className="asset-search"><MagnifyingGlass size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务、批次或执行目标" /></label>
      <label className="asset-filter"><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).filter(([value]) => !["unreachable", "skipped"].includes(value)).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label className="asset-filter"><span>对象</span><select value={targetType} onChange={(event) => setTargetType(event.target.value)}><option value="all">全部对象</option>{Object.entries(TARGET_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <span className="toolbar-summary">筛选结果 <strong>{filtered.length}</strong> 个</span>
    </div>
    {notice && <div className="prototype-notice"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="关闭提示"><X size={13} /></button></div>}
    {error && <div className="task-action-error"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭错误"><X size={13} /></button></div>}

    <div className="task-center-layout">
      <div className="task-center-list">
        <div className="task-list-head"><span>任务</span><span>执行对象</span><span>状态</span><span>进度</span><span>发起时间</span><span /></div>
        {filtered.map((item) => {
          const itemCompleted = ACTIVE_STATUSES.has(item.status) ? item.succeeded_count + item.failed_count : item.target_count;
          return <button key={item.run_id} className={item.run_id === selectedId ? "task-list-row task-list-row--selected" : "task-list-row"} onClick={() => { setSelectedId(item.run_id); setDetailTab("overview"); }}>
            <span className="task-identity"><strong>{item.operation_name || item.operation_code}</strong><small>{item.run_id}</small></span>
            <span className="task-target-cell"><strong>{TARGET_LABELS[item.target_type]}</strong><small>{(item.target_names || []).join("、") || `${item.target_count} 个目标`}</small></span>
            <TaskStatus status={item.status} />
            <span className="task-progress-cell"><span><i style={{ width: `${item.target_count ? (itemCompleted / item.target_count) * 100 : 0}%` }} /></span><small>{itemCompleted}/{item.target_count}</small></span>
            <span className="task-time-cell">{formatTime(item.created_at)}</span>
            <CaretRight size={15} />
          </button>;
        })}
        {!filtered.length && <div className="task-list-empty">没有符合条件的任务</div>}
      </div>

      <aside className="task-detail-card task-detail-card--full">
        {detail ? <>
          <header><div><TaskStatus status={detail.status} /><h3>{detail.operation_name || detail.operation_code}</h3><code>{detail.run_id}</code></div></header>
          {canExecute && <div className="task-detail-actions"><button disabled={busy || !RETRYABLE_STATUSES.has(detail.status)} onClick={() => performAction("retry", "已创建重试任务，只包含原任务中失败或跳过的目标。") }><ArrowsClockwise size={14} />重试失败目标</button><button disabled={busy || detail.status !== "queued"} onClick={() => performAction("cancel", "任务已在开始执行前取消。") }><Prohibit size={14} />取消任务</button></div>}
          <div className="automation-detail-tabs task-detail-tabs">{[["overview", "概览"], ["events", `执行过程（${detail.event_count ?? events.length}）`], ["results", `目标结果（${detail.hosts?.length || detail.target_count}）`]].map(([value, label]) => <button key={value} className={detailTab === value ? "active" : ""} onClick={() => setDetailTab(value)}>{label}</button>)}</div>
          {detailTab === "overview" && <>
            <section><h4>执行进度</h4><div className="task-detail-progress"><div><strong>{progress}%</strong><span>{completed} / {detail.target_count} 个目标完成</span></div><span><i style={{ width: `${progress}%` }} /></span><dl><div><dt>成功</dt><dd>{detail.succeeded_count}</dd></div><div><dt>失败</dt><dd>{detail.failed_count}</dd></div><div><dt>等待</dt><dd>{Math.max(0, detail.target_count - completed)}</dd></div></dl></div></section>
            <section><h4>任务信息</h4><dl className="task-detail-fields"><div><dt>适用对象</dt><dd>{TARGET_LABELS[detail.target_type]}</dd></div><div><dt>自动化版本</dt><dd>{detail.version_label ? `v${detail.version_label}` : detail.playbook_version}</dd></div><div><dt>分类</dt><dd>{detail.category || "—"}</dd></div><div><dt>风险等级</dt><dd>{detail.risk_level || "—"}</dd></div><div><dt>发起人</dt><dd>{detail.requested_by}</dd></div><div><dt>创建时间</dt><dd>{formatTime(detail.created_at)}</dd></div><div><dt>开始时间</dt><dd>{formatTime(detail.started_at)}</dd></div><div><dt>结束时间</dt><dd>{formatTime(detail.finished_at)}</dd></div></dl></section>
            <section><div className="automation-section-title"><h4>执行参数</h4><span>{Object.keys(detail.parameters || {}).length}</span></div><pre className="task-parameters-json">{JSON.stringify(detail.parameters || {}, null, 2)}</pre></section>
          </>}
          {detailTab === "events" && <section className="task-event-section"><div className="automation-section-title"><h4>Ansible 执行过程</h4><span>{events.length}</span></div>{eventsLoading && !events.length ? <div className="task-events-empty"><ArrowsClockwise size={18} className="spin" />正在读取执行事件</div> : <div className="task-event-timeline">{events.map((event) => <article key={event.operation_event_id} className={`task-event task-event--${event.event_level}`}><i /><div><header><strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong><time>#{event.sequence_no} · {formatTime(event.occurred_at)}</time></header><span>{event.task_name || event.play_name || event.target_key || "任务级事件"}</span>{event.task_action && <code>{event.task_action}</code>}{event.stdout && <pre>{event.stdout}</pre>}</div></article>)}</div>}{!eventsLoading && !events.length && <div className="task-events-empty">该任务尚未产生执行事件</div>}{eventOutput && <details className="task-combined-output"><summary>查看合并输出</summary><pre>{eventOutput}</pre></details>}<div className="task-event-cursor">已读取至事件 #{eventCursor.next_sequence || 0}{eventCursor.terminal ? " · 任务已结束" : " · 任务执行中，页面会自动刷新"}</div></section>}
          {detailTab === "results" && <section><div className="automation-section-title"><h4>目标执行结果</h4><span>{detail.hosts?.length || detail.target_count}</span></div><div className="task-host-results task-host-results--detail">{detail.hosts?.length ? detail.hosts.map((host) => <details key={host.host_result_id}><summary><TaskStatus status={host.status} /><span><strong>{targetName(host)}</strong><small>{host.error_message || (host.finished_at ? `完成于 ${formatTime(host.finished_at)}` : host.started_at ? `开始于 ${formatTime(host.started_at)}` : "等待执行")}</small></span></summary><div className="task-host-output">{host.error_message && <p>{host.error_message}</p>}{host.target_plan && <details><summary>查看冻结的下发计划</summary><pre>{JSON.stringify(host.target_plan, null, 2)}</pre></details>}<pre>{JSON.stringify(host.result || {}, null, 2)}</pre></div></details>) : (detail.target_names || []).map((name, index) => <div key={name}><TaskStatus status={index < detail.succeeded_count ? "succeeded" : detail.status === "failed" ? "failed" : detail.status} /><span><strong>{name}</strong><small>{index < detail.succeeded_count ? "执行成功" : "等待结果"}</small></span></div>)}</div></section>}
          {detail.retry_of_run_id && <div className="retry-relation"><Clock size={15} />本任务重试自 <code>{detail.retry_of_run_id}</code></div>}
        </> : <div className="task-list-empty">暂无任务明细</div>}
      </aside>
    </div>
  </section>;
}

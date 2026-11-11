import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  CaretRight,
  CheckCircle,
  Cube,
  HardDrives,
  ListChecks,
  MagnifyingGlass,
  Prohibit,
  Robot,
  Users,
  XCircle,
} from "@phosphor-icons/react";
import { displayRuntimeName } from "./serverUtils.js";
import { buildAgentOptions, buildRelationshipWire, filterOverviewItems, taskProgress, taskSummaryTotal } from "./overviewUtils.js";

const TASK_STATUS_LABELS = {
  queued: "等待执行",
  running: "执行中",
  succeeded: "成功",
  partial: "部分成功",
  failed: "失败",
  cancelled: "已取消",
};
const TARGET_LABELS = { server: "服务器", runtime: "运行时", agent: "智能体" };

function Metric({ icon: Icon, value, label, onClick, tone = "teal" }) {
  return (
    <button type="button" className={`metric metric--action metric--${tone}`} onClick={onClick} aria-label={`${label} ${value}，打开${label}`}>
      <Icon size={33} weight="regular" />
      <span className="metric-copy"><strong>{value}</strong><span>{label}</span></span>
      <CaretRight size={15} className="metric-caret" />
    </button>
  );
}

function SectionTitle({ icon: Icon, children, meta }) {
  return (
    <div className="section-title">
      <div><Icon size={20} weight="regular" /><h2>{children}</h2></div>
      <span>{meta}</span>
    </div>
  );
}

function SearchField({ value, onChange, label, placeholder }) {
  return (
    <label className="relationship-search">
      <span className="sr-only">{label}</span>
      <MagnifyingGlass size={15} />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} aria-label={label} />
    </label>
  );
}

function RelationshipWires({ wires }) {
  return (
    <svg className="relationship-wires" aria-hidden="true">
      <defs>
        {wires.map((wire) => <linearGradient key={wire.key} id={`relationship-gradient-${wire.key}`} gradientUnits="userSpaceOnUse" x1={wire.startX} x2={wire.endX} y1="0" y2="0"><stop offset="0" stopColor={wire.startColor} /><stop offset="1" stopColor={wire.endColor} /></linearGradient>)}
      </defs>
      {wires.map((wire) => {
        const path = `M ${wire.startX} ${wire.startY} H ${wire.midX} V ${wire.endY} H ${wire.endX}`;
        return <g key={wire.key}>
          <path className="relationship-wire-halo" d={path} />
          <path className="relationship-wire-path" d={path} stroke={`url(#relationship-gradient-${wire.key})`} />
          <circle className="relationship-wire-dot" cx={wire.startX} cy={wire.startY} r="4.5" fill={wire.startColor} />
          <circle className="relationship-wire-dot" cx={wire.endX} cy={wire.endY} r="4.5" fill={wire.endColor} />
        </g>;
      })}
    </svg>
  );
}

function EmptySelection({ icon: Icon, title, detail }) {
  return <div className="relationship-empty"><Icon size={26} /><strong>{title}</strong><span>{detail}</span></div>;
}

function TaskStatus({ status }) {
  const Icon = status === "succeeded" ? CheckCircle : ["queued", "running"].includes(status) ? ArrowsClockwise : status === "cancelled" ? Prohibit : XCircle;
  return <span className={`overview-task-status overview-task-status--${status}`}><Icon size={13} className={status === "running" ? "spin" : ""} />{TASK_STATUS_LABELS[status] || status}</span>;
}

function formatOverviewTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function RecentTaskExecution({ items, summary, onOpenTask, onNavigate }) {
  const total = taskSummaryTotal(summary) || items.length;
  return (
    <section className="execution-section" aria-labelledby="recent-task-title">
      <header className="execution-title">
        <div><ListChecks size={19} /><span><strong id="recent-task-title">近期任务执行</strong><small>服务器、运行时与智能体的统一执行反馈</small></span></div>
        <div className="execution-summary" aria-label="任务状态汇总">
          {["running", "queued", "succeeded", "failed"].map((status) => <span key={status} className={`execution-summary-chip execution-summary-chip--${status}`}><i />{TASK_STATUS_LABELS[status]} <strong>{summary[status] || 0}</strong></span>)}
        </div>
      </header>
      <div className="execution-list-wrap">
        <div className="execution-list-head"><span>任务</span><span>执行对象</span><span>状态</span><span>完成进度</span><span>发起时间</span><span /></div>
        <div className="execution-list">
          {items.map((task) => {
            const progress = taskProgress(task);
            return (
              <button type="button" className="execution-row" key={task.run_id} onClick={() => onOpenTask(task.run_id)}>
                <span className="execution-identity"><strong>{task.operation_name || task.operation_code}</strong><small>{task.run_id}</small></span>
                <span className="execution-target"><strong>{TARGET_LABELS[task.target_type] || task.target_type}</strong><small>{(task.target_names || []).slice(0, 2).join("、") || `${task.target_count || 0} 个目标`}</small></span>
                <TaskStatus status={task.status} />
                <span className="execution-progress"><span><i style={{ width: `${progress.percent}%` }} /></span><small>{progress.completed}/{task.target_count || 0}</small></span>
                <time>{formatOverviewTime(task.created_at)}</time>
                <CaretRight size={15} />
              </button>
            );
          })}
          {!items.length && <div className="execution-empty"><ListChecks size={24} /><span>暂无任务执行记录</span></div>}
        </div>
      </div>
      <button type="button" className="execution-view-all" onClick={() => onNavigate("任务中心")}>查看全部 {total} 个任务<CaretRight size={15} /></button>
    </section>
  );
}

export function OverviewDashboard({
  counts,
  servers,
  selectedServerId,
  selectedRuntimeId,
  runtimes,
  agents,
  recentTasks,
  taskSummary,
  onChooseServer,
  onChooseRuntime,
  onNavigate,
  onOpenTask,
}) {
  const [serverQuery, setServerQuery] = useState("");
  const [runtimeQuery, setRuntimeQuery] = useState("");
  const [agentQuery, setAgentQuery] = useState("");
  const agentOptions = useMemo(() => buildAgentOptions(agents), [agents]);
  const [selectedAgentId, setSelectedAgentId] = useState(agentOptions[0]?.agent_id || null);
  const gridRef = useRef(null);
  const serverListRef = useRef(null);
  const runtimeListRef = useRef(null);
  const agentListRef = useRef(null);
  const selectedServerCardRef = useRef(null);
  const selectedRuntimeCardRef = useRef(null);
  const selectedAgentCardRef = useRef(null);
  const [relationshipWires, setRelationshipWires] = useState([]);

  const filteredServers = useMemo(() => filterOverviewItems(servers, serverQuery, (server) => `${server.name} ${server.internal_ip || ""} ${server.environment_code || ""}`), [serverQuery, servers]);
  const filteredRuntimes = useMemo(() => filterOverviewItems(runtimes, runtimeQuery, (runtime) => `${runtime.name} ${runtime.node_id || ""} ${runtime.desired_version || ""}`), [runtimeQuery, runtimes]);
  const filteredAgents = useMemo(() => filterOverviewItems(agentOptions, agentQuery, (agent) => `${agent.name} ${agent.agent_id} ${agent.version || ""}`), [agentOptions, agentQuery]);

  useEffect(() => {
    setSelectedAgentId((current) => agentOptions.some((agent) => agent.agent_id === current) ? current : agentOptions[0]?.agent_id || null);
  }, [agentOptions, selectedRuntimeId]);

  useLayoutEffect(() => {
    function revealInside(container, element) {
      if (!container || !element) return;
      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      if (elementRect.top < containerRect.top) container.scrollTop -= containerRect.top - elementRect.top;
      else if (elementRect.bottom > containerRect.bottom) container.scrollTop += elementRect.bottom - containerRect.bottom;
    }
    revealInside(serverListRef.current, selectedServerCardRef.current);
    revealInside(runtimeListRef.current, selectedRuntimeCardRef.current);
    revealInside(agentListRef.current, selectedAgentCardRef.current);
  }, [agentQuery, filteredAgents.length, filteredRuntimes.length, filteredServers.length, runtimeQuery, selectedAgentId, selectedRuntimeId, selectedServerId, serverQuery]);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid) return undefined;

    function measureWire(fromElement, toElement, fromViewport, toViewport, key, startColor, endColor) {
      if (!fromElement || !toElement || !fromViewport || !toViewport) return null;
      return buildRelationshipWire({
        gridRect: grid.getBoundingClientRect(),
        fromRect: fromElement.getBoundingClientRect(),
        toRect: toElement.getBoundingClientRect(),
        fromViewportRect: fromViewport.getBoundingClientRect(),
        toViewportRect: toViewport.getBoundingClientRect(),
        key,
        startColor,
        endColor,
      });
    }

    function updateWires() {
      setRelationshipWires([
        measureWire(selectedServerCardRef.current, selectedRuntimeCardRef.current, serverListRef.current, runtimeListRef.current, "server-runtime", "#0b969b", "#176ec5"),
        measureWire(selectedRuntimeCardRef.current, selectedAgentCardRef.current, runtimeListRef.current, agentListRef.current, "runtime-agent", "#176ec5", "#7667d6"),
      ].filter(Boolean));
    }

    updateWires();
    const frame = window.requestAnimationFrame(updateWires);
    const observer = new ResizeObserver(updateWires);
    [grid, selectedServerCardRef.current, selectedRuntimeCardRef.current, selectedAgentCardRef.current].filter(Boolean).forEach((element) => observer.observe(element));
    const scrollElements = [serverListRef.current, runtimeListRef.current, agentListRef.current].filter(Boolean);
    scrollElements.forEach((element) => element.addEventListener("scroll", updateWires, { passive: true }));
    window.addEventListener("resize", updateWires);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      scrollElements.forEach((element) => element.removeEventListener("scroll", updateWires));
      window.removeEventListener("resize", updateWires);
    };
  }, [agentQuery, filteredAgents.length, filteredRuntimes.length, filteredServers.length, runtimeQuery, selectedAgentId, selectedRuntimeId, selectedServerId, serverQuery]);

  return <>
    <section className="metrics" aria-label="核心指标">
      <Metric icon={HardDrives} value={counts.servers} label="服务器" onClick={() => onNavigate("服务器")} />
      <Metric icon={Cube} value={counts.runtimes} label="运行时" onClick={() => onNavigate("运行时")} tone="blue" />
      <Metric icon={Users} value={counts.primaryAgents} label="主智能体" onClick={() => onNavigate("智能体")} tone="purple" />
      <Metric icon={ListChecks} value={counts.completedTasks} label="已完成任务" onClick={() => onNavigate("任务中心")} tone="success" />
    </section>

    <section className="relationship-grid" ref={gridRef} aria-label="资产关系选择器">
      <RelationshipWires wires={relationshipWires} />
      <article className="relationship-column server-column">
        <SectionTitle icon={HardDrives} meta={`${servers.length} 台`}>服务器</SectionTitle>
        <SearchField value={serverQuery} onChange={setServerQuery} label="搜索服务器" placeholder="搜索名称、IP 或环境" />
        <div className="object-list relationship-scroll" ref={serverListRef} tabIndex="0" role="region" aria-label="服务器选项">
          {filteredServers.map((server) => {
            const selected = server.server_id === selectedServerId;
            return selected ? (
              <button type="button" ref={selectedServerCardRef} key={server.server_id} aria-pressed="true" className="server-card selected-card" onClick={() => onChooseServer(server)}>
                <div className="card-name"><i className={`status-dot status-dot--${server.lifecycle_status === "maintenance" ? "amber" : server.lifecycle_status === "active" ? "green" : "muted"}`} />{server.name}</div>
                <div className="card-meta">{server.deployment_mode || "baremetal"}<span>·</span>{server.os_name || "Linux"} {server.os_version ? `(${server.os_version})` : ""}</div>
                <div className="card-subline">{server.observed_agents || 0} 个已观测智能体</div>
                <span className="server-ip">{server.internal_ip || "未配置 IP"}</span>
              </button>
            ) : (
              <button type="button" key={server.server_id} aria-pressed="false" className="compact-row" onClick={() => onChooseServer(server)}>
                <span><i className={`status-dot status-dot--${server.lifecycle_status === "maintenance" ? "amber" : server.lifecycle_status === "active" ? "green" : "muted"}`} />{server.name}</span><small>{server.internal_ip || "未配置 IP"}</small>
              </button>
            );
          })}
          {!filteredServers.length && <EmptySelection icon={HardDrives} title="没有匹配的服务器" detail="请调整搜索关键词" />}
        </div>
        <button type="button" className="relationship-footer" onClick={() => onNavigate("服务器")}><span>显示 {filteredServers.length} / {servers.length}</span><strong>服务器管理<CaretRight size={14} /></strong></button>
      </article>

      <article className="relationship-column runtime-column">
        <SectionTitle icon={Cube} meta={`${runtimes.length} 个`}>运行时</SectionTitle>
        <SearchField value={runtimeQuery} onChange={setRuntimeQuery} label="搜索运行时" placeholder="搜索名称、节点或版本" />
        <div className="object-list relationship-scroll" ref={runtimeListRef} tabIndex="0" role="region" aria-label="运行时选项">
          {filteredRuntimes.map((runtime) => {
            const selected = runtime.runtime_id === selectedRuntimeId;
            return selected ? (
              <button type="button" ref={selectedRuntimeCardRef} key={runtime.runtime_id} aria-pressed="true" className="runtime-card selected-card selected-card--blue" onClick={() => onChooseRuntime(runtime)}>
                <div className="card-name"><i className={`status-dot status-dot--${runtime.observed_status === "degraded" ? "amber" : "blue"}`} />{displayRuntimeName(runtime.name)}</div>
                <div className="card-meta">版本 {runtime.desired_version || "未设置"}<span>·</span>{runtime.lifecycle_status === "active" ? "运行中" : "已停止"}</div>
                <div className="runtime-detail">节点 ID: {runtime.node_id || String(runtime.runtime_id).slice(0, 20)}<br />部署路径: {runtime.config_path || "/opt/runtime"}</div>
              </button>
            ) : (
              <button type="button" key={runtime.runtime_id} aria-pressed="false" className="runtime-row" onClick={() => onChooseRuntime(runtime)}>
                <div className="card-name"><i className={`status-dot status-dot--${runtime.observed_status === "degraded" ? "amber" : "blue"}`} />{displayRuntimeName(runtime.name)}</div>
                <div>版本 {runtime.desired_version || "未设置"}<span>·</span>{runtime.lifecycle_status === "active" ? "运行中" : "已停止"}</div>
              </button>
            );
          })}
          {!filteredRuntimes.length && <EmptySelection icon={Cube} title={runtimes.length ? "没有匹配的运行时" : "暂无运行时"} detail={runtimes.length ? "请调整搜索关键词" : "该服务器尚未关联运行时"} />}
        </div>
        <button type="button" className="relationship-footer" onClick={() => onNavigate("运行时")}><span>显示 {filteredRuntimes.length} / {runtimes.length}</span><strong>运行时管理<CaretRight size={14} /></strong></button>
      </article>

      <article className="relationship-column agent-column">
        <SectionTitle icon={Robot} meta={`${agentOptions.length} 个`}>智能体</SectionTitle>
        <SearchField value={agentQuery} onChange={setAgentQuery} label="搜索智能体" placeholder="搜索名称、ID 或版本" />
        <div className="object-list relationship-scroll agent-option-list" ref={agentListRef} tabIndex="0" role="region" aria-label="智能体选项">
          {filteredAgents.map((agent) => {
            const selected = agent.agent_id === selectedAgentId;
            return selected ? (
              <button type="button" ref={selectedAgentCardRef} key={agent.agent_id} aria-pressed="true" className="agent-option-card selected-card selected-card--purple" onClick={() => setSelectedAgentId(agent.agent_id)}>
                <div className="card-name"><i className="status-dot status-dot--purple" />{agent.name}<span className="primary-tag">{agent.agent_kind === "primary" ? "主" : "子"}</span></div>
                <div className="card-meta">状态: {agent.lifecycle_status === "active" ? "运行中" : "已停止"}<span>·</span>版本: {agent.version || "未发现"}</div>
                <div className="runtime-detail">智能体 ID: {agent.agent_id}</div>
              </button>
            ) : (
              <button type="button" key={agent.agent_id} aria-pressed="false" className="agent-option-row" onClick={() => setSelectedAgentId(agent.agent_id)}>
                <span><i className="status-dot status-dot--purple" /><strong>{agent.name}</strong><em>{agent.agent_kind === "primary" ? "主" : "子"}</em></span>
                <small>{agent.version || "未发现"}</small>
              </button>
            );
          })}
          {!filteredAgents.length && <EmptySelection icon={Robot} title={agentOptions.length ? "没有匹配的智能体" : "暂无智能体"} detail={agentOptions.length ? "请调整搜索关键词" : "该运行时尚未建立活动部署"} />}
        </div>
        <button type="button" className="relationship-footer" onClick={() => onNavigate("智能体")}><span>显示 {filteredAgents.length} / {agentOptions.length}</span><strong>智能体管理<CaretRight size={14} /></strong></button>
      </article>
    </section>

    <RecentTaskExecution items={recentTasks} summary={taskSummary} onOpenTask={onOpenTask} onNavigate={onNavigate} />
  </>;
}

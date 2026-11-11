import { useEffect, useState } from "react";
import {
  Books,
  Buildings,
  Pulse,
  CaretDown,
  Cube,
  Database,
  FlowArrow,
  Copy,
  HardDrives,
  House,
  Key,
  ListChecks,
  LockKey,
  Robot,
  ShieldChevron,
  SignOut,
  SquaresFour,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import { ServerManagement } from "./ServerManagement.jsx";
import { RuntimeManagement } from "./RuntimeManagement.jsx";
import { AgentManagement } from "./AgentManagement.jsx";
import { GroupManagement } from "./GroupManagement.jsx";
import { DataQualityManagement } from "./DataQualityManagement.jsx";
import { AutomationLibraryManagement } from "./AutomationLibraryManagement.jsx";
import { DEMO_TASKS, TaskCenterManagement } from "./TaskCenterManagement.jsx";
import { OverviewDashboard } from "./OverviewDashboard.jsx";
import { OVERVIEW_DEMO } from "./overviewDemoData.js";
import { LoginPage } from "./LoginPage.jsx";
import { UserManagement } from "./UserManagement.jsx";
import { OrganizationManagement } from "./OrganizationManagement.jsx";
import { ObservabilityIntegration } from "./ObservabilityIntegration.jsx";
import { resolveAppRequestPath } from "./basePathUtils.js";
import { loadPaginatedItems } from "./organizationUtils.js";
import { buildAgentMapFromCatalog, completedTaskCount } from "./overviewUtils.js";
import { authFailureState, demoDataAllowed, formatFreshness } from "./runtimeMode.js";

const DEMO_ALLOWED = demoDataAllowed(import.meta.env);

const NAV_ITEMS = [
  ["总览", House], ["服务器", HardDrives], ["运行时", Cube], ["智能体", Robot], ["组织管理", Buildings], ["自动化库", Books], ["任务中心", ListChecks], ["管理分组", SquaresFour], ["数据导入", Database], ["可观测接入", FlowArrow],
];

async function getJson(path, options = {}) {
  const { confirmed = false, ...requestOptions } = options;
  const method = String(requestOptions.method || "GET").toUpperCase();
  const isFormData = typeof FormData !== "undefined" && requestOptions.body instanceof FormData;
  const csrfToken = document.cookie.split("; ").find((item) => item.startsWith("agentswatch_csrf="))?.split("=").slice(1).join("=");
  const headers = {
    Accept: "application/json",
    ...(requestOptions.body && !isFormData ? { "Content-Type": "application/json" } : {}),
    ...(method !== "GET" && csrfToken ? { "X-CSRF-Token": decodeURIComponent(csrfToken) } : {}),
    ...requestOptions.headers,
  };
  const response = await fetch(resolveAppRequestPath(path), { credentials: "same-origin", ...requestOptions, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let detail = null;
    try {
      detail = (await response.json()).detail;
      message = typeof detail === "string" ? detail : detail?.message || message;
    } catch { /* use status text */ }
    if (
      response.status === 409
      && detail?.code === "CONFIRMATION_REQUIRED"
      && !requestOptions.headers?.["X-Confirmation-ID"]
    ) {
      if (!confirmed && !window.confirm(detail.message || "此操作需要二次确认，是否继续？")) {
        const cancelled = new Error("操作已取消");
        cancelled.status = 409;
        cancelled.code = "CONFIRMATION_CANCELLED";
        throw cancelled;
      }
      return getJson(path, {
        ...requestOptions,
        confirmed,
        headers: { ...requestOptions.headers, "X-Confirmation-ID": detail.confirmation_id },
      });
    }
    const error = new Error(message);
    error.status = response.status;
    error.code = detail?.code;
    error.detail = detail;
    throw error;
  }
  return response.json();
}

function PasswordChangeDialog({ onClose, onChanged }) {
  const [form, setForm] = useState({ old_password: "", new_password: "", confirmation: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (form.new_password !== form.confirmation) { setError("两次输入的新密码不一致"); return; }
    setBusy(true); setError("");
    try {
      await getJson("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password: form.old_password, new_password: form.new_password }),
      });
      onChanged();
    } catch (changeError) { setError(changeError.message); }
    finally { setBusy(false); }
  }

  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="management-dialog password-dialog" role="dialog" aria-modal="true" aria-label="修改密码"><header><h3>修改登录密码</h3><button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button></header><form onSubmit={submit}><div className="management-form-grid"><label className="management-field management-field--wide"><span>当前密码</span><input required type="password" autoComplete="current-password" value={form.old_password} onChange={(event) => setForm({ ...form, old_password: event.target.value })} /></label><label className="management-field management-field--wide"><span>新密码</span><input required type="password" autoComplete="new-password" value={form.new_password} onChange={(event) => setForm({ ...form, new_password: event.target.value })} /><small>至少 12 位，包含大写字母、小写字母、数字和符号，且不能包含用户名。</small></label><label className="management-field management-field--wide"><span>确认新密码</span><input required type="password" autoComplete="new-password" value={form.confirmation} onChange={(event) => setForm({ ...form, confirmation: event.target.value })} /></label></div>{error && <div className="management-form-error">{error}</div>}<div className="management-dialog-actions"><button type="button" onClick={onClose}>取消</button><button className="primary-action" disabled={busy}>{busy ? "正在更新" : "更新密码"}</button></div></form></section></div>;
}

function formatApiKeyTime(value) {
  if (!value) return "尚未使用";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function ApiKeyDialog({ requestJson, onClose }) {
  const [status, setStatus] = useState(null);
  const [revealedKey, setRevealedKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    requestJson("/api/v1/auth/api-key")
      .then((response) => { if (!cancelled) setStatus(response.data); })
      .catch((loadError) => { if (!cancelled) setError(loadError.message); });
    return () => { cancelled = true; };
  }, [requestJson]);

  async function issue(path, method) {
    setBusy(true); setError(""); setCopied(false);
    try {
      const response = await requestJson(path, { method, body: method === "DELETE" ? undefined : "{}" });
      setStatus(response.data);
      setRevealedKey(response.data.api_key || "");
    } catch (issueError) { setError(issueError.message); }
    finally { setBusy(false); }
  }

  async function copyKey() {
    try {
      await navigator.clipboard.writeText(revealedKey);
      setCopied(true);
    } catch { setError("浏览器未允许复制，请手动选择并复制 API Key"); }
  }

  function rotate() {
    if (window.confirm("刷新后旧 API Key 会立即失效，是否继续？")) issue("/api/v1/auth/api-key/rotate", "POST");
  }

  function revoke() {
    if (window.confirm("撤销后使用当前 API Key 的调用会立即失败，是否继续？")) issue("/api/v1/auth/api-key", "DELETE");
  }

  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}><section className="management-dialog password-dialog api-key-dialog" role="dialog" aria-modal="true" aria-label="API Key"><header><h3>API Key</h3><button type="button" onClick={onClose} disabled={busy} aria-label="关闭"><X size={18} /></button></header><div className="api-key-content">{status === null && !error ? <div className="api-key-loading">正在读取 API Key 状态…</div> : <><div className={status?.active ? "api-key-status api-key-status--active" : "api-key-status"}><Key size={21} /><div><strong>{status?.active ? "API Key 已启用" : "尚未创建 API Key"}</strong><span>{status?.active ? status.prefix : "创建后可用于 REST API 与 MCP 服务"}</span></div></div>{status?.active && <dl className="api-key-meta"><div><dt>创建时间</dt><dd>{formatApiKeyTime(status.created_at)}</dd></div><div><dt>最近使用</dt><dd>{formatApiKeyTime(status.last_used_at)}</dd></div></dl>}{revealedKey && <div className="api-key-secret"><strong>请立即保存，关闭后将无法再次查看完整 Key</strong><div><input readOnly value={revealedKey} aria-label="新 API Key" /><button type="button" onClick={copyKey}><Copy size={16} />{copied ? "已复制" : "复制"}</button></div></div>}</>}{error && <div className="management-form-error">{error}</div>}<p className="api-key-help">每位用户保留一把长期有效的 Key；权限与当前账号角色一致，刷新或撤销会立即使旧 Key 失效。</p></div><footer className="management-dialog-actions"><button type="button" onClick={onClose} disabled={busy}>关闭</button>{status?.active ? <><button type="button" className="danger-action" onClick={revoke} disabled={busy}>撤销</button><button type="button" className="primary-action" onClick={rotate} disabled={busy}>{busy ? "正在处理" : "刷新 Key"}</button></> : <button type="button" className="primary-action" onClick={() => issue("/api/v1/auth/api-key", "POST")} disabled={busy || status === null}>{busy ? "正在创建" : "创建 Key"}</button>}</footer></section></div>;
}

function ServiceUnavailablePage() {
  return (
    <div className="service-unavailable" role="alert">
      <span className="service-unavailable-mark"><ShieldChevron size={34} weight="fill" /></span>
      <h1>无法连接管理 API</h1>
      <p>AgentsWatch 不会在连接失败时回退到演示数据，以免把合成资产误认为生产清单。请确认管理服务、数据库与反向代理状态后重试。</p>
      <div className="service-unavailable-actions">
        <button type="button" className="primary-action" onClick={() => window.location.reload()}>重新连接</button>
        <a href="./healthz" target="_blank" rel="noreferrer">查看健康检查</a>
      </div>
      <small>生产构建默认关闭离线演示数据；仅本地开发或显式设置 <code>VITE_ALLOW_DEMO=true</code> 时启用。</small>
    </div>
  );
}

export function App() {
  const [authState, setAuthState] = useState("checking");
  const [currentUser, setCurrentUser] = useState(null);
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [apiKeyDialogOpen, setApiKeyDialogOpen] = useState(false);
  const [loginNotice, setLoginNotice] = useState("");
  const [counts, setCounts] = useState(OVERVIEW_DEMO.counts);
  const [servers, setServers] = useState(OVERVIEW_DEMO.servers);
  const [selectedServerId, setSelectedServerId] = useState(OVERVIEW_DEMO.servers[0].server_id);
  const [runtimeMap, setRuntimeMap] = useState(OVERVIEW_DEMO.runtimes);
  const [selectedRuntimeId, setSelectedRuntimeId] = useState(OVERVIEW_DEMO.runtimes[OVERVIEW_DEMO.servers[0].server_id][0].runtime_id);
  const [agentMap, setAgentMap] = useState(OVERVIEW_DEMO.agents);
  const [recentTasks, setRecentTasks] = useState(DEMO_TASKS);
  const [taskSummary, setTaskSummary] = useState({ queued: 1, running: 1, succeeded: 1, failed: 1 });
  const [dataSource, setDataSource] = useState(DEMO_ALLOWED ? "demo" : "api");
  const [lastUpdated, setLastUpdated] = useState(null);
  const [environmentOpen, setEnvironmentOpen] = useState(false);
  const [activeNav, setActiveNav] = useState("总览");
  const [focusedTask, setFocusedTask] = useState({ runId: null, request: 0 });

  useEffect(() => {
    let cancelled = false;
    getJson("/api/v1/auth/me").then((response) => {
      if (!cancelled) { setCurrentUser(response.data); setAuthState("authenticated"); }
    }).catch((error) => {
      if (cancelled) return;
      const nextState = authFailureState(error, import.meta.env);
      if (nextState === "unauthenticated") {
        setAuthState("unauthenticated");
        return;
      }
      setAuthState(nextState);
      setDataSource(nextState === "offline" ? "demo" : "api");
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (authState !== "authenticated") return undefined;
    let cancelled = false;
    async function load() {
      try {
        const [overview, liveServers, taskResponse, liveAgents] = await Promise.all([
          getJson("/api/v1/overview"),
          loadPaginatedItems(getJson, "/api/v1/servers", "servers"),
          getJson("/api/v1/automation/runs?limit=6").catch(() => null),
          loadPaginatedItems(getJson, "/api/v1/agents", "agents"),
        ]);
        if (cancelled) return;
        const c = overview.data.counts;
        const nextTaskSummary = taskResponse?.summary || taskSummary;
        setCounts({ servers: c.servers, runtimes: c.runtimes, primaryAgents: c.primary_agents, completedTasks: completedTaskCount(nextTaskSummary) });
        if (taskResponse) {
          setRecentTasks(taskResponse.data || []);
          setTaskSummary(nextTaskSummary);
        }
        if (!liveServers.length) { setServers([]); setDataSource("api"); return; }
        const normalizedServers = liveServers.map((server) => ({ ...server, observed_agents: 0 }));
        const firstServerId = normalizedServers[0].server_id;
        const liveRuntimes = await loadPaginatedItems(getJson, `/api/v1/runtimes?server_id=${firstServerId}`, "runtimes");
        const liveRuntimeMap = { [firstServerId]: liveRuntimes };
        const liveAgentMap = buildAgentMapFromCatalog(liveAgents);
        if (liveRuntimes[0]) {
          const detail = await getJson(`/api/v1/runtimes/${liveRuntimes[0].runtime_id}`);
          const deployments = detail.data.active_deployments || [];
          const primary = deployments[0] ? {
            agent_id: deployments[0].agent_id,
            name: deployments[0].agent_name,
            lifecycle_status: "active",
            version: detail.data.latest_snapshot?.actual_version || detail.data.desired_version || "未发现",
            service_archetype: deployments[0].service_archetype,
          } : null;
          const mappedAgents = liveAgentMap[liveRuntimes[0].runtime_id] || { primary: null, subagents: [] };
          liveAgentMap[liveRuntimes[0].runtime_id] = { primary: primary || mappedAgents.primary, subagents: mappedAgents.subagents };
        }
        setServers(normalizedServers);
        setRuntimeMap((current) => ({ ...current, ...liveRuntimeMap }));
        setAgentMap((current) => ({ ...current, ...liveAgentMap }));
        setSelectedServerId(firstServerId);
        setSelectedRuntimeId(liveRuntimes[0]?.runtime_id || null);
        setDataSource("api");
        setLastUpdated(new Date());
      } catch {
        if (DEMO_ALLOWED) {
          setDataSource("demo");
          return;
        }
        setAuthState("unavailable");
      }
    }
    load();
    return () => { cancelled = true; };
  }, [authState]);

  const selectedServer = servers.find((item) => item.server_id === selectedServerId) || servers[0];
  const runtimes = runtimeMap[selectedServerId] || [];
  const selectedRuntime = runtimes.find((item) => item.runtime_id === selectedRuntimeId) || runtimes[0];
  const agents = selectedRuntime ? agentMap[selectedRuntime.runtime_id] || { primary: null, subagents: [] } : { primary: null, subagents: [] };

  async function chooseServer(server) {
    setSelectedServerId(server.server_id);
    if (dataSource === "api" && !runtimeMap[server.server_id]) {
      try {
        const liveRuntimes = await loadPaginatedItems(getJson, `/api/v1/runtimes?server_id=${server.server_id}`, "runtimes");
        setRuntimeMap((current) => ({ ...current, [server.server_id]: liveRuntimes }));
        setSelectedRuntimeId(liveRuntimes[0]?.runtime_id || null);
        return;
      } catch { /* retain a stable empty state */ }
    }
    setSelectedRuntimeId((runtimeMap[server.server_id] || [])[0]?.runtime_id || null);
  }

  async function chooseRuntime(runtime) {
    setSelectedRuntimeId(runtime.runtime_id);
    if (dataSource === "api" && !agentMap[runtime.runtime_id]) {
      try {
        const detail = await getJson(`/api/v1/runtimes/${runtime.runtime_id}`);
        const deployments = detail.data.active_deployments || [];
        const primary = deployments[0] ? {
          agent_id: deployments[0].agent_id,
          name: deployments[0].agent_name,
          lifecycle_status: "active",
          version: detail.data.latest_snapshot?.actual_version || detail.data.desired_version || "未发现",
          service_archetype: deployments[0].service_archetype,
        } : null;
        setAgentMap((current) => ({ ...current, [runtime.runtime_id]: { primary, subagents: [] } }));
      } catch { /* retain a stable empty state */ }
    }
  }

  async function refreshServers() {
    const [overview, serverItems, taskResponse] = await Promise.all([
      getJson("/api/v1/overview"),
      loadPaginatedItems(getJson, "/api/v1/servers", "servers"),
      getJson("/api/v1/automation/runs?limit=6").catch(() => null),
    ]);
    const liveServers = serverItems.map((server) => ({ ...server, observed_agents: 0 }));
    const c = overview.data.counts;
    setServers(liveServers);
    const nextTaskSummary = taskResponse?.summary || taskSummary;
    setCounts({ servers: c.servers, runtimes: c.runtimes, primaryAgents: c.primary_agents, completedTasks: completedTaskCount(nextTaskSummary) });
    if (taskResponse) { setRecentTasks(taskResponse.data || []); setTaskSummary(nextTaskSummary); }
    setLastUpdated(new Date());
    return liveServers;
  }

  async function refreshOverview() {
    const [overview, taskResponse] = await Promise.all([
      getJson("/api/v1/overview"),
      getJson("/api/v1/automation/runs?limit=6").catch(() => null),
    ]);
    const c = overview.data.counts;
    const nextTaskSummary = taskResponse?.summary || taskSummary;
    setCounts({ servers: c.servers, runtimes: c.runtimes, primaryAgents: c.primary_agents, completedTasks: completedTaskCount(nextTaskSummary) });
    if (taskResponse) { setRecentTasks(taskResponse.data || []); setTaskSummary(nextTaskSummary); }
    setLastUpdated(new Date());
  }

  function openTaskCenter(runId) {
    setFocusedTask((current) => ({ runId, request: current.request + 1 }));
    setActiveNav("任务中心");
  }

  async function logout() {
    try { await getJson("/api/v1/auth/logout", { method: "POST", body: "{}" }); }
    finally { setCurrentUser(null); setAuthState("unauthenticated"); setLastUpdated(null); setDataSource(DEMO_ALLOWED ? "demo" : "api"); }
  }

  if (authState === "checking") return <div className="auth-loading"><ShieldChevron size={30} weight="fill" /><span>正在验证登录状态…</span></div>;
  if (authState === "unavailable") return <ServiceUnavailablePage />;
  if (authState === "unauthenticated") return <LoginPage requestJson={getJson} notice={loginNotice} onLogin={(user) => { setLoginNotice(""); setCurrentUser(user); setAuthState("authenticated"); }} />;

  const isDemo = dataSource === "demo";
  // Demo data is synthetic: never let the shell enable mutating actions from a
  // real session that happens to be paired with the fallback dataset.
  const permissions = new Set(isDemo ? [] : currentUser?.permissions || []);
  const navItems = permissions.has("manage_users") ? [...NAV_ITEMS, ["用户权限", ShieldChevron]] : NAV_ITEMS;
  const roleLabel = { admin: "管理员", operator: "操作员", viewer: "只读用户" }[currentUser?.role] || "演示模式";
  const freshness = isDemo ? { label: "演示数据", date: "非生产数据" } : formatFreshness(lastUpdated);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><ShieldChevron size={25} weight="fill" /></span><span>AgentsWatch</span></div>
        <nav aria-label="主导航">
          {navItems.map(([label, Icon]) => (
            <button key={label} className={activeNav === label ? "nav-item nav-item--active" : "nav-item"} onClick={() => setActiveNav(label)} title={label}>
              <Icon size={22} weight="regular" /><span>{label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <h1>管理驾驶舱</h1>
          <div className="environment-wrap">
            <button className="environment-button" onClick={() => setEnvironmentOpen((open) => !open)} aria-expanded={environmentOpen}>
              <HardDrives size={20} />{isDemo ? "演示模式" : "生产环境"}<CaretDown size={15} />
            </button>
            {environmentOpen && <div className="environment-menu"><button onClick={() => setEnvironmentOpen(false)}>{isDemo ? "演示模式" : "生产环境"} <span>当前</span></button><button disabled>测试环境 <span>待接入</span></button></div>}
          </div>
          <div className="freshness" data-source={dataSource}><Pulse size={17} />{freshness.label}<span className="date">{freshness.date}</span></div>
          {currentUser && <div className="current-user"><UserCircle size={22} /><span><strong>{currentUser.display_name}</strong><small>{roleLabel}</small></span><button className="api-key-action" onClick={() => setApiKeyDialogOpen(true)} title="API Key"><Key size={17} /></button><button className="password-action" onClick={() => setPasswordDialogOpen(true)} title="修改密码"><LockKey size={17} /></button><button className="logout-action" onClick={logout} title="退出登录"><SignOut size={17} /></button></div>}
        </header>

        <div className={activeNav === "总览" ? "content" : "content content--asset-page"}>
          {isDemo && <div className="demo-banner" role="status"><Database size={17} />当前展示的是合成演示数据，未连接管理 API；所有写操作均不可用。</div>}
          {!permissions.has("manage_assets") && authState === "authenticated" && <div className="permission-banner"><ShieldChevron size={17} />当前为只读权限；新增、修改、退役及任务执行由服务端禁止。</div>}
          {activeNav === "服务器" ? <ServerManagement servers={servers} runtimeMap={runtimeMap} total={counts.servers} dataSource={dataSource} requestJson={getJson} onServersChanged={refreshServers} onOpenTask={openTaskCenter} canManage={permissions.has("manage_assets")} canExecute={permissions.has("execute_automation")} /> : activeNav === "运行时" ? <RuntimeManagement runtimeMap={runtimeMap} servers={servers} agentMap={agentMap} total={counts.runtimes} dataSource={dataSource} requestJson={getJson} onOverviewChanged={refreshOverview} onOpenTask={openTaskCenter} canManage={permissions.has("manage_assets")} canExecute={permissions.has("execute_automation")} /> : activeNav === "智能体" ? <AgentManagement agentMap={agentMap} runtimeMap={runtimeMap} primaryTotal={counts.primaryAgents} dataSource={dataSource} requestJson={getJson} onOverviewChanged={refreshOverview} onOpenTask={openTaskCenter} canManage={permissions.has("manage_assets")} canExecute={permissions.has("execute_automation")} /> : activeNav === "组织管理" ? <OrganizationManagement requestJson={getJson} canManage={permissions.has("manage_assets")} /> : activeNav === "自动化库" ? <AutomationLibraryManagement dataSource={dataSource} requestJson={getJson} canManage={permissions.has("manage_automation_library")} /> : activeNav === "任务中心" ? <TaskCenterManagement dataSource={dataSource} requestJson={getJson} initialRunId={focusedTask.runId} focusRequest={focusedTask.request} canExecute={permissions.has("execute_automation")} /> : activeNav === "用户权限" ? <UserManagement requestJson={getJson} /> : activeNav === "管理分组" ? <GroupManagement servers={servers} agentMap={agentMap} runtimeMap={runtimeMap} dataSource={dataSource} requestJson={getJson} canManage={permissions.has("manage_assets")} /> : activeNav === "数据导入" ? <DataQualityManagement dataSource={dataSource} requestJson={getJson} canManage={permissions.has("manage_assets")} onImported={refreshOverview} /> : activeNav === "可观测接入" ? <ObservabilityIntegration dataSource={dataSource} requestJson={getJson} canManage={permissions.has("manage_assets")} /> : <OverviewDashboard counts={counts} servers={servers} selectedServerId={selectedServerId} selectedRuntimeId={selectedRuntime?.runtime_id || null} runtimes={runtimes} agents={agents} recentTasks={recentTasks} taskSummary={taskSummary} onChooseServer={chooseServer} onChooseRuntime={chooseRuntime} onNavigate={setActiveNav} onOpenTask={openTaskCenter} />}
        </div>
      </main>
      {passwordDialogOpen && <PasswordChangeDialog onClose={() => setPasswordDialogOpen(false)} onChanged={() => { setPasswordDialogOpen(false); setCurrentUser(null); setLoginNotice("密码已更新，请使用新密码重新登录。"); setAuthState("unauthenticated"); setDataSource("demo"); }} />}
      {apiKeyDialogOpen && <ApiKeyDialog requestJson={getJson} onClose={() => setApiKeyDialogOpen(false)} />}
    </div>
  );
}

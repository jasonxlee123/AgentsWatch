import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowSquareOut, ArrowsClockwise, CaretRight, CheckCircle, Clock, Copy, Cube,
  FloppyDisk, FolderOpen, Globe, HardDrives, Link, LockKey, MagnifyingGlass,
  Package, PencilSimple, Play, Plugs, Plus, Power, Pulse, Robot, Rows, ShieldCheck,
  TerminalWindow, Trash, Users, X, XCircle,
} from "@phosphor-icons/react";
import { AutomationExecutionDialog } from "./AutomationExecutionDialog.jsx";
import { displayRuntimeName } from "./serverUtils.js";
import { buildDemoRuntimeDetail, filterRuntimes, flattenRuntimeMap, HEALTH_STATUS_LABELS, RUNTIME_MANAGEMENT_DRIVER_LABELS, RUNTIME_STATUS_LABELS } from "./runtimeUtils.js";
import { buildDemoGroups, filterAssetsByGroup } from "./groupUtils.js";
import { operationsForScope } from "./serverOperationUtils.js";
import {
  buildRuntimeWebEndpointPayload, runtimeWebEndpointDefaults, WEB_ACCESS_LABELS,
  WEB_AUTH_LABELS, webEndpointTarget,
} from "./runtimeWebUtils.js";

const OPERATION_LABELS = {
  "runtime.collect_info": "采集信息",
  "runtime.refresh_status": "刷新状态",
  "runtime.start": "启动运行时",
  "runtime.stop": "停止运行时",
  "runtime.restart": "重启运行时",
};

function RuntimeStatus({ status = "active" }) {
  return <span className={`asset-status asset-status--${status}`}><i />{RUNTIME_STATUS_LABELS[status] || status}</span>;
}

function HealthStatus({ status = "unknown" }) {
  return <span className={`health-status health-status--${status}`}><i />{HEALTH_STATUS_LABELS[status] || status}</span>;
}

function RuntimeField({ icon: Icon, label, value, mono = false }) {
  return <div className="detail-field"><Icon size={18} weight="regular" /><div><span>{label}</span><strong className={mono ? "field-mono" : ""}>{value || "未配置"}</strong></div></div>;
}

function formatDiscoveryTime(value) {
  if (!value) return "尚未发现";
  return String(value).replace("T", " ").replace(/([+-]\d{2}:\d{2}|Z)$/, "").slice(0, 19);
}

function Dialog({ title, children, onClose, wide = false }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className={wide ? "management-dialog management-dialog--wide" : "management-dialog"} role="dialog" aria-modal="true" aria-label={title}><header><h3>{title}</h3><button onClick={onClose} aria-label="关闭"><X size={19} /></button></header>{children}</section></div>;
}

function Field({ label, children, wide = false }) {
  return <label className={wide ? "management-field management-field--wide" : "management-field"}><span>{label}</span>{children}</label>;
}

function RuntimeForm({ runtime, servers, families, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => ({
    name: runtime?.name || "",
    server_id: runtime?.server_id || servers[0]?.server_id || "",
    runtime_family_id: runtime?.runtime_family_id || families[0]?.runtime_family_id || "",
    deployment_mode: runtime?.deployment_mode || "baremetal",
    lifecycle_status: runtime?.lifecycle_status || "planned",
    bind_address: runtime?.bind_address || "127.0.0.1",
    endpoint_port: runtime?.endpoint_port || "",
    runtime_account: runtime?.runtime_account || "",
    config_path: runtime?.config_path || "",
    state_path: runtime?.state_path || "",
    command_path: runtime?.command_path || "",
    desired_version: runtime?.desired_version || "",
    container_name: runtime?.container_name || "",
    image_ref: runtime?.image_ref || "",
    management_driver: runtime?.management_driver === "mock" ? "none" : runtime?.management_driver || "none",
    management_target: runtime?.management_driver === "mock" ? "" : runtime?.management_target || "",
  }));
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = (event) => {
    event.preventDefault();
    const payload = { ...form };
    ["bind_address", "endpoint_port", "runtime_account", "config_path", "state_path", "command_path", "desired_version", "container_name", "image_ref", "management_target"].forEach((key) => { if (payload[key] === "") payload[key] = null; });
    if (payload.endpoint_port !== null) payload.endpoint_port = Number(payload.endpoint_port);
    if (payload.deployment_mode === "baremetal") {
      payload.container_name = null; payload.image_ref = null;
      if (payload.management_driver === "none") payload.management_target = null;
    } else {
      payload.management_driver = "none"; payload.management_target = null;
    }
    if (runtime) payload.row_version = runtime.row_version;
    onSave(payload);
  };
  const serverLocked = runtime && runtime.lifecycle_status !== "planned";
  return <form onSubmit={submit}><div className="management-form-grid">
    <Field label="运行时名称" wide><input required value={form.name} onChange={(event) => set("name", event.target.value)} /></Field>
    <Field label="运行时类型"><select required value={form.runtime_family_id} onChange={(event) => set("runtime_family_id", event.target.value)}>{families.map((item) => <option key={item.runtime_family_id} value={item.runtime_family_id}>{item.name}</option>)}</select></Field>
    <Field label="宿主服务器"><select required disabled={serverLocked} value={form.server_id} onChange={(event) => set("server_id", event.target.value)}>{servers.filter((item) => !["disabled", "retired"].includes(item.lifecycle_status)).map((item) => <option key={item.server_id} value={item.server_id}>{item.name}</option>)}</select>{serverLocked && <small>只有已规划运行时可以更换宿主服务器。</small>}</Field>
    <Field label="部署方式"><select value={form.deployment_mode} onChange={(event) => set("deployment_mode", event.target.value)}><option value="baremetal">裸金属</option><option value="container">容器（暂不提供操作）</option></select></Field>
    <Field label="生命周期"><select value={form.lifecycle_status} onChange={(event) => set("lifecycle_status", event.target.value)}>{Object.entries(RUNTIME_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label="运行账号"><input value={form.runtime_account} onChange={(event) => set("runtime_account", event.target.value)} /></Field>
    <Field label="期望版本"><input value={form.desired_version} onChange={(event) => set("desired_version", event.target.value)} /></Field>
    <Field label="绑定地址"><input value={form.bind_address} onChange={(event) => set("bind_address", event.target.value)} /></Field>
    <Field label="端口"><input type="number" min="1" max="65535" value={form.endpoint_port} onChange={(event) => set("endpoint_port", event.target.value)} /></Field>
    <Field label="配置路径"><input value={form.config_path} onChange={(event) => set("config_path", event.target.value)} /></Field>
    <Field label="状态路径"><input value={form.state_path} onChange={(event) => set("state_path", event.target.value)} /></Field>
    <Field label="运行命令绝对路径"><input value={form.command_path} onChange={(event) => set("command_path", event.target.value)} placeholder="例如 /home/runtime/.local/bin/openclaw" /></Field>
    {form.deployment_mode === "container" ? <><Field label="容器名称"><input required value={form.container_name} onChange={(event) => set("container_name", event.target.value)} /></Field><Field label="镜像"><input required value={form.image_ref} onChange={(event) => set("image_ref", event.target.value)} /></Field><div className="runtime-container-note">容器资产可以录入和查看，本阶段暂不提供状态刷新、启动、停止和重启。</div></> : <><Field label="管理方式"><select value={form.management_driver} onChange={(event) => set("management_driver", event.target.value)}><option value="none">暂不管理</option><option value="systemd">systemd 系统服务（Linux）</option><option value="systemd_user">systemd 用户服务（Linux）</option><option value="launchd">launchd 用户服务（macOS）</option></select></Field><Field label={form.management_driver === "launchd" ? "LaunchAgent Label" : "systemd 服务名"}><input disabled={form.management_driver === "none"} required={form.management_driver !== "none"} value={form.management_target} onChange={(event) => set("management_target", event.target.value)} placeholder={form.management_driver === "launchd" ? "例如 ai.openclaw.gateway" : "例如 openclaw-gateway.service"} /></Field></>}
  </div>{error && <div className="management-form-error">{error}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存"}</button></footer></form>;
}

function RuntimeWebEndpointForm({ runtime, endpoint, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => runtimeWebEndpointDefaults(runtime, endpoint));
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const direct = ["direct_url", "tailscale_serve"].includes(form.access_mode);
  const credentialReusable = endpoint?.credential_configured && endpoint?.auth_mode === form.auth_mode;
  const submit = (event) => {
    event.preventDefault();
    onSave(buildRuntimeWebEndpointPayload(form));
  };
  return <form onSubmit={submit} className="runtime-web-form"><div className="runtime-web-guidance"><ShieldCheck size={20} /><div><strong>运行时管理入口</strong><span>WebUI 将在浏览器新标签页顶层打开，不嵌入管理驾驶舱。当前裸金属环境推荐 SSH 临时隧道；已有 Tailnet 时推荐 Tailscale Serve。</span></div></div><div className="management-form-grid">
    <Field label="入口名称" wide><input required value={form.display_name} onChange={(event) => set("display_name", event.target.value)} /></Field>
    <Field label="访问通道"><select value={form.access_mode} onChange={(event) => set("access_mode", event.target.value)}><option value="local_ssh_tunnel">SSH 临时隧道（当前推荐）</option><option value="tailscale_serve">Tailscale Serve</option><option value="direct_url">私网直接地址</option></select></Field>
    <Field label="认证方式"><select value={form.auth_mode} onChange={(event) => set("auth_mode", event.target.value)}><option value="none">无需共享密钥</option><option value="token">Token</option><option value="password">密码</option><option value="username_password">账号和密码</option></select></Field>
    {direct ? <Field label={form.access_mode === "tailscale_serve" ? "Tailscale Serve 地址" : "私网 WebUI 地址"} wide><input required type="url" placeholder={form.access_mode === "tailscale_serve" ? "https://gateway.example.ts.net" : "http://192.0.2.12:18789"} value={form.direct_url} onChange={(event) => set("direct_url", event.target.value)} /><small>只填写协议、主机和可选端口；页面路径在下方单独填写。</small></Field> : <><Field label="协议"><select value={form.scheme} onChange={(event) => set("scheme", event.target.value)}><option value="http">HTTP</option><option value="https">HTTPS</option></select></Field><Field label="WebUI 端口"><input type="number" min="1" max="65535" value={form.port_override} onChange={(event) => set("port_override", event.target.value)} placeholder={String(runtime.endpoint_port || 18789)} /><small>留空使用运行时端口 {runtime.endpoint_port || "（未配置）"}</small></Field></>}
    <Field label="页面路径" wide><input required value={form.base_path} onChange={(event) => set("base_path", event.target.value)} placeholder="/ 或 /openclaw" /></Field>
    {form.auth_mode === "token" && <Field label="Token" wide><input type="password" required={!credentialReusable} value={form.token} onChange={(event) => set("token", event.target.value)} autoComplete="new-password" placeholder={credentialReusable ? "已加密保存；留空保持不变" : "输入 Gateway Token"} /></Field>}
    {form.auth_mode === "password" && <Field label="密码" wide><input type="password" required={!credentialReusable} value={form.password} onChange={(event) => set("password", event.target.value)} autoComplete="new-password" placeholder={credentialReusable ? "已加密保存；留空保持不变" : "输入 Gateway 密码"} /></Field>}
    {form.auth_mode === "username_password" && <><Field label="账号"><input required={!credentialReusable} value={form.username} onChange={(event) => set("username", event.target.value)} autoComplete="off" placeholder={credentialReusable ? "留空保持已保存账号" : "输入账号"} /></Field><Field label="密码"><input type="password" required={!credentialReusable} value={form.password} onChange={(event) => set("password", event.target.value)} autoComplete="new-password" placeholder={credentialReusable ? "留空保持已保存密码" : "输入密码"} /></Field></>}
    <label className="inline-check management-field--wide"><input type="checkbox" checked={form.enabled} onChange={(event) => set("enabled", event.target.checked)} />启用此 WebUI 入口</label>
  </div>{form.access_mode === "local_ssh_tunnel" && <div className="runtime-web-channel-note"><Link size={16} /><span>访问时复用宿主服务器的 SSH 连接，只在 AgentsWatch 本机 `127.0.0.1` 创建临时端口，默认 15 分钟后关闭。</span></div>}{form.access_mode === "tailscale_serve" && <div className="runtime-web-channel-note"><ShieldCheck size={16} /><span>Gateway 仍监听服务器回环地址，由 Tailscale Serve 提供 Tailnet 内 HTTPS 和身份。请勿使用 Funnel 暴露管理入口。</span></div>}{error && <div className="management-form-error">{error}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存配置"}</button></footer></form>;
}

function WebAccessResult({ access, onClose }) {
  const copy = async (value) => { await navigator.clipboard.writeText(value); };
  const credential = access.manual_credential;
  return <Dialog title="运行时 WebUI 已就绪" onClose={onClose}><div className="runtime-web-access-result"><CheckCircle size={28} weight="fill" /><div><strong>{access.opened ? "已在新标签页打开" : "浏览器拦截了新标签页"}</strong><span>访问入口将在 {formatDiscoveryTime(access.expires_at)} 失效。</span></div></div>{!access.opened && <a className="runtime-web-launch-link" href={access.launch_url} target="_blank" rel="noreferrer"><ArrowSquareOut size={17} />打开运行时 WebUI</a>}{credential && <div className="runtime-web-credential"><div><LockKey size={17} /><span><strong>请在 WebUI 登录页粘贴凭据</strong><small>密码不会拼入 URL，也不会由 OpenClaw 跨刷新保存。</small></span></div>{credential.username && <button type="button" onClick={() => copy(credential.username)}><span>账号：{credential.username}</span><Copy size={15} /></button>}{credential.token && <button type="button" onClick={() => copy(credential.token)}><span>复制 Token</span><Copy size={15} /></button>}{credential.password && <button type="button" onClick={() => copy(credential.password)}><span>复制密码</span><Copy size={15} /></button>}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onClose}>完成</button></footer></Dialog>;
}

function OperationStatus({ operation }) {
  const status = operation.host_status || operation.status;
  const running = ["queued", "running"].includes(status);
  const Icon = status === "succeeded" ? CheckCircle : running ? ArrowsClockwise : XCircle;
  return <span className={`operation-status operation-status--${status}`}><Icon size={14} className={running ? "spin" : ""} />{status === "succeeded" ? "成功" : running ? "执行中" : "失败"}</span>;
}

export function RuntimeManagement({ runtimeMap, servers, agentMap, total, dataSource, requestJson, onOverviewChanged, onOpenTask, canManage = true, canExecute = true }) {
  const demoRuntimes = useMemo(() => flattenRuntimeMap(runtimeMap, servers), [runtimeMap, servers]);
  const [runtimes, setRuntimes] = useState(demoRuntimes);
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("all");
  const [deploymentMode, setDeploymentMode] = useState("all");
  const [runtimeGroups, setRuntimeGroups] = useState([]);
  const [selectedGroupId, setSelectedGroupId] = useState("all");
  const [groupMemberIds, setGroupMemberIds] = useState(null);
  const [selectedId, setSelectedId] = useState(demoRuntimes[0]?.runtime_id || null);
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState("ready");
  const [referenceData, setReferenceData] = useState({ runtime_families: [] });
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [operationBusy, setOperationBusy] = useState(false);
  const [activeRun, setActiveRun] = useState(null);
  const [operations, setOperations] = useState([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [webAccessBusy, setWebAccessBusy] = useState(false);
  const [webAccessResult, setWebAccessResult] = useState(null);
  const catalogRequestId = useRef(0);

  const refreshRuntimes = useCallback(async () => {
    if (dataSource !== "api") { setRuntimes(demoRuntimes); return demoRuntimes; }
    const response = await requestJson("/api/v1/runtimes?limit=100");
    setRuntimes(response.data);
    return response.data;
  }, [dataSource, demoRuntimes, requestJson]);

  useEffect(() => { refreshRuntimes().catch(() => setRuntimes(demoRuntimes)); }, [refreshRuntimes, demoRuntimes]);
  useEffect(() => { if (!runtimes.some((runtime) => runtime.runtime_id === selectedId)) setSelectedId(runtimes[0]?.runtime_id || null); }, [runtimes, selectedId]);

  useEffect(() => {
    let cancelled = false;
    if (dataSource !== "api") {
      setRuntimeGroups(buildDemoGroups(servers, [], runtimes).filter((item) => item.resource_type === "runtime"));
      return undefined;
    }
    requestJson("/api/v1/groups?resource_type=runtime&lifecycle_status=active&limit=100")
      .then((response) => { if (!cancelled) setRuntimeGroups(response.data); })
      .catch(() => { if (!cancelled) setRuntimeGroups([]); });
    return () => { cancelled = true; };
  }, [dataSource, requestJson, runtimes, servers]);

  useEffect(() => {
    let cancelled = false;
    if (selectedGroupId === "all") { setGroupMemberIds(null); return undefined; }
    const group = runtimeGroups.find((item) => item.group_id === selectedGroupId);
    if (!group) { setSelectedGroupId("all"); return undefined; }
    if (dataSource !== "api") {
      setGroupMemberIds(new Set((group.members || []).map((item) => item.runtime_id)));
      return undefined;
    }
    requestJson(`/api/v1/groups/${selectedGroupId}`)
      .then((response) => { if (!cancelled) setGroupMemberIds(new Set(response.data.members.map((item) => item.runtime_id))); })
      .catch((error) => { if (!cancelled) { setGroupMemberIds(new Set()); setNotice(error.message); } });
    return () => { cancelled = true; };
  }, [dataSource, requestJson, runtimeGroups, selectedGroupId]);

  const loadDetail = useCallback(async (runtimeId = selectedId) => {
    const runtime = runtimes.find((item) => item.runtime_id === runtimeId);
    if (!runtime) { setDetail(null); return; }
    if (dataSource !== "api") { setDetail(buildDemoRuntimeDetail(runtime, agentMap[runtime.runtime_id])); setDetailState("ready"); return; }
    setDetailState("loading");
    try { setDetail((await requestJson(`/api/v1/runtimes/${runtime.runtime_id}`)).data); setDetailState("ready"); }
    catch { setDetail(buildDemoRuntimeDetail(runtime, agentMap[runtime.runtime_id])); setDetailState("fallback"); }
  }, [agentMap, dataSource, requestJson, runtimes, selectedId]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  const families = useMemo(() => [...new Set(runtimes.map((runtime) => runtime.family_code).filter(Boolean))].sort(), [runtimes]);
  const visibleRuntimes = useMemo(
    () => filterAssetsByGroup(filterRuntimes(runtimes, query, family, deploymentMode), groupMemberIds, "runtime_id"),
    [deploymentMode, family, groupMemberIds, query, runtimes],
  );
  const selectableVisible = visibleRuntimes.filter((item) => item.deployment_mode === "baremetal" && !["disabled", "retired"].includes(item.lifecycle_status));
  const snapshot = detail?.latest_snapshot;
  const recentSnapshots = detail?.recent_snapshots || (snapshot ? [snapshot] : []);
  const deployments = detail?.active_deployments || [];
  const operationTargets = checkedIds.size ? [...checkedIds] : selectedId ? [selectedId] : [];
  const targetRuntimes = operationTargets.map((runtimeId) => runtimes.find((item) => item.runtime_id === runtimeId)).filter(Boolean);
  const canCollect = targetRuntimes.length === operationTargets.length && targetRuntimes.length > 0 && targetRuntimes.every((item) => item.deployment_mode === "baremetal" && !["disabled", "retired"].includes(item.lifecycle_status));
  const canLifecycleOperate = canCollect && targetRuntimes.every((item) => item.management_driver !== "none");
  const operationUnavailableReason = useCallback((operation) => {
    if (!canCollect) return "当前目标不支持运行时任务";
    if (operation.code !== "runtime.collect_info" && !canLifecycleOperate) return "当前目标未配置运行时管理驱动";
    return "";
  }, [canCollect, canLifecycleOperate]);

  const openDialog = async (type) => {
    setFormError("");
    if (type === "task" && !canExecute) { setNotice("当前账号没有执行自动化任务的权限。"); return; }
    if (type !== "task" && !canManage) { setNotice("当前账号为只读权限，不能修改运行时。"); return; }
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后执行写操作。"); return; }
    if (type === "task") {
      const requestId = ++catalogRequestId.current;
      setDialog(type);
      setCatalogLoading(true);
      setOperations([]);
      try {
        const response = await requestJson("/api/v1/automation/operations");
        if (requestId !== catalogRequestId.current) return;
        setOperations(operationsForScope(response.data.operations, "runtime"));
      } catch (error) {
        if (requestId === catalogRequestId.current) setFormError(error.message);
      } finally {
        if (requestId === catalogRequestId.current) setCatalogLoading(false);
      }
      return;
    }
    if (["create", "edit"].includes(type) && !referenceData.runtime_families.length) {
      try { setReferenceData((await requestJson("/api/v1/reference-data")).data); }
      catch (error) { setNotice(error.message); return; }
    }
    setDialog(type);
  };

  const saveRuntime = async (payload) => {
    setSaving(true); setFormError("");
    try {
      const editing = dialog === "edit";
      const response = await requestJson(editing ? `/api/v1/runtimes/${detail.runtime_id}` : "/api/v1/runtimes", { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
      setSelectedId(response.data.runtime_id); setDetail(response.data); setDialog(null);
      await Promise.all([refreshRuntimes(), onOverviewChanged?.()]);
      setNotice(editing ? "运行时已更新" : "运行时已创建");
    } catch (error) { setFormError(error.message); } finally { setSaving(false); }
  };

  const saveWebEndpoint = async (payload) => {
    setSaving(true); setFormError("");
    try {
      await requestJson(`/api/v1/runtimes/${detail.runtime_id}/web-endpoint`, { method: "PUT", body: JSON.stringify(payload) });
      await Promise.all([loadDetail(detail.runtime_id), refreshRuntimes()]);
      setDialog(null); setNotice("WebUI 访问配置已保存");
    } catch (error) { setFormError(error.message); } finally { setSaving(false); }
  };

  const openWebUi = async () => {
    if (!detail?.web_endpoint?.enabled || webAccessBusy) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) {
      popup.opener = null;
      popup.document.title = "正在打开运行时 WebUI";
      popup.document.body.textContent = "正在建立安全访问通道…";
    }
    setWebAccessBusy(true); setNotice("");
    try {
      const access = (await requestJson(`/api/v1/runtimes/${detail.runtime_id}/web-access-sessions`, { method: "POST", body: JSON.stringify({ requested_by: "local-user" }) })).data;
      if (popup) popup.location.replace(access.launch_url);
      if (!popup || access.manual_credential) setWebAccessResult({ ...access, opened: Boolean(popup) });
      setNotice(popup ? "运行时 WebUI 已在新标签页打开" : "访问通道已建立，请点击弹窗中的链接打开");
      await Promise.all([loadDetail(detail.runtime_id), refreshRuntimes()]);
    } catch (error) {
      popup?.close(); setNotice(error.message);
    } finally { setWebAccessBusy(false); }
  };

  const retire = async () => {
    if (!detail || !window.confirm(`确认退役运行时“${displayRuntimeName(detail.name)}”？`)) return;
    try {
      const response = await requestJson(`/api/v1/runtimes/${detail.runtime_id}?row_version=${detail.row_version}`, { method: "DELETE", confirmed: true });
      setDetail(response.data); await Promise.all([refreshRuntimes(), onOverviewChanged?.()]); setNotice("运行时已退役");
    } catch (error) { setNotice(error.message); }
  };

  const operationName = (operationCode) => operations.find((item) => item.code === operationCode)?.name || OPERATION_LABELS[operationCode] || operationCode;

  const runOperation = async (operationCode, parameters = {}, confirmed = false) => {
    const allowed = operationCode === "runtime.collect_info" ? canCollect : canLifecycleOperate;
    if (!allowed || operationBusy) return;
    setOperationBusy(true); setNotice("");
    try {
      const run = (await requestJson("/api/v1/automation/runs", { method: "POST", body: JSON.stringify({ operation_code: operationCode, runtime_ids: operationTargets, parameters }), confirmed })).data;
      setDialog(null); setActiveRun(run);
      setNotice(`${operationName(operationCode)}已下发，可前往任务中心查看 Ansible 执行过程。`);
    } catch (error) { setFormError(error.message); } finally { setOperationBusy(false); }
  };

  useEffect(() => {
    if (dataSource !== "api" || !activeRun?.run_id || !["queued", "running"].includes(activeRun.status)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const run = (await requestJson(`/api/v1/automation/runs/${activeRun.run_id}`)).data;
        if (cancelled) return;
        setActiveRun(run);
        if (!["queued", "running"].includes(run.status)) {
          setNotice(`${operationName(run.operation_code)}执行结束：成功 ${run.succeeded_count}，失败 ${run.failed_count}`);
          await Promise.all([refreshRuntimes(), loadDetail()]);
        }
      } catch (error) {
        if (!cancelled) setNotice(`任务状态刷新失败：${error.message}`);
      }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeRun?.run_id, activeRun?.status, dataSource, loadDetail, refreshRuntimes, requestJson]);

  return <section className="asset-page runtime-management-page" aria-labelledby="runtime-page-title">
    <div className="asset-page-heading"><div><span className="eyebrow">运行时管理</span><h2 id="runtime-page-title">运行时</h2><p>管理运行时资产，并通过标准 Playbook 操作裸金属服务。</p></div><div className="server-heading-actions">{canManage && <button className="secondary-action" onClick={() => openDialog("create")}><Plus size={17} />新增运行时</button>}<div className="asset-page-total asset-page-total--runtime"><Cube size={25} /><div><strong>{dataSource === "api" ? runtimes.length : total}</strong><span>个运行时</span></div></div></div></div>
    {notice && <div className="management-notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}
    <div className="asset-toolbar runtime-toolbar"><label className="asset-search"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索运行时、服务器或 IP" /></label><label className="asset-filter"><span>类型</span><select value={family} onChange={(event) => setFamily(event.target.value)}><option value="all">全部类型</option>{families.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label className="asset-filter"><span>部署</span><select value={deploymentMode} onChange={(event) => setDeploymentMode(event.target.value)}><option value="all">全部方式</option><option value="baremetal">裸金属</option><option value="container">容器</option></select></label><label className="asset-filter"><span>管理分组</span><select value={selectedGroupId} onChange={(event) => { setSelectedGroupId(event.target.value); setCheckedIds(new Set()); }}><option value="all">全部分组</option>{runtimeGroups.map((group) => <option key={group.group_id} value={group.group_id}>{group.name}</option>)}</select></label>{canExecute && <div className="server-operation-bar runtime-operation-bar"><span>执行目标 <strong>{operationTargets.length}</strong> 个</span><button className="server-task-action" disabled={!canCollect || operationBusy} onClick={() => openDialog("task")}><Play size={15} />执行任务</button></div>}</div>
    {activeRun && <div className="active-operation-strip"><OperationStatus operation={activeRun} /><strong>{operationName(activeRun.operation_code)}</strong><span>{activeRun.target_count} 个目标</span><span>成功 {activeRun.succeeded_count}</span><span>失败 {activeRun.failed_count}</span><button onClick={() => onOpenTask?.(activeRun.run_id)}>任务中心<CaretRight size={13} /></button></div>}
    <div className="asset-layout"><div className="asset-list-card"><div className="runtime-list-head runtime-management-list"><span><input type="checkbox" disabled={!canExecute} aria-label="选择可采集运行时" checked={selectableVisible.length > 0 && selectableVisible.every((item) => checkedIds.has(item.runtime_id))} onChange={(event) => setCheckedIds(event.target.checked ? new Set(selectableVisible.map((item) => item.runtime_id)) : new Set())} /></span><span>运行时</span><span>生命周期</span><span>健康度</span><span>部署方式</span><span>宿主服务器</span><span>主智能体</span><span /></div><div className="asset-list-body">
      {visibleRuntimes.map((runtime) => { const selectable = runtime.deployment_mode === "baremetal" && !["disabled", "retired"].includes(runtime.lifecycle_status); return <div className={selectedId === runtime.runtime_id ? "runtime-list-row runtime-management-list runtime-list-row--selected" : "runtime-list-row runtime-management-list"} key={runtime.runtime_id} onClick={() => setSelectedId(runtime.runtime_id)}><input type="checkbox" disabled={!canExecute || !selectable} checked={checkedIds.has(runtime.runtime_id)} onClick={(event) => event.stopPropagation()} onChange={(event) => { const next = new Set(checkedIds); event.target.checked ? next.add(runtime.runtime_id) : next.delete(runtime.runtime_id); setCheckedIds(next); }} /><span className="server-identity"><Cube size={19} /><span><strong>{displayRuntimeName(runtime.name)}</strong><small>{runtime.family_code || "未分类"}</small></span></span><RuntimeStatus status={runtime.lifecycle_status || "active"} /><HealthStatus status={runtime.observed_status || "unknown"} /><span className="deployment-mode"><Package size={15} />{runtime.deployment_mode === "container" ? "容器" : "裸金属"}</span><span className="cloud-summary"><strong>{runtime.server_name || "未关联"}</strong><small>{runtime.server_internal_ip || "未配置 IP"}</small></span><span className="agent-count"><Users size={15} />{runtime.active_primary_agents || 0}</span><CaretRight size={16} /></div>; })}
      {!visibleRuntimes.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配运行时</strong><span>请调整搜索词或筛选条件。</span></div>}
    </div></div><aside className="asset-detail-card runtime-detail-panel" aria-label="运行时详情">
      {detailState === "loading" ? <div className="detail-loading"><ArrowsClockwise size={28} className="spin" /><span>正在读取运行时详情</span></div> : detail ? <><div className="detail-header"><div className="detail-title"><span className="detail-icon detail-icon--runtime"><Cube size={22} /></span><div><h3>{displayRuntimeName(detail.name)}</h3><span>{detail.runtime_id}</span></div></div><RuntimeStatus status={detail.lifecycle_status} /></div>{canManage && <div className="server-detail-actions"><button onClick={() => openDialog("edit")} disabled={detail.lifecycle_status === "retired"}><PencilSimple size={15} />编辑</button><button onClick={() => openDialog("web-endpoint")} disabled={detail.lifecycle_status === "retired"}><Plugs size={15} />WebUI 配置</button><button className="runtime-web-open-action" onClick={openWebUi} disabled={!detail.web_endpoint?.enabled || webAccessBusy || ["disabled", "retired"].includes(detail.lifecycle_status)}><ArrowSquareOut size={15} />{webAccessBusy ? "建立通道" : "访问 WebUI"}</button>{detail.deployment_mode === "container" && <span className="runtime-readonly-tag">容器操作暂未开放</span>}<button className="danger-action" onClick={retire} disabled={detail.lifecycle_status === "retired" || deployments.length > 0}><Trash size={15} />退役</button></div>}{detailState === "fallback" && <div className="detail-notice">本机 API 详情暂不可用，当前展示模拟明细。</div>}<div className="runtime-health-strip"><HealthStatus status={detail.observed_status || "unknown"} /><span><Pulse size={15} />最后发现 {formatDiscoveryTime(detail.last_discovered_at || snapshot?.discovered_at)}</span></div>
      <div className="detail-section"><h4>部署信息</h4><div className="detail-field-grid"><RuntimeField icon={Rows} label="运行时类型" value={detail.family_code} /><RuntimeField icon={Package} label="部署方式" value={detail.deployment_mode === "container" ? "容器" : "裸金属"} /><RuntimeField icon={HardDrives} label="宿主服务器" value={detail.server_name} /><RuntimeField icon={Globe} label="服务器 IP" value={detail.server_internal_ip} mono /><RuntimeField icon={TerminalWindow} label="运行账号" value={detail.runtime_account} /><RuntimeField icon={Plugs} label="端点" value={`${detail.bind_address || "127.0.0.1"}:${detail.endpoint_port || 18789}`} mono /></div></div>
      <div className="detail-section"><h4>管理方式</h4>{detail.deployment_mode === "container" ? <div className="runtime-container-note runtime-container-note--detail">容器选项仅用于资产记录，本阶段不执行容器操作。</div> : <div className="detail-field-grid"><RuntimeField icon={Power} label="管理驱动" value={RUNTIME_MANAGEMENT_DRIVER_LABELS[detail.management_driver] || detail.management_driver} /><RuntimeField icon={TerminalWindow} label="服务名" value={detail.management_target} /><RuntimeField icon={Plugs} label="服务器连接" value={detail.server_connection_status === "reachable" ? "正常" : detail.server_connection_status || "未配置"} /><RuntimeField icon={Clock} label="最后操作" value={formatDiscoveryTime(detail.last_operation_at)} /></div>}</div>
      <div className="detail-section runtime-web-summary"><div className="detail-section-title"><h4>WebUI 访问</h4><span className={detail.web_endpoint?.enabled ? "runtime-web-enabled" : ""}>{detail.web_endpoint?.enabled ? "已启用" : "未配置"}</span></div>{detail.web_endpoint ? <><div className="detail-field-grid"><RuntimeField icon={Link} label="访问通道" value={WEB_ACCESS_LABELS[detail.web_endpoint.access_mode] || detail.web_endpoint.access_mode} /><RuntimeField icon={LockKey} label="认证方式" value={WEB_AUTH_LABELS[detail.web_endpoint.auth_mode] || detail.web_endpoint.auth_mode} /><RuntimeField icon={Globe} label="访问目标" value={webEndpointTarget(detail.web_endpoint, detail.endpoint_port)} mono /><RuntimeField icon={ShieldCheck} label="凭据" value={detail.web_endpoint.credential_configured ? "已加密保存" : "无需保存"} /></div><div className="runtime-web-session-history">{detail.recent_web_access_sessions?.slice(0, 3).map((session) => <span key={session.access_session_id} className={`runtime-web-session runtime-web-session--${session.status}`}><i />{session.status === "active" ? "访问中" : session.status === "closed" ? "已关闭" : session.status === "expired" ? "已过期" : session.status === "failed" ? "失败" : "建立中"}<small>{formatDiscoveryTime(session.created_at)}</small></span>)}{!detail.recent_web_access_sessions?.length && <small>尚无访问记录</small>}</div></> : canManage ? <button className="runtime-web-config-empty" onClick={() => openDialog("web-endpoint")}><Plus size={15} />配置运行时 WebUI 入口</button> : <span className="detail-empty-line">尚未配置 WebUI 入口</span>}</div>
      {detail.deployment_mode === "container" && <div className="detail-section"><h4>容器信息</h4><div className="detail-field-grid"><RuntimeField icon={Package} label="容器名称" value={detail.container_name} /><RuntimeField icon={Rows} label="镜像" value={detail.image_ref} /></div></div>}
      <div className="detail-section"><h4>路径与版本</h4><div className="runtime-paths"><div><FolderOpen size={16} /><span><small>运行命令</small><strong>{detail.command_path || "自动发现"}</strong></span></div><div><FolderOpen size={16} /><span><small>配置路径</small><strong>{detail.config_path || "未配置"}</strong></span></div><div><FolderOpen size={16} /><span><small>状态路径</small><strong>{detail.state_path || "未配置"}</strong></span></div></div><div className="version-compare"><div><span>计划版本</span><strong>{detail.desired_version || "未设置"}</strong></div><CaretRight size={16} /><div><span>实际版本</span><strong>{snapshot?.actual_version || "未发现"}</strong></div></div></div>
      <div className="detail-section"><div className="detail-section-title"><h4>发现快照</h4><span>{recentSnapshots.length}</span></div>{snapshot ? <><div className="snapshot-grid"><div><Clock size={16} /><span>发现时间<strong>{formatDiscoveryTime(snapshot.discovered_at)}</strong></span></div><div><Robot size={16} /><span>观测智能体<strong>{snapshot.observed_agent_count == null ? "未采集" : `${snapshot.observed_agent_count} 个`}</strong></span></div><div><Pulse size={16} /><span>数量置信度<strong>{snapshot.observed_agent_count == null ? "不适用" : snapshot.agent_count_confidence || "unknown"}</strong></span></div></div><div className="runtime-snapshot-history">{recentSnapshots.slice(0, 3).map((item) => <span key={item.snapshot_id}><strong>{item.actual_version || "版本未知"}</strong><small>{formatDiscoveryTime(item.discovered_at)}</small></span>)}</div></> : <span className="detail-empty-line">尚无发现快照</span>}</div>
      <div className="detail-section"><div className="detail-section-title"><h4>活动主智能体</h4><span>{deployments.length}</span></div><div className="runtime-agent-list">{deployments.map((deployment) => <div key={deployment.agent_deployment_id}><span className="runtime-agent-icon"><Robot size={16} /></span><span><strong>{deployment.agent_name}</strong><small>{deployment.service_archetype || "未分类"} · {deployment.agent_id}</small></span></div>)}{!deployments.length && <span className="detail-empty-line">当前没有活动主智能体部署</span>}</div></div>
      <div className="detail-section"><div className="detail-section-title"><h4>管理分组</h4><span>{detail.groups?.length || 0}</span></div><div className="group-tags">{detail.groups?.map((group) => <span key={group.group_id}>{group.name}</span>)}{!detail.groups?.length && <span className="group-tag--empty">尚未加入管理分组</span>}</div></div>
      <div className="detail-section"><div className="detail-section-title"><h4>最近操作</h4><span>{detail.recent_operations?.length || 0}</span></div><div className="operation-history">{detail.recent_operations?.slice(0, 5).map((operation) => <button type="button" key={operation.run_id} onClick={() => onOpenTask?.(operation.run_id)}><OperationStatus operation={operation} /><span><strong>{operationName(operation.operation_code)}</strong><small>{operation.error_message || formatDiscoveryTime(operation.created_at)}</small></span><CaretRight size={13} /></button>)}{!detail.recent_operations?.length && <span className="detail-empty-line">暂无操作记录</span>}</div></div>
      </> : <div className="detail-loading"><Cube size={28} /><span>选择一个运行时查看详情</span></div>}
    </aside></div>
    {dialog === "create" && <Dialog title="新增运行时" onClose={() => setDialog(null)}><RuntimeForm servers={servers} families={referenceData.runtime_families} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveRuntime} /></Dialog>}
    {dialog === "edit" && detail && <Dialog title="编辑运行时" onClose={() => setDialog(null)}><RuntimeForm runtime={detail} servers={servers} families={referenceData.runtime_families} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveRuntime} /></Dialog>}
    {dialog === "task" && <Dialog wide title="执行运行时任务" onClose={() => !operationBusy && setDialog(null)}><AutomationExecutionDialog operations={operations} operationUnavailableReason={operationUnavailableReason} loading={catalogLoading} targets={targetRuntimes} targetId={(runtime) => runtime.runtime_id} targetName={(runtime) => displayRuntimeName(runtime.name)} targetUnit="个运行时" emptyMessage="当前没有运行时任务" submitting={operationBusy} error={formError} onCancel={() => setDialog(null)} onSubmit={(operation, parameters) => runOperation(operation.code, parameters, operation.risk_level !== "low")} /></Dialog>}
    {dialog === "web-endpoint" && detail && <Dialog title="配置运行时 WebUI" onClose={() => setDialog(null)}><RuntimeWebEndpointForm key={`${detail.runtime_id}:${detail.web_endpoint?.row_version || 0}`} runtime={detail} endpoint={detail.web_endpoint} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveWebEndpoint} /></Dialog>}
    {webAccessResult && <WebAccessResult access={webAccessResult} onClose={() => setWebAccessResult(null)} />}
  </section>;
}

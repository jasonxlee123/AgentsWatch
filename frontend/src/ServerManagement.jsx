import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  CaretRight,
  CheckCircle,
  Cloud,
  Cpu,
  Cube,
  Desktop,
  FloppyDisk,
  HardDrive,
  HardDrives,
  IdentificationCard,
  MagnifyingGlass,
  Memory,
  PencilSimple,
  Play,
  Plug,
  Plus,
  Rows,
  Tag,
  Trash,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { buildDemoServerDetail, displayRuntimeName, filterServers, formatServerConfig, serverConnectionDefaults, SERVER_STATUS_LABELS } from "./serverUtils.js";
import { AutomationExecutionDialog } from "./AutomationExecutionDialog.jsx";
import { buildDemoGroups, filterAssetsByGroup } from "./groupUtils.js";
import { operationsForScope } from "./serverOperationUtils.js";

const OPERATION_LABELS = {
  "server.bootstrap_ssh": "首次纳管服务器",
  "server.check_connection": "检查连接",
  "server.collect_facts": "采集信息",
  "server.discover_runtimes": "发现运行时",
};

function StatusBadge({ status = "active" }) {
  return <span className={`asset-status asset-status--${status}`}><i />{SERVER_STATUS_LABELS[status] || status}</span>;
}

function DetailField({ icon: Icon, label, value }) {
  return <div className="detail-field"><Icon size={18} weight="regular" /><div><span>{label}</span><strong>{value || "未配置"}</strong></div></div>;
}

function Dialog({ title, children, onClose, wide = false }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className={wide ? "management-dialog management-dialog--wide" : "management-dialog"} role="dialog" aria-modal="true" aria-label={title}>
      <header><h3>{title}</h3><button onClick={onClose} aria-label="关闭"><X size={19} /></button></header>
      {children}
    </section>
  </div>;
}

function Field({ label, children, wide = false }) {
  return <label className={wide ? "management-field management-field--wide" : "management-field"}><span>{label}</span>{children}</label>;
}

function ServerForm({ server, referenceData, serverGroups, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => ({
    name: server?.name || "",
    environment_id: server?.environment_id || referenceData.environments?.[0]?.environment_id || "",
    cloud_provider_id: server?.cloud_provider_id || referenceData.cloud_providers?.find((item) => item.provider_code === server?.provider_code)?.cloud_provider_id || referenceData.cloud_providers?.[0]?.cloud_provider_id || "",
    provider_instance_id: server?.provider_instance_id || "",
    lifecycle_status: server?.lifecycle_status || "planned",
    cpu_cores: server?.cpu_cores || "",
    memory_gib: server?.memory_gib || "",
    disk_gib: server?.disk_gib || "",
    internal_ip: server?.internal_ip || "",
    public_ip: server?.public_ip || "",
    os_name: server?.os_name || "",
    os_version: server?.os_version || "",
    architecture: server?.architecture || "",
    work_account: server?.work_account || "",
    group_ids: new Set((server?.groups || []).map((item) => item.group_id)),
  }));
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = (event) => {
    event.preventDefault();
    const optional = ["cloud_provider_id", "provider_instance_id", "cpu_cores", "memory_gib", "disk_gib", "internal_ip", "public_ip", "os_name", "os_version", "architecture", "work_account"];
    const payload = { ...form, group_ids: [...form.group_ids] };
    optional.forEach((key) => { if (payload[key] === "") payload[key] = null; });
    ["cpu_cores", "memory_gib", "disk_gib"].forEach((key) => { if (payload[key] !== null) payload[key] = Number(payload[key]); });
    if (server) payload.row_version = server.row_version;
    onSave(payload);
  };
  return <form onSubmit={submit}>
    <div className="management-form-grid">
      <Field label="服务器名称" wide><input required value={form.name} onChange={(event) => set("name", event.target.value)} /></Field>
      <Field label="环境"><select required value={form.environment_id} onChange={(event) => set("environment_id", event.target.value)}>{referenceData.environments?.map((item) => <option key={item.environment_id} value={item.environment_id}>{item.name}</option>)}</select></Field>
      <Field label="云"><select value={form.cloud_provider_id} onChange={(event) => set("cloud_provider_id", event.target.value)}><option value="">未指定</option>{referenceData.cloud_providers?.map((item) => <option key={item.cloud_provider_id} value={item.cloud_provider_id}>{item.name}</option>)}</select></Field>
      <Field label="云实例 ID"><input value={form.provider_instance_id} onChange={(event) => set("provider_instance_id", event.target.value)} /></Field>
      <Field label="生命周期"><select value={form.lifecycle_status} onChange={(event) => set("lifecycle_status", event.target.value)}>{Object.entries(SERVER_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      <Field label="CPU（核）"><input type="number" min="0.01" step="0.01" value={form.cpu_cores} onChange={(event) => set("cpu_cores", event.target.value)} /></Field>
      <Field label="内存（GiB）"><input type="number" min="0.01" step="0.01" value={form.memory_gib} onChange={(event) => set("memory_gib", event.target.value)} /></Field>
      <Field label="磁盘（GiB）"><input type="number" min="0.01" step="0.01" value={form.disk_gib} onChange={(event) => set("disk_gib", event.target.value)} /></Field>
      <Field label="内网 IP"><input value={form.internal_ip} onChange={(event) => set("internal_ip", event.target.value)} /></Field>
      <Field label="公网 IP"><input value={form.public_ip} onChange={(event) => set("public_ip", event.target.value)} /></Field>
      <Field label="操作系统"><input value={form.os_name} onChange={(event) => set("os_name", event.target.value)} /></Field>
      <Field label="系统版本"><input value={form.os_version} onChange={(event) => set("os_version", event.target.value)} /></Field>
      <Field label="架构"><input value={form.architecture} onChange={(event) => set("architecture", event.target.value)} /></Field>
      <Field label="工作账号"><input value={form.work_account} onChange={(event) => set("work_account", event.target.value)} /></Field>
      <Field label="服务器管理分组" wide><div className="management-check-grid">{serverGroups.map((group) => <label key={group.group_id}><input type="checkbox" checked={form.group_ids.has(group.group_id)} onChange={(event) => { const next = new Set(form.group_ids); event.target.checked ? next.add(group.group_id) : next.delete(group.group_id); set("group_ids", next); }} />{group.name}</label>)}{!serverGroups.length && <span>暂无服务器管理分组</span>}</div></Field>
    </div>
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存"}</button></footer>
  </form>;
}

function ConnectionForm({ server, connection, saving, error, onCancel, onSave, onScanHostKeys }) {
  const [form, setForm] = useState(() => ({
    connection_type: connection?.connection_type || "ssh",
    ...serverConnectionDefaults(server, connection),
    port: connection?.port || 22,
    credential_ref: connection?.credential_ref || "agentswatch-managed",
    ssh_password: "",
    ssh_host_keys: connection?.ssh_host_keys || [],
    use_become: connection?.use_become || false,
    row_version: connection?.row_version,
  }));
  const [mode, setMode] = useState(connection?.password_configured === false ? "existing_key" : "password_bootstrap");
  const [fingerprints, setFingerprints] = useState(connection?.ssh_host_key_fingerprints || []);
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(Boolean(connection?.ssh_host_keys?.length));
  const [forceBootstrap, setForceBootstrap] = useState(false);
  const [scanning, setScanning] = useState(false);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const setEndpoint = (name, value) => {
    setForm((current) => ({ ...current, [name]: value, ssh_host_keys: [] }));
    setFingerprints([]); setFingerprintConfirmed(false);
  };
  const scan = async () => {
    setScanning(true);
    try {
      const result = await onScanHostKeys(form.host, Number(form.port));
      set("ssh_host_keys", result.host_keys);
      setFingerprints(result.fingerprints);
      setFingerprintConfirmed(false);
    } catch (_) {
      // The parent renders the API error inside this dialog.
    } finally { setScanning(false); }
  };
  const submit = (event) => {
    event.preventDefault();
    const passwordMode = form.connection_type === "ssh" && mode === "password_bootstrap";
    if (passwordMode && !fingerprintConfirmed) return;
    const payload = {
      ...form,
      port: form.connection_type === "ssh" ? Number(form.port) : null,
      bootstrap_requested: passwordMode && (Boolean(form.ssh_password) || connection?.bootstrap_status !== "succeeded" || forceBootstrap),
    };
    if (!form.ssh_password) delete payload.ssh_password;
    if (!passwordMode) {
      delete payload.ssh_password;
      payload.ssh_host_keys = form.ssh_host_keys.length ? form.ssh_host_keys : null;
    }
    onSave(payload);
  };
  return <form onSubmit={submit}>
    <div className="management-form-grid">
      <Field label="连接类型"><select value={form.connection_type} onChange={(event) => set("connection_type", event.target.value)}><option value="ssh">SSH</option><option value="local">本机（仅测试）</option></select></Field>
      <Field label="管理地址"><input required value={form.host} onChange={(event) => setEndpoint("host", event.target.value)} placeholder="IP 或主机名" /></Field>
      {form.connection_type === "ssh" && <>
        <Field label="接入方式" wide><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="password_bootstrap">首次用密码，纳管后用公钥</option><option value="existing_key">服务器已配置平台公钥</option></select></Field>
        <Field label="SSH 端口"><input required type="number" min="1" max="65535" value={form.port} onChange={(event) => setEndpoint("port", event.target.value)} /></Field>
        <Field label="SSH 用户"><input required value={form.username} onChange={(event) => set("username", event.target.value)} /></Field>
        {mode === "password_bootstrap" && <Field label="SSH 登录密码" wide><input required={!connection?.password_configured} type="password" autoComplete="new-password" value={form.ssh_password} onChange={(event) => set("ssh_password", event.target.value)} placeholder={connection?.password_configured ? "已加密保存；留空则继续使用原密码" : "首次登录服务器使用，平台加密保存"} /><small>密码仅用于首次纳管或显式重新纳管；公钥复验成功后，日常任务不会携带密码。</small></Field>}
        {mode === "password_bootstrap" && connection?.password_configured && connection?.bootstrap_status === "succeeded" && <Field label="重新纳管" wide><label className="inline-check"><input type="checkbox" checked={forceBootstrap} onChange={(event) => setForceBootstrap(event.target.checked)} />使用已保存密码重新安装并复验平台公钥</label></Field>}
        <Field label="平台公钥凭据" wide><input required value={form.credential_ref} onChange={(event) => set("credential_ref", event.target.value)} placeholder="例如 agentswatch-managed" /><small>填写 AGENTSWATCH_CREDENTIAL_FILE_MAP 中的平台私钥别名；系统会从私钥导出公钥并安装到服务器。</small></Field>
        {mode === "password_bootstrap" && <Field label="SSH 主机指纹" wide><div className="ssh-fingerprint-panel"><button type="button" disabled={scanning || !form.host || !form.port} onClick={scan}><ArrowsClockwise size={15} className={scanning ? "spin" : ""} />{scanning ? "正在扫描" : "扫描主机指纹"}</button>{fingerprints.length > 0 && <><div>{fingerprints.map((item) => <code key={item}>{item}</code>)}</div><label className="inline-check"><input type="checkbox" checked={fingerprintConfirmed} onChange={(event) => setFingerprintConfirmed(event.target.checked)} />我已通过可信渠道核对并确认以上指纹</label></>}</div></Field>}
        <Field label="提权" wide><label className="inline-check"><input type="checkbox" checked={form.use_become} onChange={(event) => set("use_become", event.target.checked)} />缺少 Python 时允许使用同一密码 sudo 安装</label></Field>
      </>}
    </div>
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving || (form.connection_type === "ssh" && mode === "password_bootstrap" && !fingerprintConfirmed)}><FloppyDisk size={16} />{saving ? "处理中" : mode === "password_bootstrap" ? "保存并开始纳管" : "保存连接"}</button></footer>
  </form>;
}

function OperationStatus({ operation }) {
  const status = operation.host_status || operation.status;
  const success = status === "succeeded";
  const running = status === "queued" || status === "running";
  const Icon = success ? CheckCircle : running ? ArrowsClockwise : XCircle;
  return <span className={`operation-status operation-status--${status}`}><Icon size={14} className={running ? "spin" : ""} />{success ? "成功" : running ? "执行中" : "失败"}</span>;
}

function OperationResultDialog({ run, operationName, error, onClose }) {
  return <div className="operation-result-dialog">
    {run ? <>
      <div className="operation-result-summary"><OperationStatus operation={run} /><div><strong>{operationName(run.operation_code)}</strong><span>批次 {run.run_id}</span></div><div className="operation-result-counts"><span>目标 <strong>{run.target_count}</strong></span><span>成功 <strong>{run.succeeded_count}</strong></span><span>失败 <strong>{run.failed_count}</strong></span></div></div>
      <div className="operation-result-meta"><span>Playbook {run.playbook_version}</span><span>发起人 {run.requested_by}</span><span>{run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : ""}</span></div>
      <div className="operation-result-hosts">{run.hosts?.map((host) => <article key={host.host_result_id || host.server_id}><div><OperationStatus operation={host} /><strong>{host.server_name || host.server_id}</strong></div>{host.error_message && <p>{host.error_message}</p>}{host.result && <details><summary>查看结构化返回</summary><pre>{JSON.stringify(host.result, null, 2)}</pre></details>}</article>)}</div>
    </> : <div className="task-dialog-loading"><ArrowsClockwise size={24} className="spin" />正在读取任务结果</div>}
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button className="primary-action" onClick={onClose}>关闭</button></footer>
  </div>;
}

export function ServerManagement({ servers, runtimeMap, total, dataSource, requestJson, onServersChanged, onOpenTask, canManage = true, canExecute = true }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selectedGroupId, setSelectedGroupId] = useState("all");
  const [groupMemberIds, setGroupMemberIds] = useState(null);
  const [selectedId, setSelectedId] = useState(servers[0]?.server_id || null);
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState("ready");
  const [dialog, setDialog] = useState(null);
  const [referenceData, setReferenceData] = useState({ environments: [], cloud_providers: [] });
  const [serverGroups, setServerGroups] = useState([]);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [operationBusy, setOperationBusy] = useState(false);
  const [activeRun, setActiveRun] = useState(null);
  const [operations, setOperations] = useState([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const catalogRequestId = useRef(0);

  const visibleServers = useMemo(
    () => filterAssetsByGroup(filterServers(servers, query, status), groupMemberIds, "server_id"),
    [groupMemberIds, query, servers, status],
  );

  useEffect(() => {
    let cancelled = false;
    if (dataSource !== "api") {
      setServerGroups(buildDemoGroups(servers, []).filter((item) => item.resource_type === "server"));
      return undefined;
    }
    requestJson("/api/v1/groups?resource_type=server&lifecycle_status=active&limit=100")
      .then((response) => { if (!cancelled) setServerGroups(response.data); })
      .catch(() => { if (!cancelled) setServerGroups([]); });
    return () => { cancelled = true; };
  }, [dataSource, requestJson, servers]);

  useEffect(() => {
    let cancelled = false;
    if (selectedGroupId === "all") { setGroupMemberIds(null); return undefined; }
    const selectedGroup = serverGroups.find((item) => item.group_id === selectedGroupId);
    if (!selectedGroup) { setSelectedGroupId("all"); return undefined; }
    if (dataSource !== "api") {
      setGroupMemberIds(new Set((selectedGroup.members || []).map((item) => item.server_id)));
      return undefined;
    }
    requestJson(`/api/v1/groups/${selectedGroupId}`)
      .then((response) => { if (!cancelled) setGroupMemberIds(new Set(response.data.members.map((item) => item.server_id))); })
      .catch((error) => { if (!cancelled) { setGroupMemberIds(new Set()); setNotice(error.message); } });
    return () => { cancelled = true; };
  }, [dataSource, requestJson, selectedGroupId, serverGroups]);

  useEffect(() => {
    if (!servers.some((server) => server.server_id === selectedId)) setSelectedId(servers[0]?.server_id || null);
    setCheckedIds((current) => new Set([...current].filter((id) => servers.some((server) => server.server_id === id))));
  }, [servers, selectedId]);

  const loadDetail = useCallback(async (serverId = selectedId) => {
    const server = servers.find((item) => item.server_id === serverId);
    if (!server) { setDetail(null); return; }
    if (dataSource !== "api") {
      setDetail(buildDemoServerDetail(server, runtimeMap[server.server_id] || []));
      setDetailState("ready");
      return;
    }
    setDetailState("loading");
    try {
      const response = await requestJson(`/api/v1/servers/${server.server_id}`);
      setDetail(response.data);
      setDetailState("ready");
    } catch {
      setDetail(buildDemoServerDetail(server, runtimeMap[server.server_id] || []));
      setDetailState("fallback");
    }
  }, [dataSource, requestJson, runtimeMap, selectedId, servers]);

  useEffect(() => { loadDetail(); }, [loadDetail]);

  const openDialog = async (type) => {
    if (type === "task" && !canExecute) { setNotice("当前账号没有执行自动化任务的权限。"); return; }
    if (type !== "task" && !canManage) { setNotice("当前账号为只读权限，不能修改服务器。"); return; }
    setFormError("");
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后执行写操作。"); return; }
    if (type === "task") {
      const requestId = ++catalogRequestId.current;
      setDialog(type);
      setCatalogLoading(true);
      setOperations([]);
      try {
        const response = await requestJson("/api/v1/automation/operations");
        if (requestId !== catalogRequestId.current) return;
        setOperations(operationsForScope(response.data.operations, "server"));
      } catch (error) {
        if (requestId === catalogRequestId.current) setFormError(error.message);
      } finally {
        if (requestId === catalogRequestId.current) setCatalogLoading(false);
      }
      return;
    }
    if ((type === "create" || type === "edit") && !referenceData.environments.length) {
      try {
        const [reference, groups] = await Promise.all([requestJson("/api/v1/reference-data"), requestJson("/api/v1/groups?resource_type=server&lifecycle_status=active&limit=100")]);
        setReferenceData(reference.data);
        setServerGroups(groups.data);
      } catch (error) { setNotice(error.message); return; }
    }
    setDialog(type);
  };

  const saveServer = async (payload) => {
    setSaving(true); setFormError("");
    try {
      const editing = dialog === "edit";
      const response = await requestJson(editing ? `/api/v1/servers/${detail.server_id}` : "/api/v1/servers", { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
      setSelectedId(response.data.server_id);
      await onServersChanged();
      setDetail(response.data);
      setDialog(editing ? null : "connection");
      setNotice(editing ? "服务器信息已更新" : "服务器资产已创建，请继续配置账号密码并完成首次纳管。");
    } catch (error) { setFormError(error.message); } finally { setSaving(false); }
  };

  const saveConnection = async (payload) => {
    setSaving(true); setFormError("");
    try {
      const { bootstrap_requested: bootstrapRequested, ...connectionPayload } = payload;
      const response = await requestJson(`/api/v1/servers/${detail.server_id}/connection`, { method: "PUT", body: JSON.stringify(connectionPayload) });
      setDetail(response.data);
      if (bootstrapRequested) {
        const created = await requestJson("/api/v1/automation/runs", { method: "POST", body: JSON.stringify({ operation_code: "server.bootstrap_ssh", server_ids: [detail.server_id], parameters: {} }) });
        setActiveRun(created.data);
        setNotice("首次纳管已下发：系统将用密码建联，安装平台公钥并进行仅公钥复验。");
      } else {
        setNotice("Ansible 连接已保存");
      }
      setDialog(null);
      await onServersChanged();
    } catch (error) { setFormError(error.message); } finally { setSaving(false); }
  };

  const scanHostKeys = async (host, port) => {
    setFormError("");
    try {
      return (await requestJson("/api/v1/server-connections/ssh-host-keys:scan", { method: "POST", body: JSON.stringify({ host, port }) })).data;
    } catch (error) {
      setFormError(error.message);
      throw error;
    }
  };

  const retire = async () => {
    if (!detail || !window.confirm(`确认退役服务器“${detail.name}”？历史关系和操作记录会保留。`)) return;
    try {
      const response = await requestJson(`/api/v1/servers/${detail.server_id}?row_version=${detail.row_version}`, { method: "DELETE", confirmed: true });
      setDetail(response.data); await onServersChanged(); setNotice("服务器已退役");
    } catch (error) { setNotice(error.message); }
  };

  const operationName = (operationCode) => operations.find((item) => item.code === operationCode)?.name || OPERATION_LABELS[operationCode] || operationCode;

  const operationTargetIds = checkedIds.size
    ? [...checkedIds]
    : selectedId && !["disabled", "retired"].includes(servers.find((item) => item.server_id === selectedId)?.lifecycle_status) ? [selectedId] : [];
  const operationTargets = operationTargetIds.map((id) => servers.find((server) => server.server_id === id)).filter(Boolean);

  const runOperation = async (operationCode, parameters = {}, confirmed = false) => {
    if (dataSource !== "api") { setNotice("当前为模拟数据，不能执行服务器操作。"); return; }
    const targets = operationTargetIds;
    if (!targets.length) return;
    setOperationBusy(true); setNotice("");
    try {
      const created = await requestJson("/api/v1/automation/runs", { method: "POST", body: JSON.stringify({ operation_code: operationCode, server_ids: targets, parameters }), confirmed });
      setDialog(null);
      setActiveRun(created.data);
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
          await Promise.all([loadDetail(), onServersChanged()]);
        }
      } catch (error) {
        if (!cancelled) setNotice(`任务状态刷新失败：${error.message}`);
      }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeRun?.run_id, activeRun?.status, dataSource, loadDetail, onServersChanged, requestJson]);

  const viewOperationRun = async (runId) => {
    if (dataSource !== "api") return;
    setFormError(""); setActiveRun(null); setDialog("operation-result");
    try { setActiveRun((await requestJson(`/api/v1/automation/runs/${runId}`)).data); }
    catch (error) { setFormError(error.message); }
  };

  const selectedRuntimes = detail?.runtimes || [];
  const currentTargets = operationTargetIds.length;
  const executableVisibleServers = visibleServers.filter((item) => !["disabled", "retired"].includes(item.lifecycle_status));
  const toggleAll = (checked) => setCheckedIds(checked ? new Set(executableVisibleServers.map((item) => item.server_id)) : new Set());

  return <section className="asset-page server-management-page" aria-labelledby="server-page-title">
    <div className="asset-page-heading">
      <div><span className="eyebrow">服务器管理</span><h2 id="server-page-title">服务器</h2><p>维护服务器资产，并通过标准 Playbook 执行受控操作。</p></div>
      <div className="server-heading-actions">{canManage && <button className="secondary-action" onClick={() => openDialog("create")}><Plus size={17} />新增服务器</button>}<div className="asset-page-total"><HardDrives size={25} /><div><strong>{total}</strong><span>台服务器</span></div></div></div>
    </div>

    {notice && <div className="management-notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}
    <div className="asset-toolbar server-toolbar">
      <label className="asset-search"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、IP 或云实例 ID" /></label>
      <label className="asset-filter"><span>生命周期</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部状态</option>{Object.entries(SERVER_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="asset-filter"><span>管理分组</span><select value={selectedGroupId} onChange={(event) => { setSelectedGroupId(event.target.value); setCheckedIds(new Set()); }}><option value="all">全部分组</option>{serverGroups.map((group) => <option key={group.group_id} value={group.group_id}>{group.name}</option>)}</select></label>
      {canExecute && <div className="server-operation-bar"><span>执行目标 <strong>{currentTargets}</strong> 台</span><button className="server-task-action" disabled={operationBusy || !currentTargets} onClick={() => openDialog("task")}><Play size={15} />执行任务</button></div>}
    </div>

    {activeRun && <div className="active-operation-strip"><OperationStatus operation={activeRun} /><strong>{operationName(activeRun.operation_code)}</strong><span>{activeRun.target_count} 台目标</span><span>成功 {activeRun.succeeded_count}</span><span>失败 {activeRun.failed_count}</span><button onClick={() => onOpenTask ? onOpenTask(activeRun.run_id) : viewOperationRun(activeRun.run_id)}>任务中心<CaretRight size={13} /></button></div>}

    <div className="asset-layout">
      <div className="asset-list-card">
        <div className="asset-list-head server-list-head"><span><input type="checkbox" aria-label="选择当前服务器" checked={executableVisibleServers.length > 0 && executableVisibleServers.every((item) => checkedIds.has(item.server_id))} onChange={(event) => toggleAll(event.target.checked)} /></span><span>服务器</span><span>状态</span><span>连接</span><span>基础配置</span><span>内网 IP</span><span /></div>
        <div className="asset-list-body">
          {visibleServers.map((server) => <div className={selectedId === server.server_id ? "asset-list-row server-list-row asset-list-row--selected" : "asset-list-row server-list-row"} key={server.server_id} onClick={() => setSelectedId(server.server_id)}>
            <input type="checkbox" aria-label={`选择 ${server.name}`} disabled={!canExecute || ["disabled", "retired"].includes(server.lifecycle_status)} checked={checkedIds.has(server.server_id)} onClick={(event) => event.stopPropagation()} onChange={(event) => { const next = new Set(checkedIds); event.target.checked ? next.add(server.server_id) : next.delete(server.server_id); setCheckedIds(next); }} />
            <span className="server-identity"><HardDrives size={19} /><span><strong>{server.name}</strong><small>{server.provider_instance_id || server.server_id}</small></span></span>
            <StatusBadge status={server.lifecycle_status || "active"} />
            <span className={`connection-dot connection-dot--${server.connection_status || "unknown"}`}><i />{{ reachable: "可连接", unreachable: "不可达", auth_failed: "认证失败", unknown: "未检查" }[server.connection_status || "unknown"]}</span>
            <span className="config-summary" title={formatServerConfig(server)}>{formatServerConfig(server)}</span>
            <span className="mono-value" title={server.internal_ip || "未配置"}>{server.internal_ip || "未配置"}</span><CaretRight size={16} />
          </div>)}
          {!visibleServers.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配服务器</strong><span>请调整搜索词或状态筛选。</span></div>}
        </div>
      </div>

      <aside className="asset-detail-card server-detail-card" aria-label="服务器详情">
        {detailState === "loading" ? <div className="detail-loading"><ArrowsClockwise size={28} className="spin" /><span>正在读取服务器详情</span></div> : detail ? <>
          <div className="detail-header"><div className="detail-title"><span className="detail-icon"><HardDrives size={22} /></span><div><h3>{detail.name}</h3><span>{detail.provider_instance_id || detail.server_id}</span></div></div><StatusBadge status={detail.lifecycle_status} /></div>
          {canManage && <div className="server-detail-actions"><button onClick={() => openDialog("edit")} disabled={detail.lifecycle_status === "retired"}><PencilSimple size={15} />编辑</button><button onClick={() => openDialog("connection")} disabled={detail.lifecycle_status === "retired"}><Plug size={15} />连接配置</button><button className="danger-action" onClick={retire} disabled={detail.lifecycle_status === "retired"}><Trash size={15} />退役</button></div>}
          {detailState === "fallback" && <div className="detail-notice">本机 API 详情暂不可用，当前展示模拟明细。</div>}
          <div className="detail-section"><h4>基础配置</h4><div className="detail-field-grid"><DetailField icon={Cpu} label="CPU" value={`${detail.cpu_cores || 0} 核`} /><DetailField icon={Memory} label="内存" value={`${detail.memory_gib || 0} GiB`} /><DetailField icon={HardDrive} label="磁盘" value={`${detail.disk_gib || 0} GiB`} /><DetailField icon={Desktop} label="操作系统" value={`${detail.os_name || "未配置"} ${detail.os_version || ""}`.trim()} /><DetailField icon={Rows} label="架构" value={detail.architecture} /><DetailField icon={IdentificationCard} label="工作账号" value={detail.work_account} /></div></div>
          <div className="detail-section"><h4>网络与归属</h4><div className="detail-field-grid"><DetailField icon={Cloud} label="云环境" value={`${detail.provider_code || "未配置"} / ${detail.environment_code || "未配置"}`} /><DetailField icon={Tag} label="内网 IP" value={detail.internal_ip} /><DetailField icon={Tag} label="公网 IP" value={detail.public_ip || "无"} /><DetailField icon={Rows} label="数据版本" value={`v${detail.row_version || 1}`} /></div></div>
          <div className="detail-section server-connection-summary"><div className="detail-section-title"><h4>Ansible 连接</h4>{detail.connection && <span className={`connection-summary-status connection-summary-status--${detail.connection.connection_status}`}>{detail.connection.connection_status === "reachable" ? "正常" : detail.connection.connection_status === "unknown" ? "未检查" : "异常"}</span>}</div>{detail.connection ? <div className="connection-summary-grid"><span><small>连接</small><strong>{detail.connection.connection_type === "local" ? "本机测试" : `${detail.connection.username}@${detail.connection.host}:${detail.connection.port}`}</strong></span><span><small>日常认证</small><strong>{detail.connection.connection_type === "local" ? "本机" : detail.connection.bootstrap_status === "succeeded" ? `公钥 · ${detail.connection.credential_ref}` : "密码建联待完成"}</strong></span>{detail.connection.password_configured && <span><small>恢复凭据</small><strong>SSH 密码已加密保存</strong></span>}{detail.connection.bootstrap_status === "failed" && <p>{detail.connection.bootstrap_error || "首次纳管失败，请修正连接后重新纳管"}</p>}{detail.connection.last_error && detail.connection.bootstrap_status !== "failed" && <p>{detail.connection.last_error}</p>}</div> : canManage ? <button className="empty-connection-action" onClick={() => openDialog("connection")}><Plug size={17} />配置账号密码并首次纳管</button> : <span className="detail-empty-line">尚未配置连接</span>}</div>
          {(detail.connection?.last_facts || detail.connection?.last_runtime_discovery) && <div className="detail-section"><h4>最近观测结果</h4>{detail.connection.last_facts && <div className="detail-field-grid"><DetailField icon={Desktop} label="主机名" value={detail.connection.last_facts.hostname} /><DetailField icon={Rows} label="观测系统" value={`${detail.connection.last_facts.operating_system || ""} ${detail.connection.last_facts.os_version || ""}`.trim()} /><DetailField icon={Cpu} label="观测 CPU" value={`${detail.connection.last_facts.cpu_cores || 0} 核`} /><DetailField icon={Memory} label="观测内存" value={`${detail.connection.last_facts.memory_mib || 0} MiB`} /></div>}{detail.connection.last_runtime_discovery && <div className="runtime-discovery-chips"><small>发现的运行时</small><div className="group-tags">{detail.connection.last_runtime_discovery.detected?.map((item) => <span key={item}>{item}</span>)}{!detail.connection.last_runtime_discovery.detected?.length && <span className="group-tag--empty">未发现已知运行时</span>}</div></div>}</div>}
          <div className="detail-section"><div className="detail-section-title"><h4>承载的运行时</h4><span>{selectedRuntimes.length}</span></div><div className="detail-runtime-list">{selectedRuntimes.map((runtime) => <div key={runtime.runtime_id}><span className="runtime-icon"><Cube size={17} /></span><span><strong>{displayRuntimeName(runtime.name)}</strong><small>{runtime.family_code || "未分类"} · {runtime.deployment_mode || "baremetal"}</small></span><StatusBadge status={runtime.lifecycle_status === "active" ? "active" : "disabled"} /></div>)}{!selectedRuntimes.length && <span className="detail-empty-line">尚未关联运行时</span>}</div></div>
          <div className="detail-section"><div className="detail-section-title"><h4>最近操作</h4><span>{detail.recent_operations?.length || 0}</span></div><div className="operation-history">{detail.recent_operations?.slice(0, 5).map((operation) => <button type="button" key={operation.run_id} onClick={() => onOpenTask ? onOpenTask(operation.run_id) : viewOperationRun(operation.run_id)}><OperationStatus operation={operation} /><span><strong>{operationName(operation.operation_code)}</strong><small>{operation.error_message || new Date(operation.created_at).toLocaleString("zh-CN")}</small></span><CaretRight size={13} /></button>)}{!detail.recent_operations?.length && <span className="detail-empty-line">暂无操作记录</span>}</div></div>
          <div className="detail-section"><div className="detail-section-title"><h4>管理分组</h4><span>{detail.groups?.length || 0}</span></div><div className="group-tags">{detail.groups?.map((group) => <span key={group.group_id}>{group.name}</span>)}{!detail.groups?.length && <span className="group-tag--empty">尚未加入管理分组</span>}</div></div>
        </> : <div className="detail-loading"><HardDrives size={28} /><span>选择一台服务器查看详情</span></div>}
      </aside>
    </div>

    {dialog === "create" && <Dialog title="新增服务器" onClose={() => setDialog(null)}><ServerForm referenceData={referenceData} serverGroups={serverGroups} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveServer} /></Dialog>}
    {dialog === "edit" && detail && <Dialog title="编辑服务器" onClose={() => setDialog(null)}><ServerForm server={detail} referenceData={referenceData} serverGroups={serverGroups} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveServer} /></Dialog>}
    {dialog === "connection" && detail && <Dialog title="服务器接入与 Ansible 连接" onClose={() => setDialog(null)}><ConnectionForm server={detail} connection={detail.connection} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveConnection} onScanHostKeys={scanHostKeys} /></Dialog>}
    {dialog === "task" && <Dialog wide title="执行标准任务" onClose={() => !operationBusy && setDialog(null)}><AutomationExecutionDialog operations={operations} loading={catalogLoading} targets={operationTargets} targetId={(server) => server.server_id} targetName={(server) => server.name} targetUnit="台服务器" emptyMessage="当前没有服务器任务" submitting={operationBusy} error={formError} onCancel={() => setDialog(null)} onSubmit={(operation, parameters) => runOperation(operation.code, parameters, operation.risk_level !== "low")} /></Dialog>}
    {dialog === "operation-result" && <Dialog wide title="任务执行结果" onClose={() => setDialog(null)}><OperationResultDialog run={activeRun} operationName={operationName} error={formError} onClose={() => setDialog(null)} /></Dialog>}
  </section>;
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  BookOpen,
  BracketsCurly,
  CaretRight,
  CheckCircle,
  ClockCounterClockwise,
  FloppyDisk,
  MagnifyingGlass,
  PencilSimple,
  Play,
  Plus,
  ShieldCheck,
  WarningCircle,
  X,
} from "@phosphor-icons/react";


const DEMO_AUTOMATIONS = [
  { automation_id: "auto-1", code: "server.check_connection", name: "检查服务器连接", description: "验证平台到目标服务器的 Ansible 连接与认证是否可用。", category: "连接与诊断", scope: "server", lifecycle_status: "published", risk_level: "low", version_label: "1.0.0", version_count: 1, supports_batch: true, builtin: true, validation_status: "passed", timeout_seconds: 30 },
  { automation_id: "auto-2", code: "server.collect_facts", name: "采集服务器信息", description: "采集主机名、操作系统、CPU、内存、磁盘和网络信息。", category: "资产采集", scope: "server", lifecycle_status: "published", risk_level: "low", version_label: "1.0.0", version_count: 1, supports_batch: true, builtin: true, validation_status: "passed", timeout_seconds: 120 },
  { automation_id: "auto-3", code: "runtime.restart", name: "重启运行时", description: "重启裸金属运行时服务并等待端点恢复。", category: "生命周期", scope: "runtime", lifecycle_status: "published", risk_level: "medium", version_label: "1.2.0", version_count: 3, supports_batch: true, builtin: true, validation_status: "passed", timeout_seconds: 180 },
  { automation_id: "auto-4", code: "agent.apply_deployment", name: "部署或同步智能体", description: "同步主子智能体、独立飞书账户与路由。", category: "部署管理", scope: "agent", lifecycle_status: "published", risk_level: "medium", version_label: "1.2.0", version_count: 3, supports_batch: false, builtin: true, validation_status: "passed", timeout_seconds: 180 },
  { automation_id: "auto-5", code: "agent.migrate_deployment", name: "更换智能体运行时", description: "受控预置目标、切换渠道与部署关系，并在失败时恢复原部署。", category: "部署管理", scope: "agent", lifecycle_status: "published", risk_level: "high", version_label: "1.0.0", version_count: 1, supports_batch: false, builtin: true, validation_status: "passed", timeout_seconds: 360 },
  { automation_id: "auto-6", code: "agent.remove_deployment", name: "解除智能体运行时部署", description: "撤销平台管理的渠道与部署关系，保留智能体 identity 和工作区。", category: "部署管理", scope: "agent", lifecycle_status: "published", risk_level: "high", version_label: "1.0.0", version_count: 1, supports_batch: false, builtin: true, validation_status: "passed", timeout_seconds: 240 },
];

const SCOPE_LABELS = { server: "服务器", runtime: "运行时", agent: "智能体" };
const RISK_LABELS = { low: "低风险", medium: "中风险", high: "高风险" };
const VERSION_STATUS_LABELS = { draft: "草稿", published: "已发布", withdrawn: "已撤回" };
const RUN_STATUS_LABELS = { queued: "等待执行", running: "执行中", succeeded: "成功", partial: "部分成功", failed: "失败", cancelled: "已取消" };
const DEFAULT_PLAYBOOK = `---
- name: AgentsWatch managed automation
  hosts: all
  gather_facts: false
  tasks:
    - name: Return a structured result
      ansible.builtin.set_fact:
        agentswatch_result:
          message: completed
`;

function withDemoDetail(item) {
  const current = {
    automation_version_id: `${item.automation_id}-version`, version_label: item.version_label,
    version_status: "published", validation_status: item.validation_status,
    content_hash: "d4a7c2e9f1b3486ab452f6b0a72ce2af", published_by: "system:catalog-sync",
    published_at: "2026-07-13T09:30:00+08:00", parameter_schema: {},
    playbook_content: DEFAULT_PLAYBOOK, row_version: 1,
  };
  return { ...item, row_version: 1, current_version: current, draft_version: null, versions: [current], recent_runs: [], run_summary: { total: 0, succeeded: 0, failed: 0, active: 0 } };
}

function Modal({ title, children, onClose, wide = false }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className={wide ? "management-dialog management-dialog--automation" : "management-dialog"} role="dialog" aria-modal="true" aria-label={title}><header><h3>{title}</h3><button onClick={onClose} aria-label="关闭"><X size={18} /></button></header>{children}</section></div>;
}

function AutomationEditor({ detail, saving, error, onCancel, onSave }) {
  const editing = Boolean(detail);
  const version = detail?.draft_version;
  const [form, setForm] = useState(() => ({
    code: detail?.code || "server.", name: detail?.name || "", description: detail?.description || "",
    category: detail?.category || "未分类", scope: detail?.scope || "server",
    risk_level: detail?.risk_level || "low", requires_become: detail?.requires_become || false,
    supports_batch: detail?.supports_batch ?? true, timeout_seconds: detail?.timeout_seconds || 120,
    version_label: version?.version_label || "1.0.0",
    playbook_content: version?.playbook_content || DEFAULT_PLAYBOOK,
    parameter_schema: JSON.stringify(version?.parameter_schema || {}, null, 2),
  }));
  const [localError, setLocalError] = useState("");
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = (event) => {
    event.preventDefault();
    try {
      const parameters = JSON.parse(form.parameter_schema || "{}");
      if (!parameters || Array.isArray(parameters) || typeof parameters !== "object") throw new Error("参数定义必须是 JSON 对象");
      setLocalError("");
      onSave({ ...form, timeout_seconds: Number(form.timeout_seconds), parameter_schema: parameters });
    } catch (parseError) { setLocalError(parseError.message); }
  };
  return <form className="automation-editor-form" onSubmit={submit}>
    <div className="automation-editor-meta">
      <label><span>稳定编码</span><input required disabled={editing} value={form.code} onChange={(event) => set("code", event.target.value)} /></label>
      <label><span>名称</span><input required value={form.name} onChange={(event) => set("name", event.target.value)} /></label>
      <label><span>分类</span><input required value={form.category} onChange={(event) => set("category", event.target.value)} /></label>
      <label><span>适用对象</span><select value={form.scope} onChange={(event) => set("scope", event.target.value)}><option value="server">服务器</option><option value="runtime">运行时</option><option value="agent">智能体</option></select></label>
      <label><span>风险</span><select value={form.risk_level} onChange={(event) => set("risk_level", event.target.value)}><option value="low">低风险</option><option value="medium">中风险</option><option value="high">高风险</option></select></label>
      <label><span>超时（秒）</span><input type="number" min="1" max="3600" required value={form.timeout_seconds} onChange={(event) => set("timeout_seconds", event.target.value)} /></label>
      <label><span>版本</span><input required value={form.version_label} onChange={(event) => set("version_label", event.target.value)} /></label>
      <label className="automation-editor-checks"><span>执行策略</span><span><input type="checkbox" checked={form.supports_batch} onChange={(event) => set("supports_batch", event.target.checked)} />支持批量</span><span><input type="checkbox" checked={form.requires_become} onChange={(event) => set("requires_become", event.target.checked)} />需要 sudo</span></label>
      <label className="automation-editor-wide"><span>说明</span><textarea rows="2" value={form.description} onChange={(event) => set("description", event.target.value)} /></label>
    </div>
    <div className="automation-editor-code-grid">
      <label><span>Playbook YAML</span><textarea spellCheck="false" value={form.playbook_content} onChange={(event) => set("playbook_content", event.target.value)} /></label>
      <label><span>参数 Schema（JSON）</span><textarea spellCheck="false" value={form.parameter_schema} onChange={(event) => set("parameter_schema", event.target.value)} /></label>
    </div>
    {(error || localError) && <div className="management-form-error">{error || localError}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={15} />{saving ? "保存中" : "保存草稿"}</button></footer>
  </form>;
}

function NewVersionForm({ currentVersion, saving, error, onCancel, onSave }) {
  const [label, setLabel] = useState("");
  return <form onSubmit={(event) => { event.preventDefault(); onSave(label); }}><div className="new-version-form"><p>从当前发布版本 <strong>v{currentVersion?.version_label}</strong> 创建一个可编辑草稿。发布前必须重新通过语法校验。</p><label><span>新版本号</span><input autoFocus required value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如 1.1.0" /></label></div>{error && <div className="management-form-error">{error}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving || !label.trim()}><Plus size={15} />{saving ? "创建中" : "创建草稿"}</button></footer></form>;
}

export function AutomationLibraryManagement({ dataSource, requestJson, canManage = true }) {
  const [items, setItems] = useState(DEMO_AUTOMATIONS);
  const [selectedId, setSelectedId] = useState(DEMO_AUTOMATIONS[0].automation_id);
  const [detail, setDetail] = useState(withDemoDetail(DEMO_AUTOMATIONS[0]));
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");
  const [tab, setTab] = useState("overview");
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadList = useCallback(async (preferredId = null) => {
    if (dataSource !== "api") return;
    const response = await requestJson("/api/v1/automation-library?limit=100");
    setItems(response.data);
    setSelectedId((current) => preferredId || (response.data.some((item) => item.automation_id === current) ? current : response.data[0]?.automation_id));
  }, [dataSource, requestJson]);
  const loadDetail = useCallback(async (id = selectedId) => {
    if (!id) return;
    if (dataSource !== "api" || String(id).startsWith("auto-")) {
      const selected = items.find((item) => item.automation_id === id);
      if (selected) setDetail(withDemoDetail(selected));
      return;
    }
    const response = await requestJson(`/api/v1/automation-library/${id}`);
    setDetail(response.data);
  }, [dataSource, items, requestJson, selectedId]);

  useEffect(() => { loadList().catch(() => setNotice("自动化库接口暂不可用，当前展示模拟数据。")); }, [loadList]);
  useEffect(() => { loadDetail().catch((loadError) => setNotice(loadError.message)); }, [loadDetail]);

  const refresh = async (id = selectedId) => { await loadList(id); await loadDetail(id); };
  const write = async (action) => {
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后执行写操作。"); return; }
    setBusy(true); setError("");
    try {
      const preferredId = await action();
      setDialog(null);
      await refresh(preferredId || selectedId);
    }
    catch (actionError) { setError(actionError.message); }
    finally { setBusy(false); }
  };
  const createAutomation = (values) => write(async () => {
    const response = await requestJson("/api/v1/automation-library", { method: "POST", body: JSON.stringify(values) });
    setSelectedId(response.data.automation_id); setNotice("自动化草稿已创建，请完成校验后发布。");
    return response.data.automation_id;
  });
  const updateDraft = (values) => write(async () => {
    const draft = detail.draft_version;
    await requestJson(`/api/v1/automation-library/${detail.automation_id}/versions/${draft.automation_version_id}`, { method: "PATCH", body: JSON.stringify({ ...values, row_version: detail.row_version, version_row_version: draft.row_version }) });
    setNotice("草稿已保存，原校验结果已失效，需要重新校验。");
  });
  const createVersion = (versionLabel) => write(async () => {
    await requestJson(`/api/v1/automation-library/${detail.automation_id}/versions`, { method: "POST", body: JSON.stringify({ version_label: versionLabel }) });
    setNotice("新版本草稿已创建。");
  });
  const validateDraft = () => write(async () => {
    const draft = detail.draft_version;
    const response = await requestJson(`/api/v1/automation-library/${detail.automation_id}/versions/${draft.automation_version_id}/validate`, { method: "POST", body: "{}" });
    setNotice(response.data.validation_status === "passed" ? "Ansible 语法校验通过。" : "语法校验失败，请查看校验输出。");
  });
  const publishDraft = () => write(async () => {
    const draft = detail.draft_version;
    await requestJson(`/api/v1/automation-library/${detail.automation_id}/versions/${draft.automation_version_id}/publish`, { method: "POST", body: "{}" });
    setNotice(`版本 v${draft.version_label} 已发布，后续新任务将固定使用该版本。`);
  });

  const filtered = useMemo(() => items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    return (scope === "all" || item.scope === scope) && (!keyword || `${item.name} ${item.code} ${item.category}`.toLowerCase().includes(keyword));
  }), [items, query, scope]);
  const current = detail?.current_version;
  const shownVersion = detail?.draft_version || current;

  return <section className="automation-page">
    <header className="asset-page-heading"><div><span className="eyebrow">自动化能力</span><h2>自动化库</h2><p>管理服务器、运行时和智能体使用的 Playbook、参数和发布版本。</p></div><div className="automation-heading-actions">{canManage && <button onClick={() => { setError(""); setDialog("create"); }}><Plus size={16} />新建自动化</button>}<div className="asset-page-total"><BookOpen size={24} /><div><strong>{items.length}</strong><span>个自动化</span></div></div></div></header>
    <div className="automation-summary-grid"><article><span className="automation-summary-icon"><ShieldCheck size={21} /></span><div><strong>{items.filter((item) => item.lifecycle_status === "published").length}</strong><small>已发布</small></div></article><article><span className="automation-summary-icon automation-summary-icon--blue"><ClockCounterClockwise size={21} /></span><div><strong>{items.filter((item) => item.has_draft || item.lifecycle_status === "draft").length}</strong><small>有草稿</small></div></article><article><span className="automation-summary-icon automation-summary-icon--amber"><WarningCircle size={21} /></span><div><strong>{items.filter((item) => item.risk_level !== "low").length}</strong><small>中高风险</small></div></article></div>
    <div className="asset-toolbar automation-toolbar"><label className="asset-search"><MagnifyingGlass size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、编码或分类" /></label><label className="asset-filter"><span>对象</span><select value={scope} onChange={(event) => setScope(event.target.value)}><option value="all">全部对象</option>{Object.entries(SCOPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><span className="toolbar-summary">筛选结果 <strong>{filtered.length}</strong> 个</span></div>
    {notice && <div className="prototype-notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={13} /></button></div>}
    {error && !dialog && <div className="task-action-error"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭错误"><X size={13} /></button></div>}
    <div className="automation-library-layout"><div className="automation-library-list"><div className="automation-list-head"><span>自动化</span><span>适用对象</span><span>当前版本</span><span>风险</span><span /></div>{filtered.map((item) => <button key={item.automation_id} className={item.automation_id === selectedId ? "automation-list-row automation-list-row--selected" : "automation-list-row"} onClick={() => { setSelectedId(item.automation_id); setTab("overview"); }}><span className="automation-identity"><i><BracketsCurly size={17} /></i><span><strong>{item.name}</strong><small>{item.code}</small></span></span><span className={`scope-chip scope-chip--${item.scope}`}>{SCOPE_LABELS[item.scope]}</span><span className="version-cell"><strong>{item.version_label ? `v${item.version_label}` : "未发布"}</strong><small>{item.version_count || 0} 个版本{item.has_draft ? " · 有草稿" : ""}</small></span><span className={`risk-chip risk-chip--${item.risk_level}`}>{RISK_LABELS[item.risk_level]}</span><CaretRight size={15} /></button>)}</div>
      <aside className="automation-detail-card automation-detail-card--full">{detail && <><header><span className="detail-icon"><BracketsCurly size={21} /></span><div><h3>{detail.name}</h3><code>{detail.code}</code></div><span className={detail.lifecycle_status === "published" ? "published-chip" : "published-chip published-chip--draft"}><i />{detail.lifecycle_status === "published" ? "已发布" : "草稿"}</span></header>{canManage && <div className="automation-detail-actions">{detail.draft_version ? <button onClick={() => { setError(""); setDialog("edit"); }}><PencilSimple size={14} />编辑草稿</button> : current ? <button onClick={() => { setError(""); setDialog("version"); }}><Plus size={14} />新建版本</button> : null}{detail.draft_version && <button onClick={validateDraft} disabled={busy}><CheckCircle size={14} />校验</button>}{detail.draft_version?.validation_status === "passed" && <button onClick={publishDraft} disabled={busy}><Play size={14} />发布</button>}</div>}<div className="automation-detail-tabs">{[["overview", "明细"], ["playbook", "Playbook"], ["versions", `版本（${detail.versions?.length || 0}）`]].map(([value, label]) => <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{label}</button>)}</div>
        {tab === "overview" && <><section><h4>基本信息</h4><p>{detail.description || "暂无说明"}</p><dl><div><dt>分类</dt><dd>{detail.category}</dd></div><div><dt>适用对象</dt><dd>{SCOPE_LABELS[detail.scope]}</dd></div><div><dt>风险等级</dt><dd>{RISK_LABELS[detail.risk_level]}</dd></div><div><dt>批量执行</dt><dd>{detail.supports_batch ? "支持" : "不支持"}</dd></div><div><dt>sudo</dt><dd>{detail.requires_become ? "需要" : "不需要"}</dd></div><div><dt>执行超时</dt><dd>{detail.timeout_seconds} 秒</dd></div><div><dt>来源</dt><dd>{detail.builtin ? "平台内置" : "在线创建"}</dd></div><div><dt>数据版本</dt><dd>v{detail.row_version}</dd></div></dl></section><section><div className="automation-section-title"><h4>{detail.draft_version ? "当前草稿" : "当前发布版本"}</h4><span>v{shownVersion?.version_label || "—"}</span></div>{shownVersion ? <div className="version-card"><span className={`version-state version-state--${shownVersion.validation_status}`}><CheckCircle size={16} />{shownVersion.validation_status === "passed" ? "语法校验通过" : shownVersion.validation_status === "failed" ? "语法校验失败" : "等待语法校验"}</span><code>{String(shownVersion.content_hash || "").slice(0, 16)}</code><small>{shownVersion.version_status === "published" ? `发布人 ${shownVersion.published_by || "系统"}` : `创建人 ${shownVersion.created_by || "—"}`}</small><small>{shownVersion.published_at || shownVersion.updated_at ? new Date(shownVersion.published_at || shownVersion.updated_at).toLocaleString("zh-CN") : ""}</small></div> : <div className="automation-empty-section">尚未创建版本</div>}</section><section><div className="automation-section-title"><h4>参数定义</h4><span>{Object.keys(shownVersion?.parameter_schema || {}).length}</span></div>{Object.entries(shownVersion?.parameter_schema || {}).map(([name, definition]) => <div className="parameter-row" key={name}><code>{name}</code><span>{definition.label || name}{definition.required ? " *" : ""}</span><small>{definition.type}</small></div>)}{!Object.keys(shownVersion?.parameter_schema || {}).length && <div className="automation-empty-section">该版本不需要用户参数</div>}</section><section><div className="automation-section-title"><h4>执行情况</h4><span>{detail.run_summary?.total || 0}</span></div><div className="automation-run-summary"><span>成功 <strong>{detail.run_summary?.succeeded || 0}</strong></span><span>失败 <strong>{detail.run_summary?.failed || 0}</strong></span><span>进行中 <strong>{detail.run_summary?.active || 0}</strong></span></div><div className="automation-recent-runs">{detail.recent_runs?.map((run) => <div key={run.run_id}><span className={`run-state run-state--${run.status}`}>{RUN_STATUS_LABELS[run.status]}</span><span><strong>{run.target_count} 个{SCOPE_LABELS[run.target_type]}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")}</small></span></div>)}{!detail.recent_runs?.length && <div className="automation-empty-section">暂无执行记录</div>}</div></section></>}
        {tab === "playbook" && <section className="automation-playbook-detail"><div className="automation-section-title"><h4>{detail.draft_version ? "草稿 Playbook" : "当前发布 Playbook"}</h4><span>v{shownVersion?.version_label}</span></div><pre>{shownVersion?.playbook_content || "暂无 Playbook 内容"}</pre>{shownVersion?.validation_report && <details><summary>查看最近校验输出</summary><pre>{shownVersion.validation_report.stdout || shownVersion.validation_report.stderr || "校验通过"}</pre></details>}</section>}
        {tab === "versions" && <section className="automation-version-history"><h4>版本历史</h4>{detail.versions?.map((version) => <article key={version.automation_version_id}><span className={`version-history-icon version-history-icon--${version.version_status}`}>{version.version_status === "published" ? <CheckCircle size={15} /> : <PencilSimple size={15} />}</span><div><strong>v{version.version_label}</strong><small>{VERSION_STATUS_LABELS[version.version_status]} · {version.source_kind === "builtin" ? "平台内置" : "在线管理"}</small><code>{String(version.content_hash).slice(0, 20)}</code></div><span><em className={`validation-label validation-label--${version.validation_status}`}>{version.validation_status === "passed" ? "校验通过" : version.validation_status === "failed" ? "校验失败" : "待校验"}</em><small>{version.published_at || version.created_at ? new Date(version.published_at || version.created_at).toLocaleString("zh-CN") : ""}</small></span></article>)}</section>}</> }</aside></div>
    {dialog === "create" && <Modal title="新建自动化" wide onClose={() => setDialog(null)}><AutomationEditor saving={busy} error={error} onCancel={() => setDialog(null)} onSave={createAutomation} /></Modal>}
    {dialog === "edit" && <Modal title={`编辑草稿 · ${detail.name}`} wide onClose={() => setDialog(null)}><AutomationEditor detail={detail} saving={busy} error={error} onCancel={() => setDialog(null)} onSave={updateDraft} /></Modal>}
    {dialog === "version" && <Modal title="创建新版本" onClose={() => setDialog(null)}><NewVersionForm currentVersion={current} saving={busy} error={error} onCancel={() => setDialog(null)} onSave={createVersion} /></Modal>}
  </section>;
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise, Buildings, CaretRight, Cube, FloppyDisk, FolderSimple,
  HardDrives, Info, MagnifyingGlass, PencilSimple, Plus, Robot, SquaresFour,
  Tag, Trash, TreeStructure, Users, X,
} from "@phosphor-icons/react";
import { flattenAgentMap, AGENT_ARCHETYPE_LABELS, AGENT_STATUS_LABELS } from "./agentUtils.js";
import { flattenRuntimeMap } from "./runtimeUtils.js";
import { displayRuntimeName, SERVER_STATUS_LABELS } from "./serverUtils.js";
import { buildDemoGroupDetail, buildDemoGroups, filterGroups, GROUP_RESOURCE_LABELS, GROUP_STATUS_LABELS } from "./groupUtils.js";

function GroupLifecycle({ status = "active" }) {
  return <span className={`asset-status asset-status--${status}`}><i />{GROUP_STATUS_LABELS[status] || status}</span>;
}

function ResourceBadge({ type }) {
  const Icon = type === "server" ? HardDrives : type === "runtime" ? Cube : Robot;
  return <span className={`group-resource-badge group-resource-badge--${type}`}><Icon size={13} />{GROUP_RESOURCE_LABELS[type] || type}</span>;
}

function Dialog({ title, children, onClose }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="management-dialog management-dialog--wide" role="dialog" aria-modal="true" aria-label={title}>
      <header><h3>{title}</h3><button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button></header>
      {children}
    </section>
  </div>;
}

function candidateMeta(resourceType, item) {
  if (resourceType === "server") return `${item.internal_ip || "未配置 IP"} · ${SERVER_STATUS_LABELS[item.lifecycle_status] || item.lifecycle_status}`;
  if (resourceType === "runtime") return `${item.server_name || "未关联服务器"} · ${item.family_code || "未分类"}`;
  return `${item.owning_department_name || "未归属部门"} · ${AGENT_ARCHETYPE_LABELS[item.service_archetype] || item.service_archetype}`;
}

function candidateId(resourceType, item) {
  return item[`${resourceType}_id`];
}

function GroupForm({ group, candidates, candidatesLoading, saving, error, onCancel, onResourceChange, onSave }) {
  const editing = Boolean(group);
  const [form, setForm] = useState(() => ({
    group_code: group?.group_code || "",
    name: group?.name || "",
    resource_type: group?.resource_type || "server",
    purpose: group?.purpose || "",
    lifecycle_status: group?.lifecycle_status || "active",
    member_ids: new Set((group?.members || []).map((item) => item.member_id)),
  }));
  const [memberQuery, setMemberQuery] = useState("");
  const visibleCandidates = useMemo(() => {
    const keyword = memberQuery.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return candidates;
    return candidates.filter((item) => `${item.name || ""} ${candidateMeta(form.resource_type, item)}`.toLocaleLowerCase("zh-CN").includes(keyword));
  }, [candidates, form.resource_type, memberQuery]);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const changeResource = async (value) => {
    set("resource_type", value);
    set("member_ids", new Set());
    setMemberQuery("");
    await onResourceChange(value);
  };
  const toggleMember = (id, checked) => {
    const next = new Set(form.member_ids);
    checked ? next.add(id) : next.delete(id);
    set("member_ids", next);
  };
  const submit = (event) => {
    event.preventDefault();
    const payload = {
      name: form.name.trim(), purpose: form.purpose.trim() || null,
      lifecycle_status: form.lifecycle_status, member_ids: [...form.member_ids],
    };
    if (editing) payload.row_version = group.row_version;
    else { payload.group_code = form.group_code.trim().toLowerCase(); payload.resource_type = form.resource_type; }
    onSave(payload);
  };
  return <form onSubmit={submit} className="group-management-form">
    <div className="management-form-grid">
      <label className="management-field"><span>分组编码</span><input required disabled={editing} pattern="[a-z0-9][a-z0-9._-]+" value={form.group_code} onChange={(event) => set("group_code", event.target.value.toLowerCase())} placeholder="例如 prod-servers" /><small>创建后不可修改，用于接口筛选和任务审计。</small></label>
      <label className="management-field"><span>资源类型</span><select disabled={editing} value={form.resource_type} onChange={(event) => changeResource(event.target.value)}><option value="server">服务器</option><option value="runtime">运行时</option><option value="agent">智能体</option></select><small>一个分组只维护一种资源，避免批量任务目标混用。</small></label>
      <label className="management-field"><span>分组名称</span><input required value={form.name} onChange={(event) => set("name", event.target.value)} /></label>
      <label className="management-field"><span>状态</span><select value={form.lifecycle_status} onChange={(event) => set("lifecycle_status", event.target.value)}>{Object.entries(GROUP_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="management-field management-field--wide"><span>用途说明</span><textarea value={form.purpose} onChange={(event) => set("purpose", event.target.value)} placeholder="说明这个分组用于哪些批量管理场景" /></label>
      <div className="group-member-picker management-field--wide">
        <div className="group-member-picker-head"><span>分组成员 <strong>{form.member_ids.size}</strong></span><label><MagnifyingGlass size={15} /><input value={memberQuery} onChange={(event) => setMemberQuery(event.target.value)} placeholder="搜索可选成员" /></label></div>
        <div className="group-member-picker-list">
          {candidatesLoading ? <div className="group-picker-empty"><ArrowsClockwise className="spin" size={20} />正在读取资源</div> : visibleCandidates.map((item) => {
            const id = candidateId(form.resource_type, item);
            return <label key={id} className={form.member_ids.has(id) ? "group-candidate group-candidate--selected" : "group-candidate"}><input type="checkbox" checked={form.member_ids.has(id)} onChange={(event) => toggleMember(id, event.target.checked)} /><span><strong>{form.resource_type === "runtime" ? displayRuntimeName(item.name) : item.name}</strong><small>{candidateMeta(form.resource_type, item)}</small></span></label>;
          })}
          {!candidatesLoading && !visibleCandidates.length && <div className="group-picker-empty">没有匹配的可选成员</div>}
        </div>
      </div>
    </div>
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving || candidatesLoading}><FloppyDisk size={16} />{saving ? "保存中" : "保存分组"}</button></footer>
  </form>;
}

function GroupMember({ resourceType, member }) {
  const Icon = resourceType === "server" ? HardDrives : resourceType === "runtime" ? Cube : Robot;
  let meta = "";
  let trailing = "";
  if (resourceType === "server") {
    meta = `${member.internal_ip || "未配置 IP"} · ${member.provider_instance_id || member.server_id}`;
    trailing = `${member.runtime_count || 0} 个运行时`;
  } else if (resourceType === "runtime") {
    meta = `${member.server_name || "未关联服务器"} · ${member.server_internal_ip || "未配置 IP"}`;
    trailing = member.family_code || "未分类";
  } else {
    meta = `${member.owning_department_name || "未归属部门"} · ${AGENT_ARCHETYPE_LABELS[member.service_archetype] || member.service_archetype}`;
    trailing = displayRuntimeName(member.active_runtime_name || "未部署");
  }
  const status = resourceType === "server" ? SERVER_STATUS_LABELS[member.lifecycle_status] : resourceType === "agent" ? AGENT_STATUS_LABELS[member.lifecycle_status] : member.lifecycle_status;
  return <div><span className={`group-member-icon group-member-icon--${resourceType}`}><Icon size={17} /></span><span><strong>{resourceType === "runtime" ? displayRuntimeName(member.name) : member.name}</strong><small>{meta}</small></span><span className="group-member-meta"><em>{status || "未知"}</em><small>{trailing}</small></span></div>;
}

export function GroupManagement({ servers, agentMap, runtimeMap, dataSource, requestJson, canManage = true }) {
  const agents = useMemo(() => flattenAgentMap(agentMap, runtimeMap), [agentMap, runtimeMap]);
  const runtimes = useMemo(() => flattenRuntimeMap(runtimeMap, servers), [runtimeMap, servers]);
  const demoServers = useMemo(() => servers.map((server) => ({ ...server, runtimes: runtimeMap[server.server_id] || [] })), [runtimeMap, servers]);
  const demoGroups = useMemo(() => buildDemoGroups(demoServers, agents, runtimes), [agents, demoServers, runtimes]);
  const [groups, setGroups] = useState(demoGroups);
  const [query, setQuery] = useState("");
  const [resourceType, setResourceType] = useState("all");
  const [lifecycle, setLifecycle] = useState("all");
  const [selectedId, setSelectedId] = useState(demoGroups[0]?.group_id || null);
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState("ready");
  const [dialog, setDialog] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshGroups = useCallback(async () => {
    if (dataSource !== "api") { setGroups(demoGroups); return demoGroups; }
    const response = await requestJson("/api/v1/groups?limit=100");
    setGroups(response.data);
    return response.data;
  }, [dataSource, demoGroups, requestJson]);

  useEffect(() => { refreshGroups().catch(() => setGroups(demoGroups)); }, [demoGroups, refreshGroups]);
  useEffect(() => { if (!groups.some((group) => group.group_id === selectedId)) setSelectedId(groups[0]?.group_id || null); }, [groups, selectedId]);

  const loadDetail = useCallback(async (groupId = selectedId) => {
    const group = groups.find((item) => item.group_id === groupId);
    if (!group) { setDetail(null); return; }
    if (dataSource !== "api") { setDetail(buildDemoGroupDetail(group, demoServers, agents, demoGroups)); setDetailState("ready"); return; }
    setDetailState("loading");
    try { setDetail((await requestJson(`/api/v1/groups/${group.group_id}`)).data); setDetailState("ready"); }
    catch { setDetail(buildDemoGroupDetail(group, demoServers, agents, demoGroups)); setDetailState("fallback"); }
  }, [agents, dataSource, demoGroups, demoServers, groups, requestJson, selectedId]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  const loadCandidates = async (type) => {
    setCandidatesLoading(true);
    try {
      if (dataSource !== "api") {
        setCandidates(type === "server" ? servers : type === "runtime" ? runtimes : agents.filter((item) => item.structure_role === "primary"));
      } else {
        const suffix = type === "agent" ? "?structure_role=primary&limit=100" : "?limit=100";
        setCandidates((await requestJson(`/api/v1/${type === "agent" ? "agents" : `${type}s`}${suffix}`)).data);
      }
    } finally { setCandidatesLoading(false); }
  };

  const openDialog = async (type) => {
    if (!canManage) { setNotice("当前账号为只读权限，不能维护管理分组。"); return; }
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后维护分组。"); return; }
    setFormError("");
    const targetType = type === "edit" ? detail?.resource_type : "server";
    if (!targetType) return;
    try { await loadCandidates(targetType); setDialog(type); }
    catch (error) { setNotice(error.message); }
  };

  const saveGroup = async (payload) => {
    setSaving(true); setFormError("");
    try {
      const editing = dialog === "edit";
      const response = await requestJson(editing ? `/api/v1/groups/${detail.group_id}` : "/api/v1/groups", { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
      setSelectedId(response.data.group_id); setDetail(response.data); setDialog(null);
      await refreshGroups();
      setNotice(editing ? "管理分组已更新" : "管理分组已创建");
    } catch (error) { setFormError(error.message); }
    finally { setSaving(false); }
  };

  const retire = async () => {
    if (!detail || !window.confirm(`确认退役分组“${detail.name}”？当前成员会同时移出，历史记录保留。`)) return;
    try {
      const response = await requestJson(`/api/v1/groups/${detail.group_id}?row_version=${detail.row_version}`, { method: "DELETE", confirmed: true });
      setDetail(response.data); await refreshGroups(); setNotice("管理分组已退役");
    } catch (error) { setNotice(error.message); }
  };

  const visibleGroups = useMemo(() => filterGroups(groups, query, resourceType, lifecycle), [groups, query, resourceType, lifecycle]);
  const counts = Object.fromEntries(["server", "runtime", "agent"].map((type) => [type, groups.filter((group) => group.resource_type === type).length]));

  return <section className="asset-page" aria-labelledby="group-page-title">
    <div className="asset-page-heading"><div><span className="eyebrow">批量管理目标集</span><h2 id="group-page-title">管理分组</h2><p>维护静态成员，在资产页面按分组筛选后执行批量管理。</p></div><div className="group-heading-actions">{canManage && <button className="secondary-action" onClick={() => openDialog("create")}><Plus size={17} />新建分组</button>}<div className="group-page-totals"><div><HardDrives size={18} /><span><strong>{counts.server}</strong>服务器</span></div><div><Cube size={18} /><span><strong>{counts.runtime}</strong>运行时</span></div><div><Robot size={18} /><span><strong>{counts.agent}</strong>智能体</span></div></div></div></div>
    {notice && <div className="management-notice"><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭提示"><X size={14} /></button></div>}
    <div className="group-concept-note"><Info size={17} /><span><strong>静态目标集</strong>分组只用于筛选和批量操作，不表达主从、高可用或网络拓扑；同一资源可以加入多个分组。</span></div>
    <div className="asset-toolbar group-toolbar"><label className="asset-search"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索分组名称、编码或用途" /></label><label className="asset-filter"><span>资源</span><select value={resourceType} onChange={(event) => setResourceType(event.target.value)}><option value="all">全部资源</option><option value="server">服务器</option><option value="runtime">运行时</option><option value="agent">智能体</option></select></label><label className="asset-filter"><span>状态</span><select value={lifecycle} onChange={(event) => setLifecycle(event.target.value)}><option value="all">全部状态</option>{Object.entries(GROUP_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><div className="toolbar-summary">当前显示 <strong>{visibleGroups.length}</strong> 条</div></div>
    <div className="asset-layout group-asset-layout"><div className="asset-list-card"><div className="group-list-head"><span>管理分组</span><span>资源类型</span><span>用途</span><span>当前成员</span><span>状态</span><span /></div><div className="asset-list-body">{visibleGroups.map((group) => <button className={selectedId === group.group_id ? "group-list-row group-list-row--selected" : "group-list-row"} key={group.group_id} onClick={() => setSelectedId(group.group_id)}><span className="server-identity"><FolderSimple size={19} /><span><strong>{group.name}</strong><small>{group.group_code}</small></span></span><ResourceBadge type={group.resource_type} /><span className="group-purpose">{group.purpose || "未说明用途"}</span><span className="group-member-count"><Users size={15} /><strong>{group.current_members || 0}</strong></span><GroupLifecycle status={group.lifecycle_status} /><CaretRight size={16} /></button>)}{!visibleGroups.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配分组</strong><span>请调整搜索词或筛选条件。</span></div>}</div></div>
      <aside className="asset-detail-card group-detail-panel" aria-label="管理分组详情">{detailState === "loading" ? <div className="detail-loading"><ArrowsClockwise size={28} className="spin" /><span>正在读取分组详情</span></div> : detail ? <><div className="detail-header"><div className="detail-title"><span className={`detail-icon detail-icon--group-${detail.resource_type}`}><SquaresFour size={22} /></span><div><h3>{detail.name}</h3><span>{detail.group_code}</span></div></div><GroupLifecycle status={detail.lifecycle_status} /></div>{canManage && <div className="server-detail-actions"><button onClick={() => openDialog("edit")} disabled={detail.lifecycle_status === "retired"}><PencilSimple size={15} />编辑成员</button><button className="danger-action" onClick={retire} disabled={detail.lifecycle_status === "retired"}><Trash size={15} />退役</button></div>}{detailState === "fallback" && <div className="detail-notice">本机 API 详情暂不可用，当前展示模拟成员。</div>}<div className="group-detail-summary"><ResourceBadge type={detail.resource_type} /><span><Tag size={14} />{detail.purpose || "未说明用途"}</span><em>v{detail.row_version || 1}</em></div><div className="detail-section"><div className="detail-section-title"><h4>当前成员</h4><span>{detail.members?.length || 0}</span></div><div className="group-member-list">{detail.members?.map((member) => <GroupMember key={member.member_id} resourceType={detail.resource_type} member={member} />)}{!detail.members?.length && <div className="group-empty-members"><TreeStructure size={25} /><span>当前没有有效成员</span></div>}</div></div><div className="group-static-note"><Info size={15} /><span>成员由管理员明确维护；名称、IP、部署关系变化不会自动改变分组。</span></div></> : <div className="detail-loading"><SquaresFour size={28} /><span>选择一个分组查看成员</span></div>}</aside>
    </div>
    {dialog === "create" && <Dialog title="新建管理分组" onClose={() => setDialog(null)}><GroupForm candidates={candidates} candidatesLoading={candidatesLoading} saving={saving} error={formError} onCancel={() => setDialog(null)} onResourceChange={loadCandidates} onSave={saveGroup} /></Dialog>}
    {dialog === "edit" && detail && <Dialog title="编辑管理分组" onClose={() => setDialog(null)}><GroupForm group={detail} candidates={candidates} candidatesLoading={candidatesLoading} saving={saving} error={formError} onCancel={() => setDialog(null)} onResourceChange={loadCandidates} onSave={saveGroup} /></Dialog>}
  </section>;
}

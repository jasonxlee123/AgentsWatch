import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddressBook,
  Buildings,
  CaretRight,
  FloppyDisk,
  IdentificationBadge,
  Info,
  LockKey,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Prohibit,
  TreeStructure,
  UserCircle,
  UsersThree,
  X,
} from "@phosphor-icons/react";
import {
  buildDepartmentPayload,
  buildPersonPayload,
  EMPLOYMENT_STATUS_LABELS,
  filterDepartments,
  filterPeople,
  loadPaginatedItems,
  organizationOptionLabel,
  selectableOrganizationItems,
  summarizeDepartmentRelationships,
  summarizePersonRelationships,
} from "./organizationUtils.js";

const EMPTY_DEPARTMENT = { department_code: "", name: "", parent_department_id: "", is_active: true };
const EMPTY_PERSON = { employee_no: "", display_name: "", primary_department_id: "", employment_status: "active" };

function formatTime(value) {
  if (!value) return "未记录";
  return String(value).replace("T", " ").replace(/([+-]\d{2}:\d{2}|Z)$/, "").slice(0, 19);
}

function Dialog({ title, children, onClose }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="management-dialog organization-dialog" role="dialog" aria-modal="true" aria-label={title}><header><h3>{title}</h3><button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button></header>{children}</section></div>;
}

function DepartmentForm({ department, departments, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => department ? {
    department_code: department.department_code,
    name: department.name,
    parent_department_id: department.parent_department_id || "",
    is_active: department.is_active,
  } : EMPTY_DEPARTMENT);
  const editing = Boolean(department);
  const parents = selectableOrganizationItems(
    departments.filter((item) => item.department_id !== department?.department_id),
    "department_id",
    (item) => item.is_active,
    [form.parent_department_id],
  );
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = (event) => { event.preventDefault(); onSave(buildDepartmentPayload(form, department)); };

  return <form className="organization-form" onSubmit={submit}><div className="management-form-grid">
    <label className="management-field"><span>部门编码</span><input required readOnly={editing} value={form.department_code} onChange={(event) => set("department_code", event.target.value)} placeholder="例如 DEP-DIGITAL" />{editing && <small><LockKey size={12} />创建后不可修改，用于稳定引用。</small>}</label>
    <label className="management-field"><span>部门名称</span><input required value={form.name} onChange={(event) => set("name", event.target.value)} placeholder="例如 示例业务部门" /></label>
    <label className="management-field management-field--wide"><span>上级部门</span><select value={form.parent_department_id} onChange={(event) => set("parent_department_id", event.target.value)}><option value="">无上级部门</option>{parents.map((item) => <option key={item.department_id} value={item.department_id}>{organizationOptionLabel(item, `${item.name} · ${item.department_code}`)}</option>)}</select><small>不能选择自身或形成循环层级。</small></label>
    <label className="management-field management-field--wide"><span>状态</span><select value={form.is_active ? "active" : "inactive"} onChange={(event) => set("is_active", event.target.value === "active")}><option value="active">启用</option><option value="inactive">停用</option></select><small>停用后不再提供给新建关系选择，已有历史关系仍会保留。</small></label>
  </div>{error && <div className="management-form-error">{error}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存部门"}</button></footer></form>;
}

function PersonForm({ person, departments, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => person ? {
    employee_no: person.employee_no,
    display_name: person.display_name,
    primary_department_id: person.primary_department_id || "",
    employment_status: person.employment_status,
  } : EMPTY_PERSON);
  const editing = Boolean(person);
  const availableDepartments = selectableOrganizationItems(departments, "department_id", (item) => item.is_active, [form.primary_department_id]);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = (event) => { event.preventDefault(); onSave(buildPersonPayload(form, person)); };

  return <form className="organization-form" onSubmit={submit}><div className="management-form-grid">
    <label className="management-field"><span>人员工号</span><input required readOnly={editing} value={form.employee_no} onChange={(event) => set("employee_no", event.target.value)} placeholder="例如 DEMO-EMP-001" />{editing && <small><LockKey size={12} />创建后不可修改，前导零会保留。</small>}</label>
    <label className="management-field"><span>人员姓名</span><input required value={form.display_name} onChange={(event) => set("display_name", event.target.value)} placeholder="例如 示例人员甲" /></label>
    <label className="management-field management-field--wide"><span>所属部门</span><select value={form.primary_department_id} onChange={(event) => set("primary_department_id", event.target.value)}><option value="">暂不归属部门</option>{availableDepartments.map((item) => <option key={item.department_id} value={item.department_id}>{organizationOptionLabel(item, `${item.name} · ${item.department_code}`)}</option>)}</select></label>
    <label className="management-field management-field--wide"><span>人员状态</span><select value={form.employment_status} onChange={(event) => set("employment_status", event.target.value)}>{Object.entries(EMPLOYMENT_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><small>停用或离职人员不再提供给新建智能体关系选择，已有关系不会被静默删除。</small></label>
  </div>{error && <div className="management-form-error">{error}</div>}<footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存人员"}</button></footer></form>;
}

function StatusBadge({ active, personStatus }) {
  const enabled = personStatus ? personStatus === "active" : active;
  const label = personStatus ? EMPLOYMENT_STATUS_LABELS[personStatus] || personStatus : active ? "启用" : "停用";
  return <span className={`organization-status ${enabled ? "organization-status--active" : "organization-status--inactive"}`}><i />{label}</span>;
}

export function OrganizationManagement({ requestJson, canManage = true }) {
  const [tab, setTab] = useState("departments");
  const [departments, setDepartments] = useState([]);
  const [people, setPeople] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selectedDepartmentId, setSelectedDepartmentId] = useState(null);
  const [selectedPersonId, setSelectedPersonId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState("loading");
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [pageError, setPageError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const [departmentRows, personRows] = await Promise.all([
      loadPaginatedItems(requestJson, "/api/v1/departments", "departments"),
      loadPaginatedItems(requestJson, "/api/v1/people", "people"),
    ]);
    setDepartments(departmentRows);
    setPeople(personRows);
    setSelectedDepartmentId((current) => departmentRows.some((item) => item.department_id === current) ? current : departmentRows[0]?.department_id || null);
    setSelectedPersonId((current) => personRows.some((item) => item.person_id === current) ? current : personRows[0]?.person_id || null);
    setPageError("");
    return { departments: departmentRows, people: personRows };
  }, [requestJson]);

  useEffect(() => { load().catch((error) => { setPageError(error.message); setDetailState("error"); }); }, [load]);

  const visibleDepartments = useMemo(() => filterDepartments(departments, query, status), [departments, query, status]);
  const visiblePeople = useMemo(() => filterPeople(people, query, status), [people, query, status]);

  useEffect(() => {
    if (tab === "departments" && !visibleDepartments.some((item) => item.department_id === selectedDepartmentId)) setSelectedDepartmentId(visibleDepartments[0]?.department_id || null);
    if (tab === "people" && !visiblePeople.some((item) => item.person_id === selectedPersonId)) setSelectedPersonId(visiblePeople[0]?.person_id || null);
  }, [selectedDepartmentId, selectedPersonId, tab, visibleDepartments, visiblePeople]);

  const selectedDepartment = departments.find((item) => item.department_id === selectedDepartmentId) || null;
  const selectedPerson = people.find((item) => item.person_id === selectedPersonId) || null;

  useEffect(() => {
    const selected = tab === "departments" ? selectedDepartment : selectedPerson;
    if (!selected) { setDetail(null); setDetailState("ready"); return undefined; }
    let cancelled = false;
    setDetail(selected); setDetailState("loading");
    const path = tab === "departments" ? `/api/v1/departments/${selected.department_id}` : `/api/v1/people/${selected.person_id}`;
    requestJson(path).then((response) => { if (!cancelled) { setDetail(response.data); setDetailState("ready"); } }).catch(() => { if (!cancelled) { setDetail(selected); setDetailState("fallback"); } });
    return () => { cancelled = true; };
  }, [requestJson, selectedDepartment, selectedPerson, tab]);

  function switchTab(next) {
    setTab(next); setQuery(""); setStatus("all"); setPageError(""); setDetail(null); setDetailState("loading");
  }

  function openCreate() {
    if (!canManage) { setNotice("当前账号为只读权限，不能新增组织档案。"); return; }
    setFormError(""); setDialog({ type: tab === "departments" ? "department" : "person", item: null });
  }

  function openEdit(item) {
    if (!canManage) { setNotice("当前账号为只读权限，不能修改组织档案。"); return; }
    setFormError(""); setDialog({ type: tab === "departments" ? "department" : "person", item });
  }

  async function save(payload) {
    setSaving(true); setFormError("");
    const type = dialog.type;
    const item = dialog.item;
    const base = type === "department" ? "/api/v1/departments" : "/api/v1/people";
    try {
      await requestJson(item ? `${base}/${type === "department" ? item.department_id : item.person_id}` : base, {
        method: item ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      const rows = await load();
      const createdOrUpdated = type === "department"
        ? rows.departments.find((entry) => entry.department_code === (item?.department_code || payload.department_code))
        : rows.people.find((entry) => entry.employee_no === (item?.employee_no || payload.employee_no));
      if (type === "department") setSelectedDepartmentId(createdOrUpdated?.department_id || null);
      else setSelectedPersonId(createdOrUpdated?.person_id || null);
      setNotice(`${type === "department" ? "部门" : "人员"}档案已${item ? "更新" : "创建"}。`);
      setDialog(null);
    } catch (error) { setFormError(error.message); }
    finally { setSaving(false); }
  }

  async function deactivate(item) {
    if (!canManage) { setNotice("当前账号为只读权限，不能停用组织档案。"); return; }
    const type = tab === "departments" ? "部门" : "人员";
    if (!window.confirm(`确认停用${type}“${item.name || item.display_name}”？已有智能体历史关系将继续保留。`)) return;
    setPageError("");
    try {
      const department = tab === "departments";
      await requestJson(`/api/v1/${department ? "departments" : "people"}/${department ? item.department_id : item.person_id}`, {
        method: "PATCH",
        body: JSON.stringify(department ? { row_version: item.row_version, is_active: false } : { row_version: item.row_version, employment_status: "inactive" }),
      });
      await load();
      setNotice(`${type}已停用，新建关系不再提供该选项。`);
    } catch (error) { setPageError(error.message); }
  }

  const activeDepartmentCount = departments.filter((item) => item.is_active).length;
  const activePersonCount = people.filter((item) => item.employment_status === "active").length;
  const visible = tab === "departments" ? visibleDepartments : visiblePeople;
  const detailMatchesSelection = tab === "departments"
    ? detail?.department_id === selectedDepartmentId
    : detail?.person_id === selectedPersonId;
  const selected = detailMatchesSelection ? detail : (tab === "departments" ? selectedDepartment : selectedPerson);
  const parentDepartment = selectedDepartment ? departments.find((item) => item.department_id === selectedDepartment.parent_department_id) : null;
  const personDepartment = selectedPerson ? departments.find((item) => item.department_id === selectedPerson.primary_department_id) : null;
  const childDepartmentCount = selectedDepartment ? departments.filter((item) => item.parent_department_id === selectedDepartment.department_id).length : 0;
  const departmentPeopleCount = selectedDepartment ? people.filter((item) => item.primary_department_id === selectedDepartment.department_id).length : 0;
  const departmentRelationships = tab === "departments" ? selected?.agent_relationships || [] : [];
  const personRelationships = tab === "people" ? selected?.agent_relationships || [] : [];
  const hasDepartmentRelationshipDetail = tab === "departments" && Array.isArray(selected?.agent_relationships);
  const hasPersonRelationshipDetail = tab === "people" && Array.isArray(selected?.agent_relationships);
  const departmentRelationshipCounts = summarizeDepartmentRelationships(departmentRelationships);
  const personRelationshipCounts = summarizePersonRelationships(personRelationships);

  return <section className="asset-page organization-page" aria-labelledby="organization-page-title">
    <header className="asset-page-heading"><div><span className="eyebrow">组织主数据</span><h2 id="organization-page-title">组织管理</h2><p>维护智能体归属、服务对象、管理员和可访问人员所引用的部门与人员档案。</p></div><button className="secondary-action" onClick={openCreate} disabled={!canManage}><Plus size={17} />新增{tab === "departments" ? "部门" : "人员"}</button></header>

    <div className="organization-boundary-note"><Info size={19} /><div><strong>人员档案不等于登录用户</strong><span>这里维护的是组织关系数据；能够登录 AgentsWatch 的账号及角色仍在“用户权限”中单独管理，两者不会自动绑定。</span></div></div>
    {notice && <div className="management-notice"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="关闭提示"><X size={14} /></button></div>}
    {pageError && <div className="management-form-error organization-page-error">{pageError}</div>}

    <div className="organization-metrics"><div><Buildings size={23} /><span><strong>{departments.length}</strong>部门档案<small>{activeDepartmentCount} 个启用</small></span></div><div><UsersThree size={23} /><span><strong>{people.length}</strong>人员档案<small>{activePersonCount} 人在职</small></span></div></div>
    <div className="organization-tabs" role="tablist" aria-label="组织管理对象"><button role="tab" aria-selected={tab === "departments"} className={tab === "departments" ? "organization-tab organization-tab--active" : "organization-tab"} onClick={() => switchTab("departments")}><Buildings size={17} />部门管理<span>{departments.length}</span></button><button role="tab" aria-selected={tab === "people"} className={tab === "people" ? "organization-tab organization-tab--active" : "organization-tab"} onClick={() => switchTab("people")}><UsersThree size={17} />人员管理<span>{people.length}</span></button></div>

    <div className="asset-toolbar organization-toolbar"><label className="asset-search"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "departments" ? "搜索部门名称、编码或上级部门" : "搜索姓名、工号或所属部门"} /></label><label className="asset-filter"><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}>{tab === "departments" ? <><option value="all">全部状态</option><option value="active">启用</option><option value="inactive">停用</option></> : <><option value="all">全部状态</option>{Object.entries(EMPLOYMENT_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</>}</select></label><div className="toolbar-summary">当前显示 <strong>{visible.length}</strong> 条</div></div>

    <div className="organization-layout">
      <div className="organization-list-card">{tab === "departments" ? <><div className="organization-list-head organization-department-grid"><span>部门</span><span>上级部门</span><span>在职人员</span><span>状态</span><span /></div><div className="organization-list-body">{visibleDepartments.map((department) => <button key={department.department_id} className={department.department_id === selectedDepartmentId ? "organization-list-row organization-list-row--selected organization-department-grid" : "organization-list-row organization-department-grid"} onClick={() => setSelectedDepartmentId(department.department_id)}><span className="organization-identity"><i><Buildings size={18} /></i><span><strong>{department.name}</strong><small>{department.department_code}</small></span></span><span>{department.parent_department_name || departments.find((item) => item.department_id === department.parent_department_id)?.name || "—"}</span><span>{department.active_person_count ?? people.filter((item) => item.primary_department_id === department.department_id && item.employment_status === "active").length} 人</span><StatusBadge active={department.is_active} /><CaretRight size={16} /></button>)}{!visibleDepartments.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配部门</strong><span>请调整搜索词或状态筛选。</span></div>}</div></> : <><div className="organization-list-head organization-person-grid"><span>人员</span><span>所属部门</span><span>状态</span><span>更新时间</span><span /></div><div className="organization-list-body">{visiblePeople.map((person) => <button key={person.person_id} className={person.person_id === selectedPersonId ? "organization-list-row organization-list-row--selected organization-person-grid" : "organization-list-row organization-person-grid"} onClick={() => setSelectedPersonId(person.person_id)}><span className="organization-identity organization-identity--person"><i><UserCircle size={19} /></i><span><strong>{person.display_name}</strong><small>{person.employee_no}</small></span></span><span>{person.department_name || departments.find((item) => item.department_id === person.primary_department_id)?.name || "未归属"}</span><StatusBadge personStatus={person.employment_status} /><span>{formatTime(person.updated_at).slice(0, 10)}</span><CaretRight size={16} /></button>)}{!visiblePeople.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配人员</strong><span>请调整搜索词或状态筛选。</span></div>}</div></>}</div>

      <aside className="organization-detail-card" aria-label={tab === "departments" ? "部门详情" : "人员详情"}>{selected ? <>{detailState === "loading" && <div className="organization-detail-loading">正在刷新详情…</div>}<div className="organization-detail-header"><div className={`organization-detail-icon organization-detail-icon--${tab === "departments" ? "department" : "person"}`}>{tab === "departments" ? <Buildings size={23} /> : <UserCircle size={24} />}</div><div><h3>{tab === "departments" ? selected.name : selected.display_name}</h3><span>{tab === "departments" ? selected.department_code : selected.employee_no}</span></div><StatusBadge active={selected.is_active} personStatus={selected.employment_status} /></div>{detailState === "fallback" && <div className="detail-notice">详情接口暂不可用，当前展示列表中的完整档案。</div>}<div className="organization-detail-actions"><button onClick={() => openEdit(selected)} disabled={!canManage}><PencilSimple size={15} />编辑</button>{(tab === "departments" ? selected.is_active : selected.employment_status === "active") && <button className="organization-stop-action" onClick={() => deactivate(selected)} disabled={!canManage}><Prohibit size={15} />停用</button>}</div>
        {tab === "departments" ? <><section className="organization-detail-section"><h4>部门定义</h4><div className="organization-field-grid"><div><IdentificationBadge size={17} /><span><small>部门编码</small><strong>{selected.department_code}</strong></span></div><div><TreeStructure size={17} /><span><small>上级部门</small><strong>{selected.parent_department_name || parentDepartment?.name || "无上级部门"}</strong></span></div><div><AddressBook size={17} /><span><small>下级部门</small><strong>{selected.children?.length ?? selected.active_child_count ?? childDepartmentCount} 个</strong></span></div><div><UsersThree size={17} /><span><small>部门人员</small><strong>{selected.people?.length ?? selected.active_person_count ?? departmentPeopleCount} 人</strong></span></div></div></section><section className="organization-detail-section"><h4>关系引用</h4><div className="organization-reference-stats"><span><strong>{hasDepartmentRelationshipDetail ? departmentRelationshipCounts.owner : selected.active_agent_references ?? 0}</strong>{hasDepartmentRelationshipDetail ? "归属智能体" : "关联智能体"}</span><span><strong>{hasDepartmentRelationshipDetail ? departmentRelationshipCounts.service : "—"}</strong>服务对象关系</span></div>{departmentRelationships.length > 0 && <div className="organization-relationship-list">{departmentRelationships.slice(0, 4).map((relationship, index) => <span key={`${relationship.agent_id}-${relationship.role}-${index}`}><strong>{relationship.name}</strong><small>{relationship.role === "owner" ? "归属部门" : relationship.role === "service_target" ? "服务对象" : "归属及服务对象"}</small></span>)}</div>}<p>停用前需先处理在用关系；已结束的历史关系不会被物理删除。</p></section></> : <><section className="organization-detail-section"><h4>人员档案</h4><div className="organization-field-grid"><div><IdentificationBadge size={17} /><span><small>人员工号</small><strong>{selected.employee_no}</strong></span></div><div><Buildings size={17} /><span><small>所属部门</small><strong>{selected.department_name || personDepartment?.name || "未归属部门"}</strong></span></div></div></section><section className="organization-detail-section"><h4>智能体关系</h4><div className="organization-reference-stats"><span><strong>{hasPersonRelationshipDetail ? personRelationshipCounts.admin : selected.active_agent_references ?? 0}</strong>{hasPersonRelationshipDetail ? "管理员记录" : "关联智能体"}</span><span><strong>{hasPersonRelationshipDetail ? personRelationshipCounts.accessible : "—"}</strong>可访问关系</span><span><strong>{hasPersonRelationshipDetail ? personRelationshipCounts.service : "—"}</strong>服务对象关系</span></div>{personRelationships.length > 0 && <div className="organization-relationship-list">{personRelationships.slice(0, 4).map((relationship, index) => <span key={`${relationship.agent_id}-${relationship.role}-${index}`}><strong>{relationship.name}</strong><small>{relationship.role === "business_admin" ? "业务管理员" : relationship.role === "it_admin" ? "IT 管理员" : relationship.role === "accessible_person" ? "可访问用户" : "主要服务人员"}</small></span>)}</div>}<p>人员档案只记录业务组织关系，不授予 AgentsWatch 登录权限；停用前需先转移在用关系。</p></section></>}
        <section className="organization-detail-section organization-record-meta"><h4>记录信息</h4><div><span>数据版本</span><strong>v{selected.row_version ?? 1}</strong></div><div><span>最后更新</span><strong>{formatTime(selected.updated_at)}</strong></div></section></> : <div className="detail-loading">{tab === "departments" ? <Buildings size={29} /> : <UsersThree size={29} />}<span>选择一条档案查看详情</span></div>}</aside>
    </div>

    {dialog?.type === "department" && <Dialog title={dialog.item ? "编辑部门" : "新增部门"} onClose={() => setDialog(null)}><DepartmentForm department={dialog.item} departments={departments} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={save} /></Dialog>}
    {dialog?.type === "person" && <Dialog title={dialog.item ? "编辑人员" : "新增人员"} onClose={() => setDialog(null)}><PersonForm person={dialog.item} departments={departments} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={save} /></Dialog>}
  </section>;
}

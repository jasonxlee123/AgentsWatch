import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddressBook,
  ArrowRight,
  ArrowsClockwise,
  ArrowsLeftRight,
  Briefcase,
  Buildings,
  CaretRight,
  CheckCircle,
  Cube,
  FloppyDisk,
  Globe,
  IdentificationBadge,
  LockKey,
  LinkBreak,
  MagnifyingGlass,
  Network,
  PaperPlaneTilt,
  PencilSimple,
  Play,
  Plus,
  Robot,
  ShieldCheck,
  Trash,
  TreeStructure,
  UserCircle,
  Users,
  UsersThree,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { displayRuntimeName } from "./serverUtils.js";
import { AGENT_ARCHETYPE_LABELS, AGENT_ROLE_LABELS, AGENT_STATUS_LABELS, buildDemoAgentDetail, filterAgents, flattenAgentMap } from "./agentUtils.js";
import { agentFormDefaults, buildAgentPayload, feishuSecretRequired } from "./agentFormUtils.js";
import {
  agentDeploymentDefaults,
  agentDeploymentOperationCode,
  agentDeploymentRecoveryStatus,
  agentDeploymentResultMessage,
  availableAgentDeploymentActions,
  buildAgentDeploymentPayload,
  currentRuntimeDeploymentBlockReason,
  deploymentRuntimeKeyNeedsResolution,
  eligibleAgentRuntimes,
  hasUnsupportedImportedChildMembers,
  isLegacyUnmanagedDeployment,
  runtimeCandidateEmptyReason,
} from "./agentDeploymentUtils.js";
import { loadPaginatedItems, organizationOptionLabel, selectableOrganizationItems } from "./organizationUtils.js";
import { buildDemoGroups, filterAssetsByGroup } from "./groupUtils.js";

const AGENT_OPERATION_LABELS = {
  "agent.apply_deployment": "部署或同步智能体",
  "agent.migrate_deployment": "更换运行时",
  "agent.remove_deployment": "解除运行时部署",
};

function AgentLifecycle({ status = "active" }) {
  const mapped = status === "testing" ? "planned" : status === "suspended" ? "maintenance" : status;
  return <span className={`asset-status asset-status--${mapped}`}><i />{AGENT_STATUS_LABELS[status] || status}</span>;
}

function RoleBadge({ role }) {
  return <span className={`role-badge role-badge--${role}`}><Robot size={13} />{AGENT_ROLE_LABELS[role] || role}</span>;
}

function ArchetypeBadge({ archetype }) {
  return <span className={`archetype-badge archetype-badge--${archetype}`}>{AGENT_ARCHETYPE_LABELS[archetype] || archetype}</span>;
}

function InfoField({ icon: Icon, label, value }) {
  return <div className="detail-field"><Icon size={18} weight="regular" /><div><span>{label}</span><strong>{value || "未配置"}</strong></div></div>;
}

function Dialog({ title, children, onClose }) {
  return <div className="management-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="management-dialog management-dialog--wide" role="dialog" aria-modal="true" aria-label={title}><header><h3>{title}</h3><button type="button" onClick={onClose} aria-label="关闭"><X size={19} /></button></header>{children}</section></div>;
}

function Field({ label, children, wide = false }) {
  return <label className={wide ? "management-field management-field--wide" : "management-field"}><span>{label}</span>{children}</label>;
}

function AgentForm({ agent, agents, referenceData, saving, error, onCancel, onSave }) {
  const [form, setForm] = useState(() => agentFormDefaults(agent, referenceData));
  const [applicationNameCustomized, setApplicationNameCustomized] = useState(() => Boolean(
    agent?.channel?.application_name && agent.channel.application_name !== agent.name,
  ));
  const primaryAgents = agents.filter((item) => item.structure_role === "primary" && item.lifecycle_status !== "retired");
  const people = selectableOrganizationItems(
    referenceData.people || [],
    "person_id",
    (item) => item.employment_status === "active",
    [form.business_admin_person_id, form.it_admin_person_id, form.primary_service_person_id, ...form.accessible_person_ids],
  );
  const departments = selectableOrganizationItems(
    referenceData.departments || [],
    "department_id",
    (item) => item.is_active,
    [form.owning_department_id, form.primary_service_department_id],
  );
  const groups = referenceData.agent_groups || [];
  const editing = Boolean(agent);
  const primary = form.structure_role === "primary";
  const needsAdmins = primary && ["testing", "active"].includes(form.lifecycle_status);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const selectFromSet = (name, id, checked) => setForm((current) => {
    const next = new Set(current[name]);
    checked ? next.add(id) : next.delete(id);
    return { ...current, [name]: next };
  });
  const changeName = (name) => setForm((current) => ({
    ...current,
    name,
    channel_application_name: applicationNameCustomized ? current.channel_application_name : name,
  }));
  const changeRole = (structureRole) => {
    const parent = primaryAgents[0];
    setForm((current) => ({
      ...current,
      structure_role: structureRole,
      parent_agent_id: structureRole === "subagent" ? parent?.agent_id || "" : "",
      service_archetype: structureRole === "subagent" ? parent?.service_archetype || "business" : current.service_archetype,
    }));
  };
  const changeParent = (parentAgentId) => {
    const parent = primaryAgents.find((item) => item.agent_id === parentAgentId);
    setForm((current) => ({ ...current, parent_agent_id: parentAgentId, service_archetype: parent?.service_archetype || current.service_archetype }));
  };
  const submit = (event) => {
    event.preventDefault();
    onSave(buildAgentPayload(form, agent));
  };

  return <form onSubmit={submit} className="agent-management-form">
    <div className="management-form-grid">
      <div className="agent-form-section-title">基本定义</div>
      <Field label="智能体名称" wide><input required value={form.name} onChange={(event) => changeName(event.target.value)} /></Field>
      <Field label="结构角色"><select disabled={editing} value={form.structure_role} onChange={(event) => changeRole(event.target.value)}><option value="primary">主智能体</option><option value="subagent">子智能体</option></select>{editing && <small>创建后结构角色不可变。</small>}</Field>
      {primary ? <Field label="服务类型"><select disabled={editing && agent.lifecycle_status !== "requested"} value={form.service_archetype} onChange={(event) => set("service_archetype", event.target.value)}>{Object.entries(AGENT_ARCHETYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field> : <Field label="所属主智能体"><select required disabled={editing} value={form.parent_agent_id} onChange={(event) => changeParent(event.target.value)}><option value="">请选择主智能体</option>{primaryAgents.map((item) => <option key={item.agent_id} value={item.agent_id}>{item.name} · {AGENT_ARCHETYPE_LABELS[item.service_archetype]}</option>)}</select><small>子智能体继承主智能体的服务类型和管理关系，但渠道独立配置。</small></Field>}
      <Field label="生命周期"><select value={form.lifecycle_status} onChange={(event) => set("lifecycle_status", event.target.value)}>{Object.entries(AGENT_STATUS_LABELS).filter(([value]) => value !== "retired").map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      {primary && <Field label="归属部门"><select value={form.owning_department_id} onChange={(event) => set("owning_department_id", event.target.value)}><option value="">未指定</option>{departments.map((item) => <option key={item.department_id} value={item.department_id}>{organizationOptionLabel(item, `${item.name} · ${item.department_code}`)}</option>)}</select></Field>}
      <Field label="说明" wide><textarea rows="3" value={form.description} onChange={(event) => set("description", event.target.value)} placeholder="说明智能体的职责和使用边界" /></Field>

      {primary && <><div className="agent-form-section-title">服务对象与管理记录</div>
        {form.service_archetype === "personal" && <Field label="主要服务人员"><select required value={form.primary_service_person_id} onChange={(event) => set("primary_service_person_id", event.target.value)}><option value="">请选择人员</option>{people.map((item) => <option key={item.person_id} value={item.person_id}>{organizationOptionLabel(item, `${item.display_name} · ${item.employee_no}`)}</option>)}</select></Field>}
        {form.service_archetype === "department" && <Field label="主要服务部门"><select required value={form.primary_service_department_id} onChange={(event) => set("primary_service_department_id", event.target.value)}><option value="">请选择部门</option>{departments.map((item) => <option key={item.department_id} value={item.department_id}>{organizationOptionLabel(item, `${item.name} · ${item.department_code}`)}</option>)}</select></Field>}
        {form.service_archetype === "business" && <><Field label="业务领域"><input required value={form.business_domain} onChange={(event) => set("business_domain", event.target.value)} placeholder="例如：合同管理" /></Field><Field label="业务场景"><input required value={form.business_scenario} onChange={(event) => set("business_scenario", event.target.value)} placeholder="例如：合同风险审查" /></Field></>}
        <Field label="业务管理员"><select required={needsAdmins} value={form.business_admin_person_id} onChange={(event) => set("business_admin_person_id", event.target.value)}><option value="">待确认</option>{people.map((item) => <option key={item.person_id} value={item.person_id}>{organizationOptionLabel(item, `${item.display_name} · ${item.employee_no}`)}</option>)}</select></Field>
        <Field label="IT 管理员"><select required={needsAdmins} value={form.it_admin_person_id} onChange={(event) => set("it_admin_person_id", event.target.value)}><option value="">待确认</option>{people.map((item) => <option key={item.person_id} value={item.person_id}>{organizationOptionLabel(item, `${item.display_name} · ${item.employee_no}`)}</option>)}</select></Field>
        <Field label="可访问用户" wide><div className="management-check-grid agent-check-grid">{people.map((person) => <label key={person.person_id}><input type="checkbox" checked={form.accessible_person_ids.has(person.person_id)} onChange={(event) => selectFromSet("accessible_person_ids", person.person_id, event.target.checked)} />{organizationOptionLabel(person, person.display_name)}<small>{person.employee_no}</small></label>)}{!people.length && <span>暂无可选人员</span>}</div></Field>
        <Field label="智能体管理分组" wide><div className="management-check-grid">{groups.map((group) => <label key={group.group_id}><input type="checkbox" checked={form.group_ids.has(group.group_id)} onChange={(event) => selectFromSet("group_ids", group.group_id, event.target.checked)} />{group.name}</label>)}{!groups.length && <span>暂无智能体管理分组</span>}</div></Field>
      </>}

      <div className="agent-form-section-title">渠道对接</div>
      <Field label="渠道"><select value={form.channel_type} onChange={(event) => set("channel_type", event.target.value)}><option value="none">不设置渠道</option><option value="feishu">飞书</option></select></Field>
      {form.channel_type === "feishu" && <><Field label="飞书应用名"><input required value={form.channel_application_name} onChange={(event) => { setApplicationNameCustomized(true); set("channel_application_name", event.target.value); }} placeholder={form.name || "默认使用智能体名称"} /><small>默认与智能体名称一致，可单独修改。</small></Field><Field label="AppID"><input required value={form.channel_app_id} onChange={(event) => set("channel_app_id", event.target.value)} autoComplete="off" /></Field><Field label="AppSecret"><input type="password" required={feishuSecretRequired(form, agent)} value={form.channel_app_secret} onChange={(event) => set("channel_app_secret", event.target.value)} autoComplete="new-password" placeholder={agent?.channel?.credential_configured ? "已加密保存；留空保持不变" : "请输入 AppSecret"} /><small>仅加密保存，保存后不再回显。</small></Field><div className="agent-channel-guidance"><LockKey size={17} /><span>每个智能体独立保存渠道配置。AppSecret 不进入列表、详情明文或变更审计；修改 AppID 时必须重新填写。</span></div></>}
    </div>
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" disabled={saving}><FloppyDisk size={16} />{saving ? "保存中" : "保存智能体"}</button></footer>
  </form>;
}

function OperationStatus({ operation }) {
  const operationStatus = operation.host_status || operation.status || operation.run_status;
  const running = ["queued", "running"].includes(operationStatus);
  const Icon = operationStatus === "succeeded" ? CheckCircle : running ? ArrowsClockwise : XCircle;
  return <span className={`operation-status operation-status--${operationStatus}`}><Icon size={14} className={running ? "spin" : ""} />{operationStatus === "succeeded" ? "成功" : running ? "执行中" : "失败"}</span>;
}

function formatOperationTime(value) {
  if (!value) return "等待执行";
  return String(value).replace("T", " ").replace(/([+-]\d{2}:\d{2}|Z)$/, "").slice(0, 19);
}

const DEPLOYMENT_ACTION_CONTENT = {
  deploy: {
    label: "首次部署",
    title: "创建运行时部署",
    description: "配置主智能体、未退役子智能体和渠道账户；目标运行时校验并重启成功后，平台才会确认部署关系。",
    submit: "开始部署",
  },
  sync: {
    label: "同步部署",
    title: "同步当前运行时",
    description: "把当前主子结构和渠道配置同步到已绑定运行时。配置校验后会重启运行时，当前会话可能短暂中断。",
    submit: "同步部署",
  },
  migrate: {
    label: "更换运行时",
    title: "受控更换运行时",
    description: "平台先在目标运行时预置配置但不启用渠道，再停用源渠道并启用目标；失败时自动恢复原部署。源工作区内容不会复制到目标，源端 identity 与工作区也不会删除。",
    submit: "确认更换",
  },
  remove: {
    label: "解除部署",
    title: "解除运行时部署",
    description: "平台撤销当前运行时上的渠道账户、路由和托管关系并重启运行时；OpenClaw 智能体 identity 与工作区保留，失败时部署关系仍保持有效。",
    submit: "确认解除",
  },
};

function DeploymentRuntimeCard({ label, runtime, fallback, emptyText }) {
  const name = runtime?.name || fallback?.runtime_name;
  const runtimeId = runtime?.runtime_id || fallback?.runtime_id;
  const serverName = runtime?.server_name || fallback?.server_name;
  const serverIp = runtime?.server_internal_ip || fallback?.server_internal_ip;
  return <div className={name ? "agent-runtime-route-card" : "agent-runtime-route-card agent-runtime-route-card--empty"}>
    <small>{label}</small>
    {name ? <><strong>{displayRuntimeName(name)}</strong><span>{serverName || "未关联服务器"}{serverIp ? ` · ${serverIp}` : ""}</span><em>{runtimeId}</em></> : <strong>{emptyText}</strong>}
  </div>;
}

function AgentDeploymentForm({ agent, runtimes, saving, error, onCancel, onSave }) {
  const deployment = agent.active_deployment;
  const runtimeKeyNeedsResolution = deploymentRuntimeKeyNeedsResolution(agent);
  const multiMemberAdoptionBlocked = hasUnsupportedImportedChildMembers(agent);
  const legacyMetadataOnlyRemoval = isLegacyUnmanagedDeployment(agent);
  const availableActions = availableAgentDeploymentActions(agent);
  const lifecycleAllowsDeployment = ["testing", "active"].includes(agent.lifecycle_status);
  const initialAction = availableActions[0] || "deploy";
  const [action, setAction] = useState(initialAction);
  const initialCandidates = eligibleAgentRuntimes(runtimes, deployment, initialAction);
  const [form, setForm] = useState(() => {
    const defaults = agentDeploymentDefaults(agent, deployment, agent.deployment_members || []);
    return { ...defaults, runtime_id: defaults.runtime_id || initialCandidates[0]?.runtime_id || "" };
  });
  const candidates = eligibleAgentRuntimes(runtimes, deployment, action);
  const currentRuntime = runtimes.find((item) => item.runtime_id === deployment?.runtime_id);
  const targetRuntime = runtimes.find((item) => item.runtime_id === form.runtime_id);
  const emptyReason = runtimeCandidateEmptyReason(runtimes, deployment, action);
  const stableChildren = (agent.subagents || []).filter((item) => item.lifecycle_status !== "retired");
  const requiresRiskAcknowledgement = ["migrate", "remove"].includes(action);
  const needsTarget = action !== "remove";
  const sourceBlockReason = currentRuntimeDeploymentBlockReason(runtimes, deployment);
  const canSubmit = !saving
    && availableActions.includes(action)
    && (!needsTarget || Boolean(form.runtime_id) && candidates.some((item) => item.runtime_id === form.runtime_id))
    && (!needsTarget || Boolean(form.runtime_agent_key.trim()))
    && (action !== "remove" || legacyMetadataOnlyRemoval || !sourceBlockReason)
    && (!requiresRiskAcknowledgement || form.risk_acknowledged);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const chooseAction = (nextAction) => {
    const nextCandidates = eligibleAgentRuntimes(runtimes, deployment, nextAction);
    setAction(nextAction);
    setForm((current) => ({
      ...current,
      runtime_id: nextAction === "sync" ? deployment?.runtime_id || "" : nextCandidates[0]?.runtime_id || "",
      risk_acknowledged: false,
    }));
  };
  const submit = (event) => {
    event.preventDefault();
    if (!canSubmit) return;
    onSave(action, buildAgentDeploymentPayload(form, action, deployment));
  };
  const actionContent = action === "remove" && legacyMetadataOnlyRemoval
    ? {
      ...DEPLOYMENT_ACTION_CONTENT.remove,
      title: "解除导入关系",
      description: "该历史关系从未完成 AgentsWatch 远端托管；本操作只关闭平台中的导入关系，不连接或修改运行时，也不触碰远端智能体身份、工作区或渠道。",
      submit: "确认解除关系",
    }
    : DEPLOYMENT_ACTION_CONTENT[action];

  return <form onSubmit={submit} className="agent-deployment-form">
    {deployment && <div className="agent-deployment-action-tabs" role="tablist" aria-label="运行时部署操作">
      {[["sync", Play], ["migrate", ArrowsLeftRight], ["remove", LinkBreak]].map(([value, Icon]) => <button type="button" role="tab" aria-selected={action === value} className={action === value ? `agent-deployment-action-tab agent-deployment-action-tab--${value} agent-deployment-action-tab--selected` : `agent-deployment-action-tab agent-deployment-action-tab--${value}`} key={value} onClick={() => chooseAction(value)} disabled={saving || !availableActions.includes(value)} title={multiMemberAdoptionBlocked && value !== "remove" ? "当前版本不支持多成员收编，可仅解除导入关系后重新部署" : value === "migrate" && runtimeKeyNeedsResolution ? "请先同步并确认真实 OpenClaw Agent ID" : undefined}><Icon size={17} /><span><strong>{DEPLOYMENT_ACTION_CONTENT[value].label}</strong><small>{value === "sync" ? "更新原运行时" : value === "migrate" ? "切换部署目标" : legacyMetadataOnlyRemoval ? "仅解除导入关系" : "结束托管关系"}</small></span></button>)}
    </div>}
    {deployment && !lifecycleAllowsDeployment && <div className="agent-deployment-lifecycle-note"><WarningCircle size={16} /><span>智能体当前为“{AGENT_STATUS_LABELS[agent.lifecycle_status] || agent.lifecycle_status}”，同步或更换前需先恢复为“运行中”；仍可解除现有部署关系。</span></div>}
    {multiMemberAdoptionBlocked ? <div className="agent-deployment-resolution-note"><WarningCircle size={16} /><span><strong>多成员导入关系暂不支持收编</strong>{legacyMetadataOnlyRemoval ? "当前历史关系含未解析子成员，本版本不能在此逐一确认 ID；可仅解除导入关系后重新部署。解除操作只关闭平台关系，不会连接或修改远端。" : "当前历史关系混合了已确认与未确认成员，暂无安全的自动同步、迁移或解除操作；请先通过运行时发现完成成员映射。"}</span></div> : runtimeKeyNeedsResolution && <div className="agent-deployment-resolution-note"><WarningCircle size={16} /><span><strong>导入关系待解析</strong>当前关系尚未确认真实 OpenClaw Agent ID；请先执行同步部署完成解析，之后才能更换运行时。平台不会根据智能体名称推断 ID。</span></div>}
    <div className={`agent-deployment-guidance agent-deployment-guidance--${action}`}>
      {action === "migrate" ? <ArrowsLeftRight size={21} /> : action === "remove" ? <LinkBreak size={21} /> : <Play size={21} weight="fill" />}
      <div><strong>{actionContent.title}</strong><span>{actionContent.description}</span></div>
    </div>
    <div className="management-form-grid">
      <div className="agent-runtime-route" aria-label="运行时关系变更">
        <DeploymentRuntimeCard label={deployment ? "当前运行时" : "当前关系"} runtime={currentRuntime} fallback={deployment} emptyText="尚未部署" />
        <ArrowRight size={20} />
        <DeploymentRuntimeCard
          label={action === "remove" ? "解除后" : action === "sync" ? "同步目标" : "目标运行时"}
          runtime={action === "remove" ? null : targetRuntime}
          fallback={action === "sync" ? deployment : null}
          emptyText={action === "remove" ? "不再绑定运行时" : "等待选择"}
        />
      </div>
      {needsTarget && action !== "sync" && <Field label="目标运行时" wide>
        <select required value={form.runtime_id} onChange={(event) => set("runtime_id", event.target.value)}><option value="">请选择运行时</option>{candidates.map((runtime) => <option key={runtime.runtime_id} value={runtime.runtime_id}>{displayRuntimeName(runtime.name)} · {runtime.server_name || "未关联服务器"}{runtime.server_internal_ip ? ` · ${runtime.server_internal_ip}` : ""}</option>)}</select>
        {emptyReason && <small className="agent-runtime-empty-reason">{emptyReason}</small>}
      </Field>}
      {action === "sync" && emptyReason && <div className="agent-runtime-empty-reason agent-runtime-empty-reason--block"><WarningCircle size={16} />{emptyReason}</div>}
      {action !== "remove" && <>
        <Field label={runtimeKeyNeedsResolution ? "现有 OpenClaw Agent ID" : "OpenClaw 智能体 ID"}><input required pattern="[a-z0-9][a-z0-9_-]{0,63}" disabled={Boolean(deployment) && !runtimeKeyNeedsResolution} value={form.runtime_agent_key} onChange={(event) => set("runtime_agent_key", event.target.value.toLowerCase())} placeholder={runtimeKeyNeedsResolution ? "请输入运行时中已存在的真实 Agent ID" : undefined} /><small>{runtimeKeyNeedsResolution ? "请从 OpenClaw 配置或运行时中核实后填写；平台会验证这个现有 ID，不会创建或根据名称猜测。同步成功后才会标记为已确认。" : deployment ? "部署期间保持稳定，不会因同步或迁移改变。" : "创建后保持稳定，只允许小写字母、数字、下划线和短横线。"}</small></Field>
        <Field label="默认模型"><input value={form.model} onChange={(event) => set("model", event.target.value)} placeholder="留空使用运行时默认模型" /><small>{action === "migrate" ? "留空时使用目标运行时的默认模型。" : "仅影响本次新建的 OpenClaw 智能体。"}</small></Field>
        <Field label="工作区根目录" wide><input value={form.workspace_root} onChange={(event) => set("workspace_root", event.target.value)} placeholder="留空使用运行时状态目录下的 workspaces" /><small>{runtimeKeyNeedsResolution ? "收编的现有主 Agent 保留运行时中的实际工作区；此根目录只用于本次新建的子 Agent，不会移动或替换现有主 Agent 工作区。" : action === "migrate" ? "这里只指定目标宿主服务器上的路径；平台不会复制源工作区内容，源工作区也不会删除。" : "必须是目标宿主服务器上的绝对路径；已有成员路径按受控流程处理。"}</small></Field>
      </>}
      <div className="agent-deployment-targets"><strong>{action === "remove" ? "本次解除对象" : "本次配置对象"}</strong><div><span><Robot size={14} />{agent.name}<small>主智能体</small></span>{stableChildren.map((child) => <span key={child.agent_id}><Robot size={14} />{child.name}<small>子智能体</small></span>)}</div></div>
      {action === "remove" && sourceBlockReason && !legacyMetadataOnlyRemoval && <div className="agent-runtime-empty-reason agent-runtime-empty-reason--block"><WarningCircle size={16} />{sourceBlockReason}</div>}
      {requiresRiskAcknowledgement && <label className={`agent-deployment-risk agent-deployment-risk--${action}`}>
        <WarningCircle size={18} />
        <input type="checkbox" checked={form.risk_acknowledged} onChange={(event) => set("risk_acknowledged", event.target.checked)} />
        <span><strong>需要风险确认</strong><small>{action === "migrate" ? "我确认切换渠道和重启相关运行时会造成短暂中断；失败时平台将恢复原部署。源工作区内容不会复制，源端 identity 与工作区不会删除。" : legacyMetadataOnlyRemoval ? "我确认本操作仅关闭平台中的导入关系，不连接或修改远端运行时，也不会撤销渠道、重启服务或处理远端身份与工作区。" : "我确认撤销渠道和重启当前运行时会造成短暂中断；智能体 identity 与工作区仍会保留。"}</small></span>
      </label>}
    </div>
    {error && <div className="management-form-error">{error}</div>}
    <footer className="management-dialog-actions"><button type="button" onClick={onCancel}>取消</button><button className={action === "remove" ? "danger-action" : "primary-action"} disabled={!canSubmit}>{action === "migrate" ? <ArrowsLeftRight size={16} /> : action === "remove" ? <LinkBreak size={16} /> : <Play size={16} />}{saving ? "任务下发中" : actionContent.submit}</button></footer>
  </form>;
}

export function AgentManagement({ agentMap, runtimeMap, primaryTotal, dataSource, requestJson, onOverviewChanged, onOpenTask, canManage = true, canExecute = true }) {
  const demoAgents = useMemo(() => flattenAgentMap(agentMap, runtimeMap), [agentMap, runtimeMap]);
  const [agents, setAgents] = useState(demoAgents);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [archetype, setArchetype] = useState("all");
  const [agentGroups, setAgentGroups] = useState([]);
  const [selectedGroupId, setSelectedGroupId] = useState("all");
  const [groupMemberIds, setGroupMemberIds] = useState(null);
  const [selectedId, setSelectedId] = useState(demoAgents[0]?.agent_id || null);
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState("ready");
  const [referenceData, setReferenceData] = useState({ departments: [], people: [], agent_groups: [], runtimes: [] });
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [operationBusy, setOperationBusy] = useState(false);
  const [activeRun, setActiveRun] = useState(null);

  const refreshAgents = useCallback(async () => {
    if (dataSource !== "api") { setAgents(demoAgents); return demoAgents; }
    const response = await requestJson("/api/v1/agents?limit=100");
    setAgents(response.data);
    return response.data;
  }, [dataSource, demoAgents, requestJson]);

  useEffect(() => { refreshAgents().catch(() => setAgents(demoAgents)); }, [refreshAgents, demoAgents]);

  useEffect(() => {
    if (!agents.some((agent) => agent.agent_id === selectedId)) setSelectedId(agents[0]?.agent_id || null);
  }, [agents, selectedId]);

  useEffect(() => {
    let cancelled = false;
    if (dataSource !== "api") {
      setAgentGroups(buildDemoGroups([], agents).filter((item) => item.resource_type === "agent"));
      return undefined;
    }
    requestJson("/api/v1/groups?resource_type=agent&lifecycle_status=active&limit=100")
      .then((response) => { if (!cancelled) setAgentGroups(response.data); })
      .catch(() => { if (!cancelled) setAgentGroups([]); });
    return () => { cancelled = true; };
  }, [agents, dataSource, requestJson]);

  useEffect(() => {
    let cancelled = false;
    if (selectedGroupId === "all") { setGroupMemberIds(null); return undefined; }
    const group = agentGroups.find((item) => item.group_id === selectedGroupId);
    if (!group) { setSelectedGroupId("all"); return undefined; }
    const includeInheritedSubagents = (memberIds) => {
      const direct = new Set(memberIds);
      agents.forEach((agent) => { if (agent.parent_agent_id && direct.has(agent.parent_agent_id)) direct.add(agent.agent_id); });
      return direct;
    };
    if (dataSource !== "api") {
      setGroupMemberIds(includeInheritedSubagents((group.members || []).map((item) => item.agent_id)));
      return undefined;
    }
    requestJson(`/api/v1/groups/${selectedGroupId}`)
      .then((response) => { if (!cancelled) setGroupMemberIds(includeInheritedSubagents(response.data.members.map((item) => item.agent_id))); })
      .catch((error) => { if (!cancelled) { setGroupMemberIds(new Set()); setNotice(error.message); } });
    return () => { cancelled = true; };
  }, [agentGroups, agents, dataSource, requestJson, selectedGroupId]);

  const loadDetail = useCallback(async (agentId = selectedId) => {
    const agent = agents.find((item) => item.agent_id === agentId);
    if (!agent) { setDetail(null); return null; }
    if (dataSource !== "api") {
      const item = buildDemoAgentDetail(agent, agents);
      setDetail(item); setDetailState("ready"); return item;
    }
    setDetailState("loading");
    try {
      let item = (await requestJson(`/api/v1/agents/${agent.agent_id}`)).data;
      if (item.structure_role === "subagent" && item.parent_agent_id && !item.active_deployment) {
        const parent = (await requestJson(`/api/v1/agents/${item.parent_agent_id}`)).data;
        item = { ...item, active_deployment: parent.active_deployment, deployment_members: parent.deployment_members };
      }
      setDetail(item); setDetailState("ready"); return item;
    } catch {
      const item = buildDemoAgentDetail(agent, agents);
      setDetail(item); setDetailState("fallback"); return item;
    }
  }, [agents, dataSource, requestJson, selectedId]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  useEffect(() => {
    if (dataSource !== "api" || ["queued", "running"].includes(activeRun?.status)) return;
    const persisted = detail?.recent_operations?.find((operation) => (
      ["queued", "running"].includes(operation.host_status || operation.run_status || operation.status)
    ));
    if (!persisted || activeRun?.run_id === persisted.run_id) return;
    const action = persisted.operation_code === "agent.migrate_deployment"
      ? "migrate"
      : persisted.operation_code === "agent.remove_deployment"
        ? "remove"
        : detail.active_deployment ? "sync" : "deploy";
    setActiveRun({
      ...persisted,
      status: persisted.host_status || persisted.run_status || persisted.status,
      target_count: 1,
      succeeded_count: 0,
      failed_count: 0,
      client_action: action,
      client_agent_id: detail.agent_id,
    });
  }, [activeRun?.run_id, activeRun?.status, dataSource, detail]);

  const refreshRuntimeReferences = useCallback(async () => {
    const runtimes = await loadPaginatedItems(
      requestJson,
      "/api/v1/runtimes?lifecycle_status=active",
      "runtimes",
    );
    setReferenceData((current) => ({ ...current, runtimes }));
    return runtimes;
  }, [requestJson]);

  const ensureReferenceData = async (refreshRuntimes = false) => {
    if (referenceData.departments.length && referenceData.people.length && referenceData.runtimes.length) {
      if (!refreshRuntimes) return referenceData;
      const runtimes = await refreshRuntimeReferences();
      return { ...referenceData, runtimes };
    }
    const [reference, people, departments, groups, runtimes] = await Promise.all([
      requestJson("/api/v1/reference-data"),
      loadPaginatedItems(requestJson, "/api/v1/people", "people"),
      loadPaginatedItems(requestJson, "/api/v1/departments", "departments"),
      requestJson("/api/v1/groups?resource_type=agent&lifecycle_status=active&limit=100"),
      loadPaginatedItems(requestJson, "/api/v1/runtimes?lifecycle_status=active", "runtimes"),
    ]);
    const next = { ...reference.data, people, departments, agent_groups: groups.data, runtimes };
    setReferenceData(next);
    return next;
  };

  const openDialog = async (type) => {
    if (type === "deploy" && !canExecute) { setNotice("当前账号没有执行智能体部署的权限。"); return; }
    const detailHasActiveDeploymentTask = detail?.recent_operations?.some((operation) => (
      ["queued", "running"].includes(operation.host_status || operation.run_status || operation.status)
    ));
    if (type === "deploy" && (["queued", "running"].includes(activeRun?.status) || detailHasActiveDeploymentTask)) { setNotice("已有运行时部署任务正在执行，请等待任务结束后再操作。"); return; }
    if (type !== "deploy" && !canManage) { setNotice("当前账号为只读权限，不能修改智能体。"); return; }
    setFormError("");
    if (dataSource !== "api") { setNotice("当前为模拟数据，请启动本机管理服务后执行写操作。"); return; }
    try { await ensureReferenceData(type === "deploy"); setDialog(type); }
    catch (error) { setNotice(error.message); }
  };

  const saveAgent = async (payload) => {
    setSaving(true); setFormError("");
    try {
      const editing = dialog === "edit";
      const response = await requestJson(editing ? `/api/v1/agents/${detail.agent_id}` : "/api/v1/agents", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setSelectedId(response.data.agent_id); setDetail(response.data); setDialog(null);
      await Promise.all([refreshAgents(), onOverviewChanged?.()]);
      setNotice(editing ? "智能体已更新" : "智能体已创建");
    } catch (error) { setFormError(error.message); }
    finally { setSaving(false); }
  };

  const deployAgent = async (action, parameters) => {
    if (!detail || operationBusy) return;
    const operationCode = agentDeploymentOperationCode(action);
    const legacyMetadataOnlyRemoval = isLegacyUnmanagedDeployment(detail);
    const runtime = referenceData.runtimes.find((item) => item.runtime_id === parameters.runtime_id);
    const currentRuntimeName = displayRuntimeName(detail.active_deployment?.runtime_name || detail.active_deployment?.runtime_id || "当前运行时");
    const targetRuntimeName = displayRuntimeName(runtime?.name || parameters.runtime_id || "无");
    const confirmation = action === "migrate"
      ? `确认将“${detail.name}”从“${currentRuntimeName}”迁移到“${targetRuntimeName}”？平台会先在目标预置，再停源并启用目标，渠道会短暂中断；失败时自动恢复原部署，源工作区内容不会复制。`
      : action === "remove"
        ? legacyMetadataOnlyRemoval
          ? `确认解除“${detail.name}”与“${currentRuntimeName}”的历史导入关系？本操作仅关闭平台导入关系，不连接或修改远端运行时；远端身份、工作区、渠道均保持原样。`
          : `确认解除“${detail.name}”与“${currentRuntimeName}”的运行时部署关系？平台会撤销渠道和托管关系并重启当前运行时，智能体 identity 与工作区保留。`
        : `确认${action === "sync" ? "同步" : "部署"}“${detail.name}”并重启目标运行时“${targetRuntimeName}”？`;
    if (!window.confirm(confirmation)) return;
    setSaving(true); setOperationBusy(true); setFormError(""); setNotice("");
    try {
      const run = (await requestJson("/api/v1/automation/runs", {
        method: "POST",
        confirmed: true,
        body: JSON.stringify({
          operation_code: operationCode,
          agent_ids: [detail.agent_id],
          parameters,
        }),
      })).data;
      setActiveRun({ ...run, client_action: action, client_agent_id: detail.agent_id });
      setDialog(null);
      setNotice(`${AGENT_OPERATION_LABELS[operationCode]}任务已下发，可前往任务中心查看执行过程。`);
    } catch (error) { setFormError(error.message); }
    finally { setSaving(false); setOperationBusy(false); }
  };

  useEffect(() => {
    if (dataSource !== "api" || !activeRun?.run_id || !["queued", "running"].includes(activeRun.status)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const run = (await requestJson(`/api/v1/automation/runs/${activeRun.run_id}`)).data;
        if (cancelled) return;
        const action = activeRun.client_action || "sync";
        setActiveRun((current) => ({
          ...run,
          client_action: current?.client_action || action,
          client_agent_id: current?.client_agent_id || activeRun.client_agent_id,
        }));
        if (!["queued", "running"].includes(run.status)) {
          await Promise.allSettled([refreshAgents(), loadDetail(selectedId), refreshRuntimeReferences(), onOverviewChanged?.()]);
          setNotice(agentDeploymentResultMessage(action, run));
        }
      } catch (error) {
        if (!cancelled) setNotice(`任务状态刷新失败：${error.message}`);
      }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeRun?.client_action, activeRun?.client_agent_id, activeRun?.run_id, activeRun?.status, dataSource, loadDetail, onOverviewChanged, refreshAgents, refreshRuntimeReferences, requestJson, selectedId]);

  const retire = async () => {
    if (!detail || !window.confirm(`确认退役智能体“${detail.name}”？`)) return;
    try {
      const response = await requestJson(`/api/v1/agents/${detail.agent_id}?row_version=${detail.row_version}`, { method: "DELETE", confirmed: true });
      setDetail(response.data);
      await Promise.all([refreshAgents(), onOverviewChanged?.()]);
      setNotice("智能体已退役");
    } catch (error) { setNotice(error.message); }
  };

  const visibleAgents = useMemo(
    () => filterAssetsByGroup(filterAgents(agents, query, role, archetype), groupMemberIds, "agent_id"),
    [agents, archetype, groupMemberIds, query, role],
  );
  const primaryCount = dataSource === "api" ? agents.filter((agent) => agent.structure_role === "primary").length : primaryTotal;
  const subagentCount = agents.filter((agent) => agent.structure_role === "subagent").length;
  const deployment = detail?.active_deployment;
  const detailDeploymentActions = availableAgentDeploymentActions(detail);
  const detailMultiMemberAdoptionBlocked = hasUnsupportedImportedChildMembers(detail);
  const detailLegacyMetadataOnlyRemoval = isLegacyUnmanagedDeployment(detail);
  const persistedActiveDeploymentRun = detail?.recent_operations?.find((operation) => (
    ["queued", "running"].includes(operation.host_status || operation.run_status || operation.status)
  ));
  const persistedDeploymentRun = persistedActiveDeploymentRun || detail?.recent_operations?.[0];
  const displayedDeploymentRun = activeRun?.client_agent_id === detail?.agent_id ? activeRun : persistedDeploymentRun;
  const deploymentOperationRunning = ["queued", "running"].includes(
    displayedDeploymentRun?.host_status || displayedDeploymentRun?.run_status || displayedDeploymentRun?.status,
  );
  const displayedDeploymentRecovery = agentDeploymentRecoveryStatus(displayedDeploymentRun);

  return (
    <section className="asset-page" aria-labelledby="agent-page-title">
      <div className="asset-page-heading"><div><span className="eyebrow">智能体管理</span><h2 id="agent-page-title">智能体</h2><p>管理主子结构、服务类型、部署关系和渠道对接。</p></div><div className="server-heading-actions">{canManage && <button className="secondary-action" onClick={() => openDialog("create")}><Plus size={17} />新增智能体</button>}<div className="agent-page-totals"><div><strong>{primaryCount}</strong><span>主智能体</span></div><i /><div><strong>{subagentCount}</strong><span>子智能体</span></div></div></div></div>

      {notice && <div className="management-notice"><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭提示"><X size={14} /></button></div>}
      {activeRun && <div className="active-operation-strip"><OperationStatus operation={activeRun} /><strong>{AGENT_OPERATION_LABELS[activeRun.operation_code] || activeRun.operation_code}</strong><span>{activeRun.target_count} 个主智能体</span><span>成功 {activeRun.succeeded_count}</span><span>失败 {activeRun.failed_count}</span><button onClick={() => onOpenTask?.(activeRun.run_id)}>任务中心<CaretRight size={13} /></button></div>}

      <div className="asset-toolbar agent-toolbar">
        <label className="asset-search"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索智能体、部门、父级或运行时" /></label>
        <label className="asset-filter"><span>结构</span><select value={role} onChange={(event) => setRole(event.target.value)}><option value="all">全部角色</option><option value="primary">主智能体</option><option value="subagent">子智能体</option></select></label>
        <label className="asset-filter"><span>服务类型</span><select value={archetype} onChange={(event) => setArchetype(event.target.value)}><option value="all">全部类型</option>{Object.entries(AGENT_ARCHETYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="asset-filter"><span>管理分组</span><select value={selectedGroupId} onChange={(event) => setSelectedGroupId(event.target.value)}><option value="all">全部分组</option>{agentGroups.map((group) => <option key={group.group_id} value={group.group_id}>{group.name}</option>)}</select></label>
        <div className="toolbar-summary">当前显示 <strong>{visibleAgents.length}</strong> 条</div>
      </div>

      <div className="asset-layout agent-asset-layout">
        <div className="asset-list-card">
          <div className="agent-list-head"><span>智能体</span><span>结构</span><span>服务类型</span><span>状态</span><span>归属部门</span><span>部署 / 父级</span><span /></div>
          <div className="asset-list-body">
            {visibleAgents.map((agent) => <button className={selectedId === agent.agent_id ? "agent-list-row agent-list-row--selected" : "agent-list-row"} key={agent.agent_id} onClick={() => setSelectedId(agent.agent_id)}>
              <span className="server-identity"><Robot size={19} /><span><strong>{agent.name}</strong><small>{agent.agent_id}</small></span></span>
              <RoleBadge role={agent.structure_role} />
              <ArchetypeBadge archetype={agent.service_archetype} />
              <AgentLifecycle status={agent.lifecycle_status} />
              <span className="department-summary"><Buildings size={14} />{agent.owning_department_name || "未归属"}</span>
              <span className="cloud-summary"><strong>{agent.structure_role === "primary" ? displayRuntimeName(agent.active_runtime_name || "未部署") : agent.parent_agent_name || "未关联父级"}</strong><small>{agent.structure_role === "primary" ? "活动运行时" : "所属主智能体"}</small></span>
              <CaretRight size={16} />
            </button>)}
            {!visibleAgents.length && <div className="asset-empty"><MagnifyingGlass size={28} /><strong>未找到匹配智能体</strong><span>请调整搜索词或筛选条件。</span></div>}
          </div>
        </div>

        <aside className="asset-detail-card agent-detail-panel" aria-label="智能体详情">
          {detailState === "loading" ? <div className="detail-loading"><ArrowsClockwise size={28} className="spin" /><span>正在读取智能体详情</span></div> : detail ? <>
            <div className="detail-header agent-detail-header"><div className="detail-title"><span className="detail-icon detail-icon--agent"><Robot size={22} /></span><div><h3>{detail.name}</h3><span>{detail.agent_id}</span></div></div><AgentLifecycle status={detail.lifecycle_status} /></div>
            {(canManage || canExecute) && <div className="server-detail-actions">{canManage && <button onClick={() => openDialog("edit")} disabled={detail.lifecycle_status === "retired"}><PencilSimple size={15} />编辑</button>}{canExecute && detail.structure_role === "primary" && <button className="agent-runtime-deployment-action" onClick={() => openDialog("deploy")} disabled={!detailDeploymentActions.length || operationBusy || ["queued", "running"].includes(activeRun?.status) || deploymentOperationRunning}><Cube size={15} />运行时部署</button>}{canManage && <button className="danger-action" onClick={retire} disabled={detail.lifecycle_status === "retired"}><Trash size={15} />退役</button>}</div>}
            {detailState === "fallback" && <div className="detail-notice">本机 API 详情暂不可用，当前展示同一智能体的模拟明细。</div>}
            <div className="agent-classification"><RoleBadge role={detail.structure_role} /><ArchetypeBadge archetype={detail.service_archetype} /><span>v{detail.row_version || 1}</span></div>
            <div className="detail-section"><h4>业务定义</h4><p className="agent-description">{detail.description || "尚未填写智能体说明。"}</p><div className="detail-field-grid"><InfoField icon={Buildings} label="归属部门" value={`${detail.owning_department_name || "未归属"}${detail.owning_department_code ? ` / ${detail.owning_department_code}` : ""}`} />{detail.service_archetype === "personal" && <InfoField icon={UserCircle} label="主要服务人员" value={`${detail.primary_service_person_name || "未配置"}${detail.primary_service_employee_no ? ` / ${detail.primary_service_employee_no}` : ""}`} />}{detail.service_archetype === "department" && <InfoField icon={Buildings} label="主要服务部门" value={detail.primary_service_department_code} />}{detail.service_archetype === "business" && <><InfoField icon={Briefcase} label="业务领域" value={detail.business_domain} /><InfoField icon={Globe} label="业务场景" value={detail.business_scenario} /></>}{detail.structure_role === "subagent" && <InfoField icon={TreeStructure} label="所属主智能体" value={detail.parent_agent_name} />}</div></div>
            {detail.structure_role === "primary" && <div className="detail-section"><h4>管理人员</h4><div className="administrator-grid"><div><span className="admin-icon admin-icon--business"><Briefcase size={17} /></span><span><small>业务管理员</small><strong>{detail.business_admin_name || "待确认"}</strong><em>{detail.business_admin_employee_no || "未填工号"}</em></span></div><div><span className="admin-icon admin-icon--it"><ShieldCheck size={17} /></span><span><small>IT 管理员</small><strong>{detail.it_admin_name || "待确认"}</strong><em>{detail.it_admin_employee_no || "未填工号"}</em></span></div></div></div>}
            <div className="detail-section agent-channel-summary"><div className="detail-section-title"><h4>渠道对接</h4><span className={detail.channel?.channel_type === "feishu" ? "agent-channel-enabled" : ""}>{detail.channel?.channel_type === "feishu" ? "飞书" : "未设置"}</span></div>{detail.channel?.channel_type === "feishu" ? <div className="agent-channel-card"><span className="agent-channel-icon"><PaperPlaneTilt size={18} weight="fill" /></span><span><strong>{detail.channel.application_name}</strong><small>AppID：{detail.channel.app_id}</small><em><LockKey size={12} />AppSecret 已加密保存</em></span></div> : <span className="detail-empty-line">当前不对接外部渠道</span>}</div>
            <div className="detail-section agent-runtime-deployment-section"><div className="detail-section-title"><h4>{detail.structure_role === "primary" ? "运行时部署" : "继承的运行时部署"}</h4><span className={displayedDeploymentRecovery === "需要人工恢复" ? "agent-deployment-state agent-deployment-state--recovery" : deployment ? "agent-deployment-state agent-deployment-state--bound" : "agent-deployment-state"}>{deploymentOperationRunning ? "任务执行中" : displayedDeploymentRecovery === "需要人工恢复" ? "需人工恢复" : deployment ? "已绑定" : "未绑定"}</span></div>{displayedDeploymentRun && <button type="button" className="agent-deployment-task-state" onClick={() => onOpenTask?.(displayedDeploymentRun.run_id)}><OperationStatus operation={displayedDeploymentRun} /><span><strong>{AGENT_OPERATION_LABELS[displayedDeploymentRun.operation_code] || displayedDeploymentRun.operation_code}</strong><small>{deploymentOperationRunning ? "部署关系操作执行中，完整阶段可在任务中心查看" : displayedDeploymentRecovery || formatOperationTime(displayedDeploymentRun.finished_at || displayedDeploymentRun.created_at)}</small></span><CaretRight size={14} /></button>}{deployment ? <><div className="agent-deployment-card"><span className="runtime-icon"><Cube size={17} /></span><span><strong>{displayRuntimeName(deployment.runtime_name)}</strong><small>{deployment.family_code} · {deployment.runtime_id}</small></span></div><div className="agent-deployment-members">{detail.deployment_members?.map((member) => <div key={member.deployment_member_id}><Robot size={14} /><span><strong>{member.name}</strong><small>{member.runtime_agent_key || "真实 ID 待确认"} · {member.member_role === "primary" ? "主智能体" : "子智能体"}</small></span><em className={member.agent_lifecycle_status === "retired" ? "agent-member-pending" : ""}>{member.agent_lifecycle_status === "retired" ? "待同步退役" : member.mapping_status === "verified" ? "已确认" : "待确认"}</em></div>)}</div></> : <span className="detail-empty-line">尚未部署到运行时；点击“运行时部署”建立关系。</span>}</div>
            {detailMultiMemberAdoptionBlocked && <div className="agent-deployment-detail-note"><WarningCircle size={16} /><span><strong>历史关系含未解析子成员</strong>{detailLegacyMetadataOnlyRemoval ? "当前版本不支持多成员收编，可仅解除导入关系后重新部署；解除只关闭平台关系，不会连接或修改远端。" : "当前关系混合已确认与未确认成员，平台暂无安全自动操作；请先通过运行时发现完成成员映射。"}</span></div>}
            {detail.structure_role === "primary" && <><div className="detail-section"><div className="detail-section-title"><h4>子智能体</h4><span>{detail.subagents?.length || 0}</span></div><div className="agent-subagent-grid">{detail.subagents?.map((subagent) => <div key={subagent.agent_id}><Robot size={15} /><span><strong>{subagent.name}</strong><small>{AGENT_STATUS_LABELS[subagent.lifecycle_status] || subagent.lifecycle_status}</small></span></div>)}{!detail.subagents?.length && <span className="detail-empty-line">尚无稳定子智能体</span>}</div></div>
            <div className="detail-section"><div className="detail-section-title"><h4>可访问用户</h4><span>{detail.accessible_people?.length || 0}</span></div><div className="people-chips">{detail.accessible_people?.map((person) => <span key={person.person_id}><UserCircle size={15} /><strong>{person.display_name}</strong><small>{person.employee_no}</small></span>)}{!detail.accessible_people?.length && <span className="detail-empty-line">尚未配置可访问用户</span>}</div></div>
            <div className="detail-section"><div className="detail-section-title"><h4>管理分组</h4><span>{detail.groups?.length || 0}</span></div><div className="group-tags">{detail.groups?.map((group) => <span key={group.group_id}>{group.name}</span>)}{!detail.groups?.length && <span className="group-tag--empty">尚未加入管理分组</span>}</div></div>
            <div className="detail-section"><div className="detail-section-title"><h4>网络访问需求</h4><span>{detail.network_requirements?.length || 0}</span></div><div className="network-requirement-list">{detail.network_requirements?.map((item) => <div key={item.network_requirement_id}><Network size={16} /><span><strong>{item.system_name}</strong><small>{item.protocol}://{item.target_value}:{item.port_from}{item.port_to !== item.port_from ? `-${item.port_to}` : ""}</small><em>{item.purpose}</em></span></div>)}{!detail.network_requirements?.length && <span className="detail-empty-line">尚未配置网络需求</span>}</div></div><div className="detail-section"><div className="detail-section-title"><h4>最近部署操作</h4><span>{detail.recent_operations?.length || 0}</span></div><div className="operation-history agent-operation-history">{detail.recent_operations?.slice(0, 5).map((operation) => <button type="button" key={operation.run_id} onClick={() => onOpenTask?.(operation.run_id)}><OperationStatus operation={operation} /><span><strong>{AGENT_OPERATION_LABELS[operation.operation_code] || operation.operation_code}</strong><small>{agentDeploymentRecoveryStatus(operation) || operation.error_message || formatOperationTime(operation.created_at)}</small></span><CaretRight size={13} /></button>)}{!detail.recent_operations?.length && <span className="detail-empty-line">暂无部署操作记录</span>}</div></div></>}
          </> : <div className="detail-loading"><Robot size={28} /><span>选择一个智能体查看详情</span></div>}
        </aside>
      </div>
      {dialog === "create" && <Dialog title="新增智能体" onClose={() => setDialog(null)}><AgentForm key="create-new" agents={agents} referenceData={referenceData} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveAgent} /></Dialog>}
      {dialog === "edit" && detail && <Dialog title="编辑智能体" onClose={() => setDialog(null)}><AgentForm key={`edit-${detail.agent_id}`} agent={detail} agents={agents} referenceData={referenceData} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={saveAgent} /></Dialog>}
      {dialog === "deploy" && detail && <Dialog title={`运行时部署 · ${detail.name}`} onClose={() => !operationBusy && setDialog(null)}><AgentDeploymentForm key={`deploy-${detail.agent_id}-${deployment?.agent_deployment_id || "new"}`} agent={detail} runtimes={referenceData.runtimes} saving={saving} error={formError} onCancel={() => setDialog(null)} onSave={deployAgent} /></Dialog>}
    </section>
  );
}

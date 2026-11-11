import { useEffect, useState } from "react";
import { FloppyDisk, Plus, ShieldCheck, UserCircle, X } from "@phosphor-icons/react";

const ROLE_LABELS = { admin: "管理员", operator: "操作员", viewer: "只读用户" };
const EMPTY = { username: "", display_name: "", password: "", role: "viewer" };

export function UserManagement({ requestJson }) {
  const [users, setUsers] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const response = await requestJson("/api/v1/users");
    setUsers(response.data.users);
    setSelectedId((current) => current || response.data.users[0]?.user_id || null);
  }

  useEffect(() => { load().catch((loadError) => setError(loadError.message)); }, []);
  const selected = users.find((user) => user.user_id === selectedId);
  useEffect(() => {
    if (selected && !creating) {
      setForm({ display_name: selected.display_name, role: selected.role, status: selected.status, password: "" });
    }
  }, [selected?.user_id, selected?.row_version, creating]);

  function edit(user) {
    setCreating(false); setSelectedId(user.user_id); setError("");
    setForm({ display_name: user.display_name, role: user.role, status: user.status, password: "" });
  }

  function startCreate() {
    setCreating(true); setSelectedId(null); setForm(EMPTY); setError("");
  }

  async function save(event) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      if (creating) {
        await requestJson("/api/v1/users", { method: "POST", body: JSON.stringify(form) });
        setNotice("账号已创建，可以立即登录。 ");
      } else {
        const payload = { row_version: selected.row_version, display_name: form.display_name, role: form.role, status: form.status };
        if (form.password) payload.password = form.password;
        await requestJson(`/api/v1/users/${selected.user_id}`, { method: "PATCH", body: JSON.stringify(payload) });
        setNotice(form.password ? "账号已更新，原有会话已退出。" : "账号权限已更新。");
      }
      await load(); setCreating(false); setForm(EMPTY);
    } catch (saveError) { setError(saveError.message); }
    finally { setBusy(false); }
  }

  return <section className="user-management" aria-labelledby="user-page-title">
    <header className="asset-page-heading"><div><span className="eyebrow">访问控制</span><h2 id="user-page-title">用户权限</h2><p>管理登录账号、角色和密码。权限由后端执行，停用或重置密码会撤销现有会话。</p></div><button className="secondary-action" onClick={startCreate}><Plus size={16} />新增用户</button></header>
    {notice && <div className="management-notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={14} /></button></div>}
    <div className="user-management-layout">
      <div className="user-list"><div className="user-list-head"><span>用户</span><span>角色</span><span>状态</span></div>{users.map((user) => <button key={user.user_id} className={user.user_id === selectedId ? "user-list-row user-list-row--selected" : "user-list-row"} onClick={() => edit(user)}><span><i><UserCircle size={18} /></i><span><strong>{user.display_name}</strong><small>{user.username}</small></span></span><em>{ROLE_LABELS[user.role]}</em><b className={`user-status user-status--${user.status}`}>{user.status === "active" ? "启用" : "停用"}</b></button>)}</div>
      <form className="user-editor" onSubmit={save}>
        <header><ShieldCheck size={22} /><div><h3>{creating ? "创建登录账号" : selected ? `编辑 ${selected.username}` : "选择用户"}</h3><p>{creating ? "新账号默认立即生效。" : "调整角色、状态或重置密码。"}</p></div></header>
        {(creating || selected) && <div className="user-editor-fields">
          {creating && <label><span>用户名</span><input required value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} placeholder="例如 zhangsan" /></label>}
          <label><span>显示名称</span><input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
          <label><span>角色</span><select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          {!creating && <label><span>状态</span><select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}><option value="active">启用</option><option value="disabled">停用</option></select></label>}
          <label className="user-password-field"><span>{creating ? "初始密码" : "重置密码（可选）"}</span><input required={creating} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /><small>至少 12 位，包含大写、小写、数字和符号，且不能包含用户名。</small></label>
          {error && <div className="management-form-error">{error}</div>}
          <button className="primary-action user-save" disabled={busy}><FloppyDisk size={15} />{busy ? "保存中" : "保存账号"}</button>
        </div>}
      </form>
    </div>
  </section>;
}

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowClockwise, LockKey, ShieldCheck, User } from "@phosphor-icons/react";


export function LoginPage({ requestJson, onLogin, notice = "" }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [offset, setOffset] = useState(0);
  const [dragged, setDragged] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const dragRef = useRef(null);

  const loadChallenge = useCallback(async () => {
    setError(""); setOffset(0); setDragged(false);
    try {
      const response = await requestJson("/api/v1/auth/challenge", { method: "POST" });
      setChallenge(response.data);
    } catch (loadError) {
      setError(loadError.message || "无法加载滑块验证");
    }
  }, [requestJson]);

  useEffect(() => { loadChallenge(); }, [loadChallenge]);

  function pointerDown(event) {
    if (!challenge || loading) return;
    dragRef.current = { startX: event.clientX, startOffset: offset };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function pointerMove(event) {
    if (!dragRef.current || !challenge) return;
    const max = challenge.track_width - challenge.piece_width;
    setOffset(Math.max(0, Math.min(max, dragRef.current.startOffset + event.clientX - dragRef.current.startX)));
  }

  function pointerUp(event) {
    if (!dragRef.current) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    setDragged(true);
  }

  async function submit(event) {
    event.preventDefault();
    if (!challenge || !dragged) { setError("请先完成滑块验证"); return; }
    setLoading(true); setError("");
    try {
      const response = await requestJson("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username, password,
          challenge_id: challenge.challenge_id,
          challenge_offset: Math.round(offset),
        }),
      });
      onLogin(response.data.user);
    } catch (loginError) {
      setError(loginError.message || "登录失败");
      await loadChallenge();
    } finally {
      setLoading(false);
    }
  }

  return <main className="login-page">
    <section className="login-brand-panel">
      <div className="login-brand"><span><ShieldCheck size={30} weight="fill" /></span>AgentsWatch</div>
      <div className="login-brand-copy"><small>OPENCLAW OPERATIONS</small><h1>统一管理每一个<br />智能体运行环境</h1><p>资产关系、自动化执行与可观测性数据统一收口，所有管理操作均留存登录用户审计身份。</p></div>
      <div className="login-security-note"><LockKey size={20} /><span><strong>受保护的管理入口</strong><small>会话 Cookie、CSRF 校验与角色权限由服务端强制执行</small></span></div>
    </section>
    <section className="login-form-panel">
      <form className="login-card" onSubmit={submit}>
        <header><span className="eyebrow">管理系统登录</span><h2>欢迎回来</h2><p>请输入账号密码，并完成滑块验证。</p></header>
        <label className="login-field"><span>用户名</span><div><User size={18} /><input autoFocus required autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="请输入用户名" /></div></label>
        <label className="login-field"><span>密码</span><div><LockKey size={18} /><input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="请输入密码" /></div></label>
        <div className="slider-captcha">
          <div className="slider-image">{challenge ? <img src={challenge.image} alt="滑块拼图背景" /> : <div className="slider-loading">正在生成验证图…</div>}<i style={{ transform: `translateX(${offset}px)` }} /></div>
          <div className={dragged ? "slider-track slider-track--dragged" : "slider-track"}>
            <span style={{ width: `${offset + 21}px` }} />
            <button type="button" aria-label="拖动滑块" style={{ transform: `translateX(${offset}px)` }} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}><span>››</span></button>
            <em>{dragged ? "已完成拖动，点击登录验证" : "按住滑块，拖动完成拼图"}</em>
          </div>
          <button className="captcha-refresh" type="button" onClick={loadChallenge} aria-label="刷新滑块验证"><ArrowClockwise size={15} />换一张</button>
        </div>
        {notice && <div className="login-success">{notice}</div>}
        {error && <div className="login-error">{error}</div>}
        <button className="login-submit" disabled={loading || !challenge}>{loading ? "正在验证…" : "登录管理系统"}</button>
        <footer>密码至少 12 位，必须同时包含大小写字母、数字和符号。</footer>
      </form>
    </section>
  </main>;
}

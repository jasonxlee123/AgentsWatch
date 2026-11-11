export const WEB_ACCESS_LABELS = {
  local_ssh_tunnel: "SSH 临时隧道",
  tailscale_serve: "Tailscale Serve",
  direct_url: "私网直接地址",
};

export const WEB_AUTH_LABELS = {
  none: "无需共享密钥",
  token: "Token",
  password: "密码",
  username_password: "账号和密码",
};

export function runtimeWebEndpointDefaults(runtime, endpoint) {
  const openClaw = runtime?.family_code === "openclaw";
  return {
    display_name: endpoint?.display_name || (openClaw ? "OpenClaw Control UI" : "运行时 WebUI"),
    access_mode: endpoint?.access_mode || "local_ssh_tunnel",
    scheme: endpoint?.scheme || "http",
    direct_url: endpoint?.direct_url || "",
    remote_host: endpoint?.remote_host || "127.0.0.1",
    port_override: endpoint?.port_override ?? "",
    base_path: endpoint?.base_path || "/",
    auth_mode: endpoint?.auth_mode || (openClaw ? "token" : "none"),
    token: "",
    username: "",
    password: "",
    enabled: endpoint?.enabled ?? true,
    row_version: endpoint?.row_version ?? null,
  };
}

export function buildRuntimeWebEndpointPayload(form) {
  const direct = ["direct_url", "tailscale_serve"].includes(form.access_mode);
  const payload = {
    display_name: form.display_name.trim(),
    access_mode: form.access_mode,
    scheme: form.access_mode === "tailscale_serve" ? "https" : form.scheme,
    direct_url: direct ? form.direct_url.trim() : null,
    remote_host: direct ? "127.0.0.1" : "127.0.0.1",
    port_override: direct || form.port_override === "" ? null : Number(form.port_override),
    base_path: form.base_path.trim() || "/",
    auth_mode: form.auth_mode,
    enabled: form.enabled,
    row_version: form.row_version,
  };
  if (form.auth_mode === "token" && form.token) payload.token = form.token;
  if (form.auth_mode === "password" && form.password) payload.password = form.password;
  if (form.auth_mode === "username_password") {
    if (form.username) payload.username = form.username.trim();
    if (form.password) payload.password = form.password;
  }
  if (payload.row_version === null) delete payload.row_version;
  return payload;
}

export function webEndpointTarget(endpoint, runtimePort) {
  if (!endpoint) return "未配置";
  if (["direct_url", "tailscale_serve"].includes(endpoint.access_mode)) {
    return `${endpoint.direct_url || ""}${endpoint.base_path === "/" ? "/" : endpoint.base_path}`;
  }
  return `${endpoint.scheme}://127.0.0.1:${endpoint.effective_port || runtimePort || "未配置"}${endpoint.base_path === "/" ? "/" : endpoint.base_path}`;
}

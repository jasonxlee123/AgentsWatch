export function normalizeBasePath(value = "/") {
  const raw = String(value || "/").trim();
  if (!raw || raw === "/") return "/";

  const pathname = raw.split(/[?#]/, 1)[0].replaceAll("\\", "/");
  const segments = pathname.split("/").filter(Boolean);
  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new Error("frontend base path cannot contain relative path segments");
  }
  return segments.length ? `/${segments.join("/")}/` : "/";
}

export function resolveAppRequestPath(path, basePath = import.meta.env?.BASE_URL || "/") {
  const requestPath = String(path);
  const isManagedRequest = /^\/api(?:\/|\?|#|$)/.test(requestPath)
    || /^\/healthz(?:\/|\?|#|$)/.test(requestPath);
  if (!isManagedRequest) return requestPath;

  const normalizedBase = normalizeBasePath(basePath);
  if (normalizedBase === "/") return requestPath;
  return `${normalizedBase.slice(0, -1)}${requestPath}`;
}

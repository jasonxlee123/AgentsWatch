/**
 * Decide whether synthetic demo data may stand in for a missing API.
 *
 * Demo data is a development convenience, never a production fallback: a
 * production build must show a blocking error instead of plausible-looking
 * assets that an operator could mistake for live inventory.
 */
export function demoDataAllowed(env) {
  if (!env) return false;
  if (env.DEV === true) return true;
  return String(env.VITE_ALLOW_DEMO ?? "").trim().toLowerCase() === "true";
}

/**
 * Map an auth probe failure to the UI state it should produce.
 *
 * 401 means "not signed in" in every environment. Any other failure means the
 * API is unreachable; only an explicitly demo-enabled build may fall back to
 * the offline demo shell.
 */
export function authFailureState(error, env) {
  if (error?.status === 401) return "unauthenticated";
  return demoDataAllowed(env) ? "offline" : "unavailable";
}

export function formatFreshness(value) {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    return { label: "正在同步", date: "" };
  }
  const pad = (part) => String(part).padStart(2, "0");
  return {
    label: `更新于 ${pad(value.getHours())}:${pad(value.getMinutes())}`,
    date: `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`,
  };
}

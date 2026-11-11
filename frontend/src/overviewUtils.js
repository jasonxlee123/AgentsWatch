export function filterOverviewItems(items, query, textForItem) {
  const keyword = String(query || "").trim().toLocaleLowerCase("zh-CN");
  if (!keyword) return items;
  return items.filter((item) => String(textForItem(item) || "").toLocaleLowerCase("zh-CN").includes(keyword));
}

export function buildAgentOptions(agents = {}) {
  return [
    ...(agents.primary ? [{ ...agents.primary, agent_kind: "primary" }] : []),
    ...((agents.subagents || []).map((agent) => ({ ...agent, agent_kind: "subagent" }))),
  ];
}

export function taskProgress(task = {}) {
  const targetCount = Math.max(0, Number(task.target_count) || 0);
  if (!targetCount) return { completed: 0, percent: 0 };
  const terminal = !["queued", "running"].includes(task.status);
  const completed = terminal
    ? targetCount
    : Math.min(targetCount, Math.max(0, (Number(task.succeeded_count) || 0) + (Number(task.failed_count) || 0)));
  return { completed, percent: Math.min(100, Math.round((completed / targetCount) * 100)) };
}

export function taskSummaryTotal(summary = {}) {
  return Object.values(summary).reduce((total, value) => total + (Number(value) || 0), 0);
}

export function completedTaskCount(summary = {}) {
  return ["succeeded", "partial", "failed", "cancelled"].reduce((total, status) => total + (Number(summary[status]) || 0), 0);
}

export function buildAgentMapFromCatalog(items = []) {
  const activeItems = items.filter((agent) => agent.lifecycle_status !== "retired");
  const childrenByParent = activeItems
    .filter((agent) => agent.structure_role === "subagent" && agent.parent_agent_id)
    .reduce((groups, agent) => {
      const children = groups.get(agent.parent_agent_id) || [];
      children.push({ ...agent, version: agent.version || "未发现" });
      groups.set(agent.parent_agent_id, children);
      return groups;
    }, new Map());

  return Object.fromEntries(activeItems
    .filter((agent) => agent.structure_role === "primary" && agent.active_runtime_id)
    .map((primary) => [primary.active_runtime_id, {
      primary: { ...primary, version: primary.version || "未发现" },
      subagents: childrenByParent.get(primary.agent_id) || [],
    }]));
}

function rectIsInside(viewport, rect, tolerance = 1) {
  return rect.top >= viewport.top - tolerance
    && rect.bottom <= viewport.bottom + tolerance
    && rect.left >= viewport.left - tolerance
    && rect.right <= viewport.right + tolerance;
}

export function buildRelationshipWire({
  gridRect,
  fromRect,
  toRect,
  fromViewportRect,
  toViewportRect,
  key,
  startColor,
  endColor,
}) {
  if (![gridRect, fromRect, toRect, fromViewportRect, toViewportRect].every(Boolean)) return null;
  if (!rectIsInside(fromViewportRect, fromRect) || !rectIsInside(toViewportRect, toRect)) return null;

  const startX = fromRect.right - gridRect.left;
  const startY = fromRect.top + fromRect.height / 2 - gridRect.top;
  const endX = toRect.left - gridRect.left;
  const endY = toRect.top + toRect.height / 2 - gridRect.top;
  if (startX < 0 || endX > gridRect.width || startY < 0 || startY > gridRect.height || endY < 0 || endY > gridRect.height) return null;

  return {
    key,
    startX,
    startY,
    endX,
    endY,
    midX: startX + (endX - startX) / 2,
    startColor,
    endColor,
  };
}

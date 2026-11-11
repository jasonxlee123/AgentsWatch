export const RISK_LABELS = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export function operationsForScope(operations = [], scope) {
  return operations.filter((operation) => operation.scope === scope);
}

function isBlank(value) {
  return value === undefined || value === null || value === "";
}

export function prepareOperationParameters(operation, rawValues = {}) {
  const parameters = {};
  for (const [name, definition] of Object.entries(operation?.parameters || {})) {
    const raw = rawValues[name];
    if (isBlank(raw)) {
      if (definition.required) throw new Error(`${definition.label || name}为必填项`);
      continue;
    }

    if (definition.type === "array") {
      const items = Array.isArray(raw)
        ? raw
        : String(raw).split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      if (!items.length) {
        if (definition.required) throw new Error(`${definition.label || name}为必填项`);
        continue;
      }
      parameters[name] = items.map((item) => convertScalar(item, definition.items, definition.label || name));
    } else {
      parameters[name] = convertScalar(raw, definition.type, definition.label || name);
    }
  }
  return parameters;
}

function convertScalar(value, type, label) {
  if (type === "integer") {
    const converted = Number(value);
    if (!Number.isInteger(converted)) throw new Error(`${label}必须是整数`);
    return converted;
  }
  if (type === "number") {
    const converted = Number(value);
    if (!Number.isFinite(converted)) throw new Error(`${label}必须是数值`);
    return converted;
  }
  if (type === "boolean") return value === true || value === "true";
  return String(value).trim();
}

export function groupOperationsByCategory(operations = []) {
  return operations.reduce((groups, operation) => {
    const category = operation.category || "未分类";
    if (!groups[category]) groups[category] = [];
    groups[category].push(operation);
    return groups;
  }, {});
}

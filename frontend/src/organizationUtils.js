export const EMPLOYMENT_STATUS_LABELS = {
  active: "在职",
  inactive: "停用",
  departed: "离职",
};

export function responseItems(response, key) {
  if (Array.isArray(response?.data)) return response.data;
  if (Array.isArray(response?.data?.[key])) return response.data[key];
  return [];
}

export async function loadPaginatedItems(requestJson, path, key, maximum = 5000) {
  const collected = [];
  let offset = 0;
  do {
    const separator = path.includes("?") ? "&" : "?";
    const response = await requestJson(`${path}${separator}limit=100&offset=${offset}`);
    const pageItems = responseItems(response, key);
    collected.push(...pageItems);
    const total = response?.page?.total ?? collected.length;
    if (!pageItems.length || collected.length >= total) break;
    offset += pageItems.length;
  } while (offset < maximum);
  return collected;
}

function contains(value, query) {
  return String(value || "").toLocaleLowerCase().includes(query);
}

export function filterDepartments(departments, query = "", activity = "all") {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return departments.filter((department) => {
    if (activity === "active" && !department.is_active) return false;
    if (activity === "inactive" && department.is_active) return false;
    if (!normalizedQuery) return true;
    return [department.name, department.department_code, department.parent_department_name, department.parent_department_code]
      .some((value) => contains(value, normalizedQuery));
  });
}

export function filterPeople(people, query = "", status = "all") {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  return people.filter((person) => {
    if (status !== "all" && person.employment_status !== status) return false;
    if (!normalizedQuery) return true;
    return [person.display_name, person.employee_no, person.department_name, person.department_code]
      .some((value) => contains(value, normalizedQuery));
  });
}

export function buildDepartmentPayload(form, department = null) {
  const values = {
    name: form.name.trim(),
    parent_department_id: form.parent_department_id || null,
    is_active: Boolean(form.is_active),
  };
  if (!department) values.department_code = form.department_code.trim();
  else values.row_version = department.row_version;
  return values;
}

export function buildPersonPayload(form, person = null) {
  const values = {
    display_name: form.display_name.trim(),
    primary_department_id: form.primary_department_id || null,
    employment_status: form.employment_status,
  };
  if (!person) values.employee_no = form.employee_no.trim();
  else values.row_version = person.row_version;
  return values;
}

export function selectableOrganizationItems(items, idField, activePredicate, selectedIds = []) {
  const selected = new Set(selectedIds.filter(Boolean));
  return items.filter((item) => activePredicate(item) || selected.has(item[idField]));
}

export function organizationOptionLabel(item, label) {
  const active = "is_active" in item ? item.is_active : item.employment_status === "active";
  return `${label}${active ? "" : "（已停用）"}`;
}

export function summarizeDepartmentRelationships(relationships = []) {
  return relationships.reduce((summary, relationship) => {
    if (["owner", "owner_and_service_target"].includes(relationship.role)) summary.owner += 1;
    if (["service_target", "owner_and_service_target"].includes(relationship.role)) summary.service += 1;
    return summary;
  }, { owner: 0, service: 0 });
}

export function summarizePersonRelationships(relationships = []) {
  return relationships.reduce((summary, relationship) => {
    if (["business_admin", "it_admin"].includes(relationship.role)) summary.admin += 1;
    if (relationship.role === "accessible_person") summary.accessible += 1;
    if (relationship.role === "primary_service_person") summary.service += 1;
    return summary;
  }, { admin: 0, accessible: 0, service: 0 });
}

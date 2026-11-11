import assert from "node:assert/strict";
import test from "node:test";
import {
  buildDepartmentPayload,
  buildPersonPayload,
  filterDepartments,
  filterPeople,
  loadPaginatedItems,
  responseItems,
  selectableOrganizationItems,
  summarizeDepartmentRelationships,
  summarizePersonRelationships,
} from "../src/organizationUtils.js";

const departments = [
  { department_id: "dep-1", department_code: "DEMO-DIGITAL", name: "示例数字化部", is_active: true },
  { department_id: "dep-2", department_code: "DEMO-ARCHIVE", name: "示例历史部门", is_active: false, parent_department_name: "示例数字化部" },
];

const people = [
  { person_id: "person-1", employee_no: "DEMO-EMP-001", display_name: "示例人员甲", employment_status: "active", department_name: "示例数字化部" },
  { person_id: "person-2", employee_no: "DEMO-EMP-002", display_name: "示例人员乙", employment_status: "inactive", department_name: "示例历史部门" },
];

test("responseItems accepts standard pagination and named reference responses", () => {
  assert.deepEqual(responseItems({ data: departments }, "departments"), departments);
  assert.deepEqual(responseItems({ data: { departments } }, "departments"), departments);
  assert.deepEqual(responseItems({}, "departments"), []);
});

test("loadPaginatedItems follows standard pages without truncating reference choices", async () => {
  const requests = [];
  const items = await loadPaginatedItems(async (path) => {
    requests.push(path);
    return requests.length === 1
      ? { data: [{ person_id: "p1" }, { person_id: "p2" }], page: { total: 3 } }
      : { data: [{ person_id: "p3" }], page: { total: 3 } };
  }, "/api/v1/people?employment_status=active", "people");
  assert.deepEqual(items.map((item) => item.person_id), ["p1", "p2", "p3"]);
  assert.deepEqual(requests, [
    "/api/v1/people?employment_status=active&limit=100&offset=0",
    "/api/v1/people?employment_status=active&limit=100&offset=2",
  ]);
});

test("organization filters search identifiers and apply lifecycle state", () => {
  assert.deepEqual(filterDepartments(departments, "digital", "all").map((item) => item.department_id), ["dep-1"]);
  assert.deepEqual(filterDepartments(departments, "示例数字", "inactive").map((item) => item.department_id), ["dep-2"]);
  assert.deepEqual(filterPeople(people, "DEMO-EMP-001", "active").map((item) => item.person_id), ["person-1"]);
  assert.deepEqual(filterPeople(people, "历史", "inactive").map((item) => item.person_id), ["person-2"]);
});

test("payload builders omit immutable identifiers while editing", () => {
  assert.deepEqual(buildDepartmentPayload({ department_code: " DEP-NEW ", name: " 新部门 ", parent_department_id: "", is_active: true }), {
    department_code: "DEP-NEW", name: "新部门", parent_department_id: null, is_active: true,
  });
  assert.deepEqual(buildDepartmentPayload({ name: "新名称", parent_department_id: "dep-1", is_active: false }, { row_version: 3 }), {
    name: "新名称", parent_department_id: "dep-1", is_active: false, row_version: 3,
  });
  assert.deepEqual(buildPersonPayload({ employee_no: " DEMO-EMP-001 ", display_name: " 示例人员甲 ", primary_department_id: "dep-1", employment_status: "active" }), {
    employee_no: "DEMO-EMP-001", display_name: "示例人员甲", primary_department_id: "dep-1", employment_status: "active",
  });
  assert.deepEqual(buildPersonPayload({ display_name: "示例人员丙", primary_department_id: "", employment_status: "inactive" }, { row_version: 2 }), {
    display_name: "示例人员丙", primary_department_id: null, employment_status: "inactive", row_version: 2,
  });
});

test("inactive organization references remain selectable only when already related", () => {
  assert.deepEqual(
    selectableOrganizationItems(people, "person_id", (item) => item.employment_status === "active", ["person-2"]).map((item) => item.person_id),
    ["person-1", "person-2"],
  );
  assert.deepEqual(
    selectableOrganizationItems(people, "person_id", (item) => item.employment_status === "active").map((item) => item.person_id),
    ["person-1"],
  );
});

test("relationship summaries use the roles returned by organization detail APIs", () => {
  assert.deepEqual(summarizeDepartmentRelationships([
    { role: "owner" }, { role: "service_target" }, { role: "owner_and_service_target" },
  ]), { owner: 2, service: 2 });
  assert.deepEqual(summarizePersonRelationships([
    { role: "business_admin" }, { role: "it_admin" }, { role: "accessible_person" }, { role: "primary_service_person" },
  ]), { admin: 2, accessible: 1, service: 1 });
});

export function agentFormDefaults(agent, referenceData = {}) {
  const channel = agent?.channel || { channel_type: "none" };
  return {
    name: agent?.name || "",
    structure_role: agent?.structure_role || "primary",
    parent_agent_id: agent?.parent_agent_id || "",
    service_archetype: agent?.service_archetype || "business",
    lifecycle_status: agent?.lifecycle_status || "requested",
    owning_department_id: agent?.owning_department_id || referenceData.departments?.[0]?.department_id || "",
    business_admin_person_id: agent?.business_admin_person_id || "",
    it_admin_person_id: agent?.it_admin_person_id || "",
    primary_service_person_id: agent?.primary_service_person_id || "",
    primary_service_department_id: agent?.primary_service_department_id || "",
    business_domain: agent?.business_domain || "",
    business_scenario: agent?.business_scenario || "",
    description: agent?.description || "",
    accessible_person_ids: new Set((agent?.accessible_people || []).map((item) => item.person_id)),
    group_ids: new Set((agent?.groups || []).map((item) => item.group_id)),
    channel_type: channel.channel_type || "none",
    channel_application_name: channel.application_name || agent?.name || "",
    channel_app_id: channel.app_id || "",
    channel_app_secret: "",
  };
}

function optional(value) {
  const normalized = typeof value === "string" ? value.trim() : value;
  return normalized === "" ? null : normalized;
}

export function buildAgentPayload(form, agent = null) {
  const primary = form.structure_role === "primary";
  const payload = {
    name: form.name.trim(),
    service_archetype: form.service_archetype,
    lifecycle_status: form.lifecycle_status,
    owning_department_id: primary ? optional(form.owning_department_id) : null,
    business_admin_person_id: primary ? optional(form.business_admin_person_id) : null,
    it_admin_person_id: primary ? optional(form.it_admin_person_id) : null,
    primary_service_person_id: primary && form.service_archetype === "personal" ? optional(form.primary_service_person_id) : null,
    primary_service_department_id: primary && form.service_archetype === "department" ? optional(form.primary_service_department_id) : null,
    business_domain: primary && form.service_archetype === "business" ? optional(form.business_domain) : null,
    business_scenario: primary && form.service_archetype === "business" ? optional(form.business_scenario) : null,
    description: optional(form.description),
  };

  if (!agent) {
    payload.structure_role = form.structure_role;
    payload.parent_agent_id = primary ? null : optional(form.parent_agent_id);
  }

  if (primary) {
    payload.accessible_person_ids = [...form.accessible_person_ids];
    payload.group_ids = [...form.group_ids];
  } else if (!agent) {
    payload.accessible_person_ids = [];
    payload.group_ids = [];
  }
  payload.channel = form.channel_type === "feishu" ? {
    channel_type: "feishu",
    application_name: optional(form.channel_application_name) || payload.name,
    app_id: optional(form.channel_app_id),
    ...(form.channel_app_secret ? { app_secret: form.channel_app_secret } : {}),
  } : { channel_type: "none" };

  if (agent) payload.row_version = agent.row_version;
  return payload;
}

export function feishuSecretRequired(form, agent) {
  if (form.channel_type !== "feishu") return false;
  const existing = agent?.channel;
  return !existing?.credential_configured || existing.app_id !== form.channel_app_id.trim();
}

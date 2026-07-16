export function workflowStepMcpProfiles(step) {
  const configured = Array.isArray(step?.mcp_profiles)
    ? step.mcp_profiles
    : [step?.mcp_profile];
  return [...new Set(configured.map((value) => String(value ?? "").trim()).filter(Boolean))];
}

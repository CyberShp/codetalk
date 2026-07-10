/**
 * Derive the cockpit's user-facing terminal state from the public run contract.
 * The backend summary is authoritative when present; quality rejection is a
 * review state because execution and artifact generation may still have completed.
 */
export function deriveRunPanelStatus({
  hasPreparedRun,
  activeStatusLabel = "",
  testActivityStatus = "",
  acceptanceStatus = "",
  missingRequired = 0,
  workflowStatus = "",
  hasMaterializedOutput = false,
}) {
  if (!hasPreparedRun) return "空";

  if (activeStatusLabel) {
    if (activeStatusLabel === "运行失败") return "失败";
    if (activeStatusLabel === "运行完成") return "已完成";
    if (["完成但信息不足", "需要复核"].includes(activeStatusLabel)) return "需复核";
    return "进行中";
  }

  const qualityStatus = String(testActivityStatus).toLowerCase();
  const normalizedWorkflowStatus = String(workflowStatus).toLowerCase();
  if (
    ["needs_rework", "invalid"].includes(qualityStatus) ||
    ["needs_rework", "invalid"].includes(normalizedWorkflowStatus)
  ) {
    return "需复核";
  }

  if (
    Number(missingRequired) > 0 ||
    ["incomplete", "error", "failed", "failure"].includes(
      String(acceptanceStatus).toLowerCase(),
    ) ||
    ["failed", "error", "timeout"].includes(normalizedWorkflowStatus)
  ) {
    return "失败";
  }

  if (
    hasMaterializedOutput ||
    ["ready", "passed", "ok", "completed", "success"].includes(normalizedWorkflowStatus)
  ) {
    return "已完成";
  }
  return "进行中";
}

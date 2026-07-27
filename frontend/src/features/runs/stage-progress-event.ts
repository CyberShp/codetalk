type StageEventLike = {
  payload: Record<string, unknown>;
};

export function selectStageProgressEvent<T extends StageEventLike>(
  stageEvents: T[],
  runPartial: boolean,
): T | undefined {
  if (!runPartial) return stageEvents.at(-1);
  return [...stageEvents].reverse().find((item) => {
    const kind = String(item.payload.kind || "");
    const status = String(item.payload.status || "");
    return kind === "stage_timed_out" ||
      kind === "stage_workflow_deadline_exceeded" ||
      status === "partial";
  }) || stageEvents.at(-1);
}

export function selectStageAttemptStart<T extends StageEventLike>(
  stageEvents: T[],
  stageId: string,
): T | undefined {
  return [...stageEvents].reverse().find((item) =>
    item.payload.stage_id === stageId &&
    item.payload.kind === "stage_provider_started"
  );
}

/**
 * The live stream carries test-activity transitions as thinking events, not
 * task lifecycle events. Refresh the persisted summary for those transitions
 * so the nine-stage checklist does not stay at its initial state; deliberately
 * skip token deltas and checkpoints to avoid polling once per streamed chunk.
 */
export function requiresRunSummaryRefresh(event: StageEventLike): boolean {
  const kind = String(event.payload.kind || "");
  if (!kind.startsWith("stage_")) return false;
  return !new Set([
    "stage_output_delta",
    "stage_output_checkpoint",
    "stage_heartbeat",
  ]).has(kind);
}

export function formatStageAttemptLabel(payload: Record<string, unknown>): string {
  const attemptCount = Number(payload.attempt_count ?? 0);
  if (attemptCount > 0) return `${attemptCount} 次完整生成`;
  if (
    String(payload.stage_id || "") === "behavior_claim_validation"
  ) {
    return String(payload.status || "") === "running"
      ? "正在进行事实核验"
      : "事实核验已完成";
  }
  return "未调用模型";
}

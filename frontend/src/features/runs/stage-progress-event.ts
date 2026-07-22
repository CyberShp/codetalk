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

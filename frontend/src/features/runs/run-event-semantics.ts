import type { WorkbenchTaskRunEvent } from "../../lib/types.ts";

const liveKinds = new Set(["output", "thinking", "reasoning", "error"]);
const lifecycleTypes = new Set([
  "step_started",
  "node_started",
  "step_completed",
  "node_completed",
  "step_failed",
  "node_failed",
  "artifact_created",
]);

function nonEmptyText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

export function eventText(event: WorkbenchTaskRunEvent) {
  const value = event.payload.delta
    ?? event.payload.text
    ?? event.payload.output
    ?? event.payload.error
    ?? event.payload.detail
    ?? event.payload.message
    ?? event.payload.content;
  return nonEmptyText(value) ? value.trim() : "";
}

export function isMeaningfulLiveEvent(event: WorkbenchTaskRunEvent) {
  if (!eventText(event)) return false;
  return liveKinds.has(event.event_kind)
    || event.event_type === "activity"
    || event.event_type === "agent_output";
}

export function isStructuredToolEvent(event: WorkbenchTaskRunEvent) {
  if (event.event_kind !== "tool_use" && event.event_kind !== "tool_result") return false;
  const tool = event.payload.tool ?? event.payload.name ?? event.payload.tool_id;
  const callId = event.payload.call_id ?? event.payload.tool_call_id;
  return nonEmptyText(tool) && nonEmptyText(callId);
}

export function selectOverviewActivity(events: WorkbenchTaskRunEvent[]) {
  const latestFirst = [...events].reverse();
  return latestFirst.find(isMeaningfulLiveEvent)
    ?? latestFirst.find((event) => (
      event.event_kind === "artifact" || lifecycleTypes.has(event.event_type)
    ));
}

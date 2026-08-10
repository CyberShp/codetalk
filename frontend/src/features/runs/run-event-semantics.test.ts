import assert from "node:assert/strict";
import test from "node:test";

import type { WorkbenchTaskRunEvent } from "../../lib/types.ts";
import {
  isMeaningfulLiveEvent,
  isStructuredToolEvent,
  selectOverviewActivity,
} from "./run-event-semantics.ts";

function event(
  eventId: number,
  eventKind: WorkbenchTaskRunEvent["event_kind"],
  eventType: string,
  payload: Record<string, unknown>,
): WorkbenchTaskRunEvent {
  return {
    event_id: eventId,
    seq: eventId,
    event_kind: eventKind,
    event_type: eventType,
    task_run_id: "run-test",
    payload,
    created_at: `2026-08-11T01:47:${String(eventId).padStart(2, "0")}Z`,
  };
}

test("legacy provider activity text remains visible in the live transcript", () => {
  const activity = event(1, "diagnostic", "activity", {
    provider: "opencode",
    text: "Step 01 complete. I found the primary call chain.",
  });

  assert.equal(isMeaningfulLiveEvent(activity), true);
  assert.equal(isMeaningfulLiveEvent(event(8, "output", "agent_output", {
    content: "ordinary workflow output",
  })), true);
});

test("session-only provider notifications are not presented as tool calls", () => {
  const sessionUpdate = event(2, "tool_use", "tool_use", {
    provider: "opencode",
    session_id: "session-1",
    resume_session_id: "session-1",
    event_type: "tool_use",
  });

  assert.equal(isStructuredToolEvent(sessionUpdate), false);
  assert.equal(isMeaningfulLiveEvent(sessionUpdate), false);
});

test("tool tab only accepts calls with a real tool identity and call identity", () => {
  const toolUse = event(3, "tool_use", "tool_use", {
    tool: "read_file",
    call_id: "call-1",
    input: { path: "src/main.ts" },
  });
  const toolResult = event(4, "tool_result", "tool_result", {
    tool: "read_file",
    call_id: "call-1",
    output: "export function main() {}",
  });

  assert.equal(isStructuredToolEvent(toolUse), true);
  assert.equal(isStructuredToolEvent(toolResult), true);
  assert.equal(isStructuredToolEvent(event(9, "tool_use", "tool_requested", {
    tool_id: "source.read",
    tool_call_id: "call-2",
    arguments: { path: "src/main.ts" },
  })), true);
});

test("overview chooses useful progress instead of generic session chatter", () => {
  const events = [
    event(5, "diagnostic", "activity", { text: "Analyzing the controller and service boundary." }),
    event(6, "tool_use", "tool_use", { provider: "opencode", session_id: "session-1" }),
    event(7, "status", "step_started", { step_id: "step-02" }),
  ];

  assert.equal(selectOverviewActivity(events)?.event_id, 5);
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const cockpit = readFileSync(
  new URL("../src/features/runs/run-cockpit-page.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
const realE2e = readFileSync(
  new URL("../e2e/phase6-checkpoint-hitl-real.spec.ts", import.meta.url),
  "utf8",
);

test("task-run approval API submits a node decision and reason", () => {
  assert.match(api, /decideApproval:\s*\(/);
  assert.match(api, /\/api\/workbench\/task-runs\/\$\{encodeURIComponent\(taskRunId\)\}\/approvals\/\$\{encodeURIComponent\(nodeId\)\}\/decision/);
  assert.match(api, /decision:\s*"approve"\s*\|\s*"reject"/);
  assert.match(api, /actor:\s*string/);
  assert.match(api, /reason:\s*string/);
  assert.match(api, /decided_at:\s*string/);
});

test("cockpit keeps approval waits active and offers an ergonomic decision surface", () => {
  assert.doesNotMatch(cockpit, /terminalStatuses\s*=\s*new Set\([^;]*waiting_for_input/);
  assert.match(cockpit, /\["queued", "running", "prepared", "waiting_for_input"\]/);
  assert.match(cockpit, /<HumanApprovalPanel/);
  assert.match(cockpit, /api\.workbench\.taskRuns\.decideApproval\(/);
  assert.match(cockpit, /actor:\s*"local-operator"/);
  assert.match(cockpit, /decided_at:\s*new Date\(\)\.toISOString\(\)/);
  assert.doesNotMatch(cockpit, /decideApproval[\s\S]{0,600}taskRuns\.execute\(runId/);
  assert.match(cockpit, /aria-label="审批原因"/);
  assert.match(cockpit, /待审批上下文/);
  assert.match(cockpit, /node\.approval_context\?\.summary/);
  assert.match(cockpit, /批准/);
  assert.match(cockpit, /拒绝/);
});

test("cockpit labels human-input wait lifecycle events and styles the control", () => {
  assert.match(cockpit, /const statusLabel = status === "waiting_for_input"[\s\S]{0,80}"等待人工审批"/);
  assert.match(cockpit, /status_label:\s*statusLabel/);
  assert.match(cockpit, /waiting_for_input:\s*"等待人工审批"/);
  assert.match(cockpit, /node_waiting:\s*"节点等待人工审批"/);
  assert.match(cockpit, /function lifecycleEventNode\(/);
  assert.match(cockpit, /function nodeLifecycleStatus\(/);
  assert.match(cockpit, /events\.filter\(\(item\) => eventNodeId\(item\) === node\.id\)/);
  assert.match(cockpit, /const approvalContext = lifecycleApprovalContext\(nodeEvent\)/);
  assert.match(cockpit, /\.\.\.\(approvalContext \? \{ approval_context: approvalContext \} : \{\}\)/);
  assert.match(cockpit, /status === "waiting_for_input"[\s\S]{0,260}node\.type === "human_approval"/);
  assert.match(styles, /\.ct-v2-human-approval/);
});

test("cockpit announces checkpoint recovery and translates public lifecycle activity", () => {
  assert.match(cockpit, /payload\.source === "checkpoint_projection_rebuild"/);
  assert.match(cockpit, /payload\.source === "startup_recovery"/);
  assert.match(cockpit, /已从检查点恢复/);
  assert.match(cockpit, /node_completed:\s*"节点执行完成"/);
  assert.match(cockpit, /node_checkpoint_committed:\s*"节点进度已持久保存"/);
  assert.match(cockpit, /v3_status_updated:\s*"运行状态已同步"/);
  assert.match(cockpit, /function executionStatusLabel\(/);
  assert.match(cockpit, /V3StatusAxis label="执行" value=\{executionStatusLabel\(axes\.execution\)\}/);
});

test("real HITL evidence clears the injected conflict before waiting screenshots", () => {
  assert.match(
    realE2e,
    /await expect\(approvalError\)\.not\.toContainText\(approvalNodeId\);[\s\S]{0,260}getByLabel\("关闭错误"\)\.click\(\)[\s\S]{0,180}hitl-waiting-desktop\.png/,
  );
});

test("cockpit ignores stale refresh responses and never moves the event cursor backward", () => {
  assert.match(cockpit, /const refreshEpoch = useRef\(0\)/);
  assert.match(cockpit, /const refreshId = \+\+refreshEpoch\.current/);
  assert.match(cockpit, /if \(refreshId !== refreshEpoch\.current\) return/);
  assert.match(
    cockpit,
    /lastEventId\.current = Math\.max\(lastEventId\.current, eventResult\.latest_event_id\)/,
  );
});

test("cockpit resolves internal node IDs to safe labels everywhere users can see or copy them", () => {
  assert.match(cockpit, /function nodeKindLabel\(/);
  assert.match(cockpit, /function publicNodeLabel\(/);
  assert.match(cockpit, /function eventNodeLabel\(/);
  assert.match(cockpit, /nodeLabels\.get\(nodeId\)/);
  assert.match(cockpit, /events\.map\(\(item\) => eventNodeLabel\(item, nodeLabels\)\)/);
  assert.match(cockpit, /<EventRow[^>]*nodeLabels=\{nodeLabels\}/);
  assert.match(cockpit, /eventClipboardLine\(item, nodeLabels\)/);
  assert.match(cockpit, /executionProfileLabel[\s\S]{0,180}: "已冻结执行档位"/);
  assert.doesNotMatch(cockpit, /executionProfileLabel\s*=\s*[^;]*executionProfileId/);
  assert.match(cockpit, /\{publicNodeText\(error, nodeLabels\)\}/);
  assert.match(cockpit, /task_run\|profile/);
  assert.match(cockpit, /human_approval:\s*"人工审批"/);
  assert.doesNotMatch(cockpit, /stage\.name \|\| stage\.stage_id/);
  assert.doesNotMatch(cockpit, /node\.label \|\| node\.id/);
  assert.doesNotMatch(cockpit, /currentNode\?\.label \|\| currentNode\?\.id/);
  assert.doesNotMatch(cockpit, /eventNode\(item\) \|\| "系统"/);
  assert.match(cockpit, /function ArtifactRow\(\{ item, runId, onOpen, nodeLabels \}/);
  assert.match(cockpit, /artifactDisplayName\(path, nodeLabels\)/);
  assert.match(cockpit, /publicNodeText\(item\.relative_path, nodeLabels\)/);
  assert.match(cockpit, /publicNodeText\(preview\.content, nodeLabels\)/);
  assert.match(cockpit, /function InspectorGroup\(\{ label, values, nodeLabels \}/);
  assert.match(cockpit, /values\.map\(\(item\) => publicNodeText\(item, nodeLabels\)\)/);
  assert.match(cockpit, /publicNodeText\(item\.role \|\| item\.id, nodeLabels\)/);
  assert.match(cockpit, /publicNodeText\(item\.value_summary \|\| "已绑定", nodeLabels\)/);
  assert.match(
    cockpit,
    /input\|output\|port\|contract/,
  );
  assert.match(cockpit, /<InputConsumptionPanel ledger=\{run\.input_consumption\} nodeLabels=\{nodeLabels\}/);
  assert.match(cockpit, /publicNodeText\(input\.label \|\| input\.input_id, nodeLabels\)/);
});

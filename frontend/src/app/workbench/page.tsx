import { WorkbenchEntryGate } from "@/features/release/workbench-entry-gate";

export default function AgentWorkbenchPage() {
  return <WorkbenchEntryGate destination="/tasks" legacyView="run" />;
}

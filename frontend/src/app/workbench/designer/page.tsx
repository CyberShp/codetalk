import { WorkbenchEntryGate } from "@/features/release/workbench-entry-gate";

export default function WorkflowDesignerPage() {
  return <WorkbenchEntryGate destination="/workflows" legacyView="workflow" />;
}

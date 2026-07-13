import { WorkbenchEntryGate } from "@/features/release/workbench-entry-gate";

export default function WorkbenchSemanticPage() {
  return <WorkbenchEntryGate destination="/semantic-library" legacyView="knowledge" />;
}

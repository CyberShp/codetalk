import { Suspense } from "react";
import { WorkflowWizard } from "@/features/workflows/workflow-wizard/workflow-wizard";

export default function LegacyWorkflowCreatePage() {
  return <Suspense fallback={<p>正在加载 V2 兼容编辑器...</p>}><WorkflowWizard /></Suspense>;
}

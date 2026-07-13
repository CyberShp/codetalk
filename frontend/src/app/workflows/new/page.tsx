import { Suspense } from "react";
import { WorkflowWizard } from "@/features/workflows/workflow-wizard/workflow-wizard";

export default function NewWorkflowPage() {
  return <Suspense fallback={<div className="ct-v2-page-loading">正在打开工作流向导…</div>}><WorkflowWizard /></Suspense>;
}

import { WorkflowDesigner } from "@/features/workflows/designer/workflow-designer";

export default async function WorkflowDesignerRoute({ params }: { params: Promise<{ workflowId: string }> }) {
  const { workflowId } = await params;
  return <WorkflowDesigner workflowId={workflowId} />;
}

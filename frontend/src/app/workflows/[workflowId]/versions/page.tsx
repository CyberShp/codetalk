import { WorkflowVersionsPage } from "@/features/workflows/workflow-versions-page";

export default async function WorkflowVersionsRoute({ params }: { params: Promise<{ workflowId: string }> }) {
  const { workflowId } = await params;
  return <WorkflowVersionsPage workflowId={workflowId} />;
}

import { WorkflowVersionDetailPage } from "@/features/workflows/workflow-version-detail-page";

export default async function WorkflowVersionRoute({ params }: { params: Promise<{ workflowId: string; versionId: string }> }) {
  const { workflowId, versionId } = await params;
  return <WorkflowVersionDetailPage workflowId={workflowId} versionId={versionId} />;
}

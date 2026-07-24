import { request } from "@/lib/api";
import type {
  AuthoringGraphV2,
  WorkflowCapabilities,
  WorkflowCompileResult,
  WorkflowDetail,
  WorkflowHeader,
  WorkflowListItem,
  WorkflowNodeRegistry,
  WorkflowProviderCapability,
  WorkflowValidationResult,
  WorkflowVersion,
  WorkflowTrialRunResult,
} from "@/lib/types/workflow";

const workflowPath = (workflowId: string) =>
  `/api/workbench/workflows/${encodeURIComponent(workflowId)}`;

export const workflowsApi = {
  list: () => request<WorkflowListItem[]>("/api/workbench/workflows"),
  get: (workflowId: string) =>
    request<WorkflowDetail>(workflowPath(workflowId)),
  create: (payload: {
    id: string;
    name: string;
    description: string;
    authoring_graph: AuthoringGraphV2;
  }) =>
    request<WorkflowHeader>("/api/workbench/workflows", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateHeader: (
    workflowId: string,
    payload: { name?: string; description?: string },
  ) =>
    request<WorkflowHeader>(workflowPath(workflowId), {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  archive: (workflowId: string) =>
    request<WorkflowHeader>(`${workflowPath(workflowId)}/archive`, {
      method: "POST",
    }),
  versions: (workflowId: string) =>
    request<{ items: WorkflowVersion[] }>(`${workflowPath(workflowId)}/versions`),
  version: (workflowId: string, versionId: string) =>
    request<WorkflowVersion>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}`,
    ),
  createDraft: (workflowId: string, basedOnVersionId?: string) =>
    request<WorkflowVersion>(`${workflowPath(workflowId)}/versions`, {
      method: "POST",
      body: JSON.stringify({ based_on_version_id: basedOnVersionId ?? null }),
    }),
  updateDraft: (
    workflowId: string,
    versionId: string,
    authoringGraph: AuthoringGraphV2,
  ) =>
    request<WorkflowVersion>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}`,
      { method: "PUT", body: JSON.stringify({ authoring_graph: authoringGraph }) },
    ),
  validate: (workflowId: string, versionId: string) =>
    request<WorkflowValidationResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/validate`,
      { method: "POST" },
    ),
  compile: (workflowId: string, versionId: string) =>
    request<WorkflowCompileResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/compile`,
      { method: "POST" },
    ),
  publish: (workflowId: string, versionId: string) =>
    request<WorkflowVersion>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/publish`,
      { method: "POST", body: "{}" },
    ),
  testRun: (
    workflowId: string,
    versionId: string,
    payload: { workspace_id: string; inputs: Record<string, unknown>; node_id?: string },
  ) =>
    request<WorkflowTrialRunResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/test-run`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  capabilities: () =>
    request<WorkflowCapabilities>("/api/workbench/workflow-capabilities"),
  nodeRegistry: () =>
    request<WorkflowNodeRegistry>("/api/workbench/node-registry"),
  providers: () =>
    request<{ providers: WorkflowProviderCapability[] }>(
      "/api/workbench/provider-capabilities",
    ),
};

import { request } from "@/lib/api";
import type {
  AuthoringGraph,
  WorkflowCapabilities,
  WorkflowCanvasCreateResult,
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

import { workflowRevisionBody } from "./workflow-action-contract";

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
    authoring_graph: AuthoringGraph;
  }) =>
    request<WorkflowHeader>("/api/workbench/workflows", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createCanvas: (payload: {
    template: "blank" | "free_source_analysis";
    name: string;
    description?: string;
  }) =>
    request<WorkflowCanvasCreateResult>("/api/workbench/workflows/new", {
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
  copyAsCustomDraft: (workflowId: string) =>
    request<WorkflowVersion>(`${workflowPath(workflowId)}/copy`, {
      method: "POST",
      body: "{}",
    }),
  copyVersionToV3: (workflowId: string, versionId: string) =>
    request<{
      workflow: WorkflowHeader;
      draft: WorkflowVersion;
      designer_url: string;
      migration_preview: Record<string, unknown>;
    }>(`${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/copy-to-v3`, {
      method: "POST",
      body: "{}",
    }),
  updateDraft: (
    workflowId: string,
    versionId: string,
    authoringGraph: AuthoringGraph,
    expectedRevision?: number,
  ) =>
    request<WorkflowVersion>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          authoring_graph: authoringGraph,
          ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
        }),
      },
    ),
  addNode: (
    workflowId: string,
    versionId: string,
    payload: { kind: string; position: { x: number; y: number }; label?: string; config?: Record<string, unknown> },
    expectedRevision: number,
  ) =>
    request<{ node: unknown; draft: WorkflowVersion }>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/nodes`,
      { method: "POST", body: JSON.stringify({ ...payload, expected_revision: expectedRevision }) },
    ),
  addPort: (
    workflowId: string,
    versionId: string,
    nodeId: string,
    payload: {
      direction: "input" | "output";
      label: string;
      type: string;
      required?: boolean;
      collection?: boolean;
    },
    expectedRevision: number,
  ) =>
    request<{ port: unknown; draft: WorkflowVersion }>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/nodes/${encodeURIComponent(nodeId)}/ports`,
      { method: "POST", body: JSON.stringify({ ...payload, expected_revision: expectedRevision, direction: payload.direction === "input" ? "inputs" : "outputs" }) },
    ),
  updatePort: (
    workflowId: string,
    versionId: string,
    nodeId: string,
    portId: string,
    payload: { label?: string; type?: string; required?: boolean; collection?: boolean },
    expectedRevision: number,
  ) =>
    request<{ port: unknown; draft: WorkflowVersion }>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/nodes/${encodeURIComponent(nodeId)}/ports/${encodeURIComponent(portId)}`,
      { method: "PATCH", body: JSON.stringify({ ...payload, expected_revision: expectedRevision }) },
    ),
  deletePort: (
    workflowId: string,
    versionId: string,
    nodeId: string,
    portId: string,
    expectedRevision: number,
  ) =>
    request<{ draft: WorkflowVersion }>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/nodes/${encodeURIComponent(nodeId)}/ports/${encodeURIComponent(portId)}`,
      { method: "DELETE", body: JSON.stringify({ expected_revision: expectedRevision }) },
    ),
  addEdge: (
    workflowId: string,
    versionId: string,
    payload: {
      source: { node_id: string; port_id: string };
      target: { node_id: string; port_id: string };
    },
    expectedRevision: number,
  ) =>
    request<{ edge: unknown; draft: WorkflowVersion }>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/edges`,
      { method: "POST", body: JSON.stringify({ ...payload, expected_revision: expectedRevision }) },
    ),
  validate: (workflowId: string, versionId: string, expectedRevision?: number) =>
    request<WorkflowValidationResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/validate`,
      { method: "POST", body: workflowRevisionBody(expectedRevision) },
    ),
  compile: (workflowId: string, versionId: string, expectedRevision?: number) =>
    request<WorkflowCompileResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/compile`,
      { method: "POST", body: workflowRevisionBody(expectedRevision) },
    ),
  publish: (workflowId: string, versionId: string, expectedRevision?: number) =>
    request<WorkflowVersion>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/publish`,
      { method: "POST", body: workflowRevisionBody(expectedRevision) },
    ),
  testRun: (
    workflowId: string,
    versionId: string,
    payload: { workspace_id: string; inputs: Record<string, unknown>; node_id?: string; expected_revision?: number },
  ) =>
    request<WorkflowTrialRunResult>(
      `${workflowPath(workflowId)}/versions/${encodeURIComponent(versionId)}/test-run`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  capabilities: () =>
    request<WorkflowCapabilities>("/api/workbench/workflow-capabilities"),
  nodeRegistry: (schemaVersion: 2 | 3 = 3) =>
    request<WorkflowNodeRegistry>(`/api/workbench/node-registry?schema_version=${schemaVersion}`),
  providers: () =>
    request<{ providers: WorkflowProviderCapability[]; meta?: import("@/lib/types/workflow").WorkflowResourceMeta }>(
      "/api/workbench/provider-capabilities",
    ),
};

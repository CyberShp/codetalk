import { currentApiBase } from "./api";

export type KnowledgeScope = "project" | "personal_global";
export type PatternReviewState = "unreviewed" | "confirmed" | "rejected";
export type PatternLifecycleState = "active" | "superseded" | "deprecated";

export interface KnowledgeProvenance {
  source_snapshot_id: string;
  source_document_id: string;
  source_kind: string;
  source_identity: string;
  sha256: string;
  revision: string;
  scope: KnowledgeScope;
  workspace_identity: string;
  locators: Record<string, unknown>[];
}

export interface KnowledgeIncident {
  incident_id: string;
  title: string;
  summary: string;
  terms: string[];
  scope: KnowledgeScope;
  workspace_identity: string;
  status: string;
  provenance?: KnowledgeProvenance[];
  record_type?: "incident";
}

export interface KnowledgePatternVersion {
  pattern_version_id: string;
  pattern_id: string;
  version_number: number;
  content: string;
  terms: string[];
  applicability: string[];
  exclusions: string[];
  created_at: string;
}

export interface KnowledgePattern {
  pattern_id: string;
  name: string;
  scope: KnowledgeScope;
  workspace_identity: string;
  active_version_id: string;
  review_state: PatternReviewState;
  lifecycle_state: PatternLifecycleState;
  content?: string;
  version_number?: number;
  terms?: string[];
  applicability?: string[];
  exclusions?: string[];
  versions?: KnowledgePatternVersion[];
  incidents?: KnowledgeIncident[];
  record_type?: "pattern";
}

export interface KnowledgeImportStage {
  job_id: string;
  stage: string;
  status: string;
  attempt: number;
  processed_count: number;
  error: string;
  updated_at: string;
}

export interface KnowledgeImportJob {
  job_id: string;
  source_count: number;
  scope: KnowledgeScope;
  workspace_identity: string;
  status: string;
  created_at: string;
  updated_at: string;
  stages: KnowledgeImportStage[];
}

export interface CodeHubRequest {
  mr_url: string;
  allowed_operations: ["read"];
  max_reference_hops: 1;
  search_enabled: false;
}

export interface KnowledgeImportResult {
  job: KnowledgeImportJob;
  sources: {
    filename: string;
    source_snapshot_id: string;
    sha256: string;
    duplicate: boolean;
    parser: string;
    parse_status: string;
    parse_error: string;
  }[];
  scope: {
    scope: KnowledgeScope;
    workspace_identity: string;
    reason: string;
  };
  codehub_request: CodeHubRequest | null;
  extraction: {
    status: "pending_agent_enrichment";
    job_id: string;
    action: "Agent extraction";
    agent_execution: "not_started";
  };
}

export interface KnowledgeAgentEnrichmentResult {
  job_id: string;
  status: "agent_enrichment_running";
  task_run_id: string;
  provider: string;
  codehub_request: CodeHubRequest | null;
}

export interface KnowledgeFeedback {
  feedback_id: string;
  subject_type: string;
  subject_id: string;
  outcome: string;
  note: string;
}

async function knowledgeRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  const response = await fetch(`${currentApiBase()}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : `知识中心请求失败 (${response.status})`;
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function queryPath(
  path: string,
  params: Record<string, string | number | undefined>,
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && String(value).trim()) query.set(key, String(value));
  });
  const encoded = query.toString();
  return encoded ? `${path}?${encoded}` : path;
}

export function listKnowledgeIncidents(options: {
  query?: string;
  scope?: KnowledgeScope;
  workspaceIdentity?: string;
  limit?: number;
} = {}): Promise<KnowledgeIncident[]> {
  return knowledgeRequest(
    queryPath("/api/knowledge-center/incidents", {
      query: options.query,
      scope: options.scope,
      workspace_identity: options.workspaceIdentity,
      limit: options.limit,
    }),
  );
}

export function getKnowledgeIncident(
  incidentId: string,
): Promise<KnowledgeIncident> {
  return knowledgeRequest(
    `/api/knowledge-center/incidents/${encodeURIComponent(incidentId)}`,
  );
}

export function listKnowledgePatterns(options: {
  query?: string;
  scope?: KnowledgeScope;
  workspaceIdentity?: string;
  limit?: number;
} = {}): Promise<KnowledgePattern[]> {
  return knowledgeRequest(
    queryPath("/api/knowledge-center/patterns", {
      query: options.query,
      scope: options.scope,
      workspace_identity: options.workspaceIdentity,
      limit: options.limit,
    }),
  );
}

export function getKnowledgePattern(
  patternId: string,
): Promise<KnowledgePattern> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}`,
  );
}

export function createKnowledgePattern(input: {
  name: string;
  content: string;
  scope: KnowledgeScope;
  workspace_identity?: string;
  terms?: string[];
  applicability?: string[];
  exclusions?: string[];
}): Promise<KnowledgePattern> {
  return knowledgeRequest("/api/knowledge-center/patterns", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function addKnowledgePatternVersion(
  patternId: string,
  input: {
    content: string;
    terms?: string[];
    applicability?: string[];
    exclusions?: string[];
  },
): Promise<KnowledgePatternVersion> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}/versions`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function listKnowledgePatternVersions(
  patternId: string,
): Promise<KnowledgePatternVersion[]> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}/versions`,
  );
}

export function restoreKnowledgePatternVersion(
  patternId: string,
  versionId: string,
): Promise<KnowledgePattern> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}/restore/${encodeURIComponent(versionId)}`,
    { method: "POST" },
  );
}

export function reviewKnowledgePattern(
  patternId: string,
  reviewState: PatternReviewState,
): Promise<KnowledgePattern> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}/review`,
    { method: "POST", body: JSON.stringify({ review_state: reviewState }) },
  );
}

export function updateKnowledgePatternLifecycle(
  patternId: string,
  lifecycleState: PatternLifecycleState,
): Promise<KnowledgePattern> {
  return knowledgeRequest(
    `/api/knowledge-center/patterns/${encodeURIComponent(patternId)}/lifecycle`,
    { method: "POST", body: JSON.stringify({ lifecycle_state: lifecycleState }) },
  );
}

export function listKnowledgeImportJobs(
  limit = 50,
): Promise<KnowledgeImportJob[]> {
  return knowledgeRequest(
    queryPath("/api/knowledge-center/import-jobs", { limit }),
  );
}

export function getKnowledgeImportJob(
  jobId: string,
): Promise<KnowledgeImportJob> {
  return knowledgeRequest(
    `/api/knowledge-center/import-jobs/${encodeURIComponent(jobId)}`,
  );
}

export function retryKnowledgeImportStage(
  jobId: string,
  stage: string,
): Promise<KnowledgeImportJob> {
  return knowledgeRequest(
    `/api/knowledge-center/import-jobs/${encodeURIComponent(jobId)}/retry`,
    { method: "POST", body: JSON.stringify({ stage }) },
  );
}

export function startKnowledgeAgentEnrichment(
  jobId: string,
  provider = "claude-code",
): Promise<KnowledgeAgentEnrichmentResult> {
  return knowledgeRequest(
    `/api/knowledge-center/import-jobs/${encodeURIComponent(jobId)}/agent-enrichment`,
    { method: "POST", body: JSON.stringify({ provider }) },
  );
}

export function importKnowledgePaste(input: {
  text: string;
  filename?: string;
  scope: KnowledgeScope;
  workspace_identity?: string;
  workspace_remotes?: string[];
  mr_project_identity?: string;
  mr_url?: string;
  keep_links?: boolean;
}): Promise<KnowledgeImportResult> {
  return knowledgeRequest("/api/knowledge-center/imports/paste", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function importKnowledgeFiles(
  files: File[],
  options: {
    scope: KnowledgeScope;
    workspaceIdentity?: string;
    workspaceRemotes?: string[];
    mrProjectIdentity?: string;
    mrUrl?: string;
    keepLinks?: boolean;
  },
): Promise<KnowledgeImportResult> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file, file.name));
  body.set("scope", options.scope);
  body.set("workspace_identity", options.workspaceIdentity ?? "");
  body.set("workspace_remotes", (options.workspaceRemotes ?? []).join("\n"));
  body.set("mr_project_identity", options.mrProjectIdentity ?? "");
  body.set("mr_url", options.mrUrl ?? "");
  body.set("keep_links", String(options.keepLinks ?? false));
  return knowledgeRequest("/api/knowledge-center/imports/files", {
    method: "POST",
    body,
  });
}

export function recordKnowledgeFeedback(input: {
  subject_type: "incident" | "pattern" | "import_job" | "retrieval";
  subject_id: string;
  outcome: "useful" | "irrelevant" | "confirmed" | "ruled_out";
  workspace_identity?: string;
  note?: string;
}): Promise<KnowledgeFeedback> {
  return knowledgeRequest("/api/knowledge-center/feedback", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export type WorkbenchTaskLifecycle = "draft" | "ready" | "archived";

export type WorkbenchArtifactValidationStatus =
  | "not_requested"
  | "not_started"
  | "running"
  | "passed"
  | "failed";

export type WorkbenchGovernanceStatus =
  | "not_requested"
  | "running"
  | "passed"
  | "warning"
  | "failed"
  | "waived";

export type WorkbenchDeliveryStatus =
  | "none"
  | "partial"
  | "complete"
  | "pending"
  | "ready"
  | "blocked";

export interface WorkbenchRunSummary {
  task_run_id: string;
  task_id: string;
  attempt_number: number;
  parent_task_run_id: string;
  workflow_id: string;
  workspace_id: string;
  compiled_contract_version?: number;
  execution_status: string;
  /** Legacy V1/V2 quality projection. V3 exposes the two explicit axes below. */
  quality_status: string;
  artifact_validation_status?: WorkbenchArtifactValidationStatus;
  governance_status?: WorkbenchGovernanceStatus;
  delivery_status: WorkbenchDeliveryStatus;
  started_at: string;
  completed_at: string;
  created_at: string;
  legacy?: boolean;
}

export interface WorkbenchTask {
  task_id: string;
  name: string;
  description: string;
  workspace_id: string;
  workspace_name: string;
  skill_id: string;
  skill_name: string;
  skill_version_id: string;
  skill_content_digest: string;
  lifecycle_status: WorkbenchTaskLifecycle;
  execution_profile_id: string;
  input_values: Record<string, unknown>;
  execution_overrides: Record<string, unknown>;
  output_overrides: Record<string, unknown>;
  tags: string[];
  last_run_id: string | null;
  latest_run: WorkbenchRunSummary | null;
  runs?: WorkbenchRunSummary[];
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  skill_version?: {
    version_id: string;
    skill_id: string;
    content_digest: string;
    ir: Record<string, unknown>;
  };
  ai_origins?: Array<{
    conversation_id: string;
    message_id: string;
    ai_run_id: string;
    task_run_id?: string;
    relation_type: string;
    created_at: string;
  }>;
}

export interface WorkbenchTaskListQuery {
  q?: string;
  lifecycle_status?: string;
  execution_status?: string;
  quality_status?: string;
  skill_id?: string;
  workspace_id?: string;
  page?: number;
  page_size?: number;
}

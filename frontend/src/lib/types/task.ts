export type WorkbenchTaskLifecycle = "draft" | "ready" | "archived";

export interface WorkbenchRunSummary {
  task_run_id: string;
  task_id: string;
  attempt_number: number;
  parent_task_run_id: string;
  workflow_id: string;
  workspace_id: string;
  execution_status: string;
  quality_status: string;
  delivery_status: string;
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
  workflow_id: string;
  workflow_name: string;
  workflow_version_id: string;
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
  workflow_version?: {
    version_id: string;
    version_number: number;
    compiled_definition: Record<string, unknown>;
    compiled_plan: Record<string, unknown>;
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
  workflow_id?: string;
  workspace_id?: string;
  page?: number;
  page_size?: number;
}

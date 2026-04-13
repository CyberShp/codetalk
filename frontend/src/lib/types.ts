/* ── TypeScript types mirroring backend Pydantic schemas ── */

export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  repo_count: number;
}

export interface ProjectCreate {
  name: string;
  description?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
}

export type SourceType = "git_url" | "local_path" | "zip_upload";

export interface Repository {
  id: string;
  project_id: string;
  name: string;
  source_type: SourceType;
  source_uri: string;
  local_path: string | null;
  branch: string;
  last_indexed_at: string | null;
  created_at: string;
}

export interface RepositoryCreate {
  name: string;
  source_type: SourceType;
  source_uri: string;
  branch?: string;
}

export type TaskType = "full_repo" | "file_paths" | "mr_diff";
export type TaskStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface AnalysisTask {
  id: string;
  repository_id: string;
  task_type: TaskType;
  status: TaskStatus;
  tools: string[];
  ai_enabled: boolean;
  progress: number;
  error: string | null;
  ai_summary: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface TaskCreate {
  repository_id: string;
  task_type: TaskType;
  tools: string[];
  ai_enabled?: boolean;
  target_spec?: Record<string, unknown>;
}

export interface ToolRun {
  id: string;
  tool_name: string;
  status: TaskStatus;
  started_at: string | null;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface TaskDetail extends AnalysisTask {
  tool_runs: ToolRun[];
}

export interface LLMConfig {
  id: string;
  provider: string;
  model_name: string;
  is_default: boolean;
  created_at: string;
}

export interface LLMConfigCreate {
  provider: string;
  model_name: string;
  is_default?: boolean;
}

export type ToolCapability =
  | "code_search"
  | "call_graph"
  | "dependency_graph"
  | "taint_analysis"
  | "security_scan"
  | "documentation"
  | "knowledge_graph"
  | "architecture_diagram"
  | "pointer_analysis"
  | "ast_analysis";

export interface ToolInfo {
  name: string;
  capabilities: ToolCapability[];
  healthy: boolean;
  message: string;
}

export interface LogEntry {
  timestamp: string;
  level: "info" | "warn" | "error" | "debug";
  message: string;
  tool?: string;
}

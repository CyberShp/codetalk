/* ── Lightweight CodeTalk types matching backend SQLite schemas ── */

export type TaskStatus = "pending" | "running" | "completed" | "completed_with_warnings" | "failed";

export interface Task {
  id: string;
  name: string;
  repo_path: string;
  status: TaskStatus;
  tools: string[];
  requirements_doc: string | null;
  design_doc: string | null;
  analysis_focus: string | null;
  prompt_content: string | null;
  progress: number;
  error_message: string | null;
  current_step: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskStep {
  timestamp: string;
  progress: number;
  step: string;
}

export interface TaskCreate {
  name: string;
  repo_path: string;
  tools: string[];
  requirements_doc?: string;
  design_doc?: string;
  analysis_focus?: string;
  prompt_content?: string;
}

export interface PromptTemplate {
  id: string;
  name: string;
  content: string;
  is_system: boolean;
  created_at: string;
}

export interface PromptTemplateCreate {
  name: string;
  content: string;
}

export interface PromptTemplateUpdate {
  name?: string;
  content?: string;
}

export type ApiType = "anthropic" | "openai_compat";

export interface LLMConfig {
  id: string;
  name: string;
  api_type: ApiType;
  base_url: string;
  model: string;
  max_tokens: number;
  temperature: number;
  config_json: string | null;
  is_chat_model: boolean;
  is_embedding_model: boolean;
  created_at: string;
}

export interface LLMConfigCreate {
  name: string;
  api_type: ApiType;
  base_url: string;
  api_key: string;
  model: string;
  max_tokens?: number;
  temperature?: number;
  config_json?: string;
  is_chat_model?: boolean;
  is_embedding_model?: boolean;
}

export interface LLMConfigUpdate {
  name?: string;
  api_type?: ApiType;
  base_url?: string;
  api_key?: string;
  model?: string;
  max_tokens?: number;
  temperature?: number;
  config_json?: string;
  is_chat_model?: boolean;
  is_embedding_model?: boolean;
}

export interface GeneralSettings {
  proxy_mode: "none" | "system" | "custom";
  proxy_url: string;
  ssl_cert_path: string;
  active_chat_model_id: string;
  active_embedding_model_id: string;
}

export type ToolStatusValue = "running" | "stopped" | "error" | "unknown";

export interface ToolInfo {
  name: string;
  display_name: string;
  healthy: boolean;
  status: string;
  pid?: number;
  health_url?: string;
  last_check?: string;
  message?: string;
}

export type ExportFormat = "md" | "docx" | "xml";

export interface ChatMessage {
  id: number;
  task_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

/* ── Coverage analysis types ── */

export type CoverageStatus = "parsed" | "analyzing" | "analyzed";

export interface CoverageAnalysis {
  id: string;
  name: string;
  source_type: string;
  status: CoverageStatus;
  overall_line_rate: number;
  overall_branch_rate: number;
  overall_function_rate: number;
  module_count: number;
  source_format: string;
  created_at: string;
  updated_at: string;
}

export interface CoverageDetail extends CoverageAnalysis {
  modules_json: string | null;
  analysis_results_json: string | null;
}

export interface CoverageModuleResult {
  module_path: string;
  line_rate: number;
  branch_rate: number;
  function_rate: number;
  analysis?: string;
  error?: string;
  uncovered_function_count?: number;
  uncovered_branch_count?: number;
}

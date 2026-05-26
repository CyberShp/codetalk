/* ── Lightweight CodeTalk types matching backend SQLite schemas ── */

export type TaskStatus = "pending" | "running" | "completed" | "completed_with_warnings" | "failed" | "cancelled";

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
  deepwiki_depth?: "fast" | "balanced" | "deep";
  material_ids: string[];
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
  deepwiki_depth?: "fast" | "balanced" | "deep";
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

/* ── Workspace types (V2) ── */

export type WorkspaceMaterialType = "requirements" | "design" | "other";
export type WorkspaceReportStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed";

export interface WorkspaceMaterial {
  id: string;
  workspace_id: string;
  filename: string;
  content_type: WorkspaceMaterialType;
  file_path: string;
  is_active: boolean;
  created_at: string;
}

export interface WorkspaceReportMeta {
  id: string;
  workspace_id: string;
  report_type: string;
  title: string | null;
  status: WorkspaceReportStatus;
  created_at: string;
}

export interface WorkspaceReport extends WorkspaceReportMeta {
  content: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  repo_path: string;
  indexed: number;  // 0=indexing, 1=done, -1=failed
  index_job: string | null;
  index_progress: number;  // 0-100 while indexed===0
  analyze_status: string | null;
  analyze_progress: number;
  last_index_error: string | null;
  created_at: string;
  updated_at: string;
  materials: WorkspaceMaterial[];
  reports: WorkspaceReportMeta[];
}

export interface WorkspaceCreate {
  name: string;
  repo_path: string;
}

export type ChatMode = "targeted" | "freeqa";

export interface WorkspaceChatMessage {
  id: string;
  workspace_id: string;
  mode: ChatMode;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface WorkspaceModule {
  id: string;
  name: string;
}

/* ── Analysis plan / scope preview (workspace analysis modal) ── */

export type AnalysisObjectKind =
  | "topic"
  | "module"
  | "flow"
  | "file"
  | "function"
  | "mixed";
export type AnalysisObjectPriority = "high" | "medium" | "low";

export interface AnalysisObject {
  id: string;
  text: string;
  kind: AnalysisObjectKind;
  priority: AnalysisObjectPriority;
}

export interface FocusOptions {
  key_flows: boolean;
  exception_branches: boolean;
  exception_propagation: boolean;
  boundary_values: boolean;
  long_running_flip: boolean;
  state_machine: boolean;
  resource_cleanup: boolean;
  concurrency: boolean;
  observability: boolean;
  sfmea: boolean;
  cpp_implicit_logic: boolean;
  security_risk: boolean;
}

export interface ReportSpec {
  id: string;
  title: string;
  enabled: boolean;
  template_id: string;
  custom: boolean;
  audience?: string | null;
  questions: string[];
  output_format?: string | null;
  max_sections?: number | null;
  max_length_chars?: number | null;
}

export interface LLMLimits {
  max_evidence_cards: number;
  max_files_per_object: number;
  max_functions_per_object: number;
  max_communities_per_object: number;
  max_cards_per_report_section: number;
  max_output_chars_per_section: number;
  retry_empty_output: number;
  max_analysis_units: number;
}

export interface AnalysisPlan {
  version: "workspace-analysis-plan-v1";
  analysis_objects: AnalysisObject[];
  focus: FocusOptions;
  reports: ReportSpec[];
  user_guidance: string;
  llm_limits: LLMLimits;
}

export type ScopeCandidateSource =
  | "gitnexus"
  | "repo_search"
  | "material"
  | "manual";
export type ScopeCandidateConfidence = "high" | "medium" | "low";

export interface ScopeCandidate {
  path?: string | null;
  symbol?: string | null;
  source: ScopeCandidateSource;
  confidence: ScopeCandidateConfidence;
  reason: string;
}

export interface ResolvedAnalysisObject {
  object_id: string;
  text: string;
  candidate_files: ScopeCandidate[];
  candidate_symbols: ScopeCandidate[];
  related_communities: string[];
  warnings: string[];
}

export interface ScopePreview {
  workspace_id: string;
  resolved_objects: ResolvedAnalysisObject[];
  estimated_analysis_units: number;
  estimated_evidence_cards: number;
  warnings: string[];
  gitnexus_available: boolean;
}

export interface ChatRequest {
  message: string;
  mode: ChatMode;
  module?: string;
}

export interface EmbeddingStatus {
  active_materials: number;
  embedded_materials: number;
  total_chunks: number;
  rag_ready: boolean;
}

/* ── DeepWiki types (V2) ── */

export type DeepWikiStatus = "pending" | "running" | "completed" | "failed";

export interface DeepWikiPage {
  id: string;
  title: string;
  content: string;
  filePaths?: string[];
  importance?: string;
  relatedPages?: string[];
}

export interface DeepWikiRepo {
  id: string;
  repo_path: string;
  name: string;
  page_count: number;
  status: DeepWikiStatus;
  progress: number;
  created_at: string;
  updated_at: string;
}

export interface DeepWikiRepoCreate {
  name: string;
  repo_path: string;
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

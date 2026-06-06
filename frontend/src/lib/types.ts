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
  event_type?: string;
  phase?: string;
  target?: Record<string, unknown>;
  detail?: Record<string, unknown>;
  level?: string;
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
  managed?: boolean;
  pid?: number;
  health_url?: string;
  last_check?: string;
  message?: string;
  version?: string | null;
  capabilities?: string[];
}

export interface ExternalAgentProbeAttempt {
  command?: string;
  status?: string;
  reason?: string;
  launch_kind?: string;
}

export interface ExternalAgentStartupProbeResult {
  provider: string;
  healthy: boolean;
  status: string;
  message: string;
  warnings?: string[];
  stdout?: string;
  stderr?: string;
  health?: {
    reason?: string;
    command?: string;
    configured_command?: string;
    path?: string;
    launch_kind?: string;
    used_fallback?: boolean;
    attempts?: ExternalAgentProbeAttempt[];
    diagnostic?: {
      summary?: string;
      cwd?: string;
      path_entries?: string[];
      path_entry_count?: number;
    };
  };
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
  task_id: string | null;
  report_type: string;
  title: string | null;
  status: WorkspaceReportStatus;
  created_at: string;
}

export interface WorkspaceVersion {
  task_id: string;
  status: string;
  progress: number;
  material_ids: string[];
  created_at: string;
  updated_at: string;
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

export type ChatMode = "targeted" | "freeqa" | "report_qa";

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
export type ScopeHintRole = "primary" | "supporting" | "external";

export interface ScopeHint {
  path: string;
  role: ScopeHintRole;
}

export interface AnalysisObject {
  id: string;
  text: string;
  kind: AnalysisObjectKind;
  priority: AnalysisObjectPriority;
  path_hints?: string[];
  scope_hints?: ScopeHint[];
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
  | "external_agent"
  | "material"
  | "manual";
export type ScopeCandidateConfidence = "high" | "medium" | "low";

export interface ScopeCandidate {
  path?: string | null;
  symbol?: string | null;
  source: ScopeCandidateSource;
  confidence: ScopeCandidateConfidence;
  reason: string;
  role?: "primary" | "supporting" | "related" | "external" | null;
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
  external_agent_status?: Record<string, string>;
  external_agent_warnings?: string[];
  agent_discovery_session_id?: string | null;
  external_agent_turn_count?: number;
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
  workspace_id?: string | null;
  repo_path?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CoverageDetail extends CoverageAnalysis {
  modules_json: string | null;
  analysis_results_json: string | null;
}

/* coverage-test-design-v1 enrichment */

export interface CoverageTriggerBranch {
  condition: string;
  source?: string; // "self" | "caller"
  file?: string | null;
  line?: string;
  line_number?: number | null;
  category?: string;
  is_error_path?: boolean;
}

export interface CoverageEntryPath {
  entry_kind: string; // cli | api | message | config | file | callback | timer | service
  entry_symbol?: string | null;
  entry_file?: string | null;
  entry_label?: string | null;
  chain?: string[];
  depth?: number;
  call_line?: number | null;
  evidence?: string | null;
  tool?: string;
  provider?: string | null;
  turn_id?: string | null;
  source_verification?: string;
  validation_error?: string | null;
  input_hints?: string[];
}

export interface CoverageEntryDiscoveryCandidate {
  entry_type: string;
  entry_symbol?: string | null;
  entry_file?: string | null;
  entry_label?: string | null;
  chain?: string[];
  evidence?: string | null;
  confidence?: string;
  source_verification?: string;
  tool?: string;
  provider?: string | null;
  turn_id?: string | null;
  validation_error?: string | null;
  input_hints?: string[];
}

export interface CoverageExternalAgentContext {
  status?: string;
  provider_status?: Record<string, string>;
  validated_entry_count?: number;
  unverified_entries?: CoverageEntryDiscoveryCandidate[];
  warnings?: string[];
}

export interface CoverageEntryDiscoveryCard {
  function_name?: string | null;
  file_path?: string | null;
  module_path?: string | null;
  entry_trace_status?: string;
  candidate_external_entries?: CoverageEntryDiscoveryCandidate[];
  external_agent?: CoverageExternalAgentContext;
  report_material_clues?: Array<Record<string, unknown>>;
  source_verification_status?: string;
  unresolved_reasons?: string[];
  gray_box_allowed?: boolean;
}

export interface CoverageBlackBoxCase {
  title: string;
  entry_kind?: string;
  preconditions?: string;
  inputs?: string;
  steps?: string[];
  expected?: string;
  observable_signals?: string[];
  evidence?: string | null;
}

export interface CoverageGrayBox {
  required?: boolean;
  technique?: string;
  scheme?: string;
  injection_points?: string[];
  stub_or_fault?: string;
  observable_signals?: string[];
}

export interface CoverageSourceWindow {
  available?: boolean;
  path?: string;
  definition_line?: number;
  start?: number;
  end?: number;
  tool?: string;
}

export interface CoverageToolStatus {
  joern?: string;
  cgc?: string;
  gitnexus?: string;
  external_agent?: string;
  ripgrep?: string;
  source?: string;
}

export interface CoverageSfmea {
  failure_mode?: string;
  trigger_condition?: string;
  propagation_effect?: string;
  observable_effect?: string;
  recommended_test?: string;
}

export interface CoverageTestScenario {
  version?: string;
  scenario_id: string;
  priority: "high" | "medium" | "low" | string;
  case_type: "black_box_ready" | "black_box_hypothesis" | "gray_box_required" | string;
  flow_purpose: string;
  external_trigger: string;
  input_construction: string;
  normal_path: string;
  error_path: string;
  key_call_chain: string[];
  expected_result: string;
  observable_signals: string[];
  gray_box_aid: string;
  sfmea: CoverageSfmea;
  evidence_refs: string[];
  related_gaps: string[];
  confidence: "high" | "medium" | "low" | string;
  verification_gaps: string[];
}

export interface CoverageModuleResult {
  module_path: string;
  line_rate: number;
  branch_rate: number;
  function_rate: number;
  kind?: "function" | "branch";
  function_name?: string;
  file_path?: string;
  line_start?: number | null;
  line_end?: number | null;
  hit_count?: number;
  risk_level?: "high" | "medium" | "low";
  category?: string;
  scenario?: string;
  input_conditions?: string;
  expected_behavior?: string;
  observable_signals?: string[];
  confidence?: "high" | "medium" | "low";
  evidence?: Record<string, unknown>;
  analysis?: string;
  error?: string;
  uncovered_function_count?: number;
  uncovered_branch_count?: number;
  // coverage-test-design-v1 fields
  source_window?: CoverageSourceWindow | null;
  trigger_branches?: CoverageTriggerBranch[];
  entry_paths?: CoverageEntryPath[];
  black_box_cases?: CoverageBlackBoxCase[];
  gray_box?: CoverageGrayBox | null;
  gray_box_required?: boolean;
  entry_trace_status?: string;
  entry_discovery?: CoverageEntryDiscoveryCard;
  test_scenarios?: CoverageTestScenario[];
  ai_generation_status?: string;
  ai_recommendation_status?: string;
  ai_scenario_count?: number;
  deterministic_case_role?: string;
  evidence_gaps?: string[];
  tool_status?: CoverageToolStatus;
  // branch-gap fields
  branch?: string;
  condition?: string;
}

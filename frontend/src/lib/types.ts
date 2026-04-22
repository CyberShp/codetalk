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
  repository_name?: string | null;
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

export type ProxyMode = "system" | "direct";

export interface LLMConfig {
  id: string;
  provider: string;
  model_name: string;
  has_api_key: boolean;
  base_url: string | null;
  proxy_mode: ProxyMode;
  is_default: boolean;
  created_at: string;
}

export interface LLMConfigCreate {
  provider: string;
  model_name: string;
  api_key?: string;
  base_url?: string;
  proxy_mode?: ProxyMode;
  is_default?: boolean;
}

export interface LLMConfigUpdate {
  provider?: string;
  model_name?: string;
  api_key?: string;
  base_url?: string;
  proxy_mode?: ProxyMode;
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
  container_status?: string;
  message?: string;
}

export interface LogEntry {
  timestamp: string;
  level: "info" | "warn" | "error" | "debug";
  message: string;
  tool?: string;
}

/* ── GitNexus Knowledge Graph types ── */

export interface ProcessStep {
  symbolId: string;
  step: number;
}

export interface GraphNode {
  id: string;
  label: string; // File, Folder, Function, Method, Class, Module, etc.
  properties: {
    name: string;
    filePath?: string;
    startLine?: number;
    endLine?: number;
    content?: string;
    description?: string;
    heuristicLabel?: string;
    processType?: string;
    stepCount?: number;
    memberCount?: number;
    cohesion?: number;
    [key: string]: unknown;
  };
  steps?: ProcessStep[]; // enriched on Process nodes by backend
}

export interface GraphEdge {
  id: string;
  type: string; // CALLS, IMPORTS, CONTAINS, MEMBER_OF, etc.
  sourceId: string;
  targetId: string;
  confidence?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  processes?: GraphNode[];
  communities?: GraphNode[];
  intelligence?: Record<string, unknown>;
}

export interface SyncResult {
  status: "synced";
  local_path: string;
  last_indexed_at: string;
}

export interface FileSlice {
  content: string;
  startLine: number;
  endLine: number;
  totalLines: number;
  actualPath?: string;
}

/* ── Component Config types ── */

export interface ConfigField {
  name: string;
  label: string;
  field_type: "url" | "secret" | "text" | "select";
  options?: string[];
  placeholder?: string;
}

export interface ConfigDomain {
  domain: string;
  label: string;
  fields: ConfigField[];
  env_map: Record<string, string>;
}

export interface ComponentContract {
  component: string;
  label: string;
  domains: ConfigDomain[];
}

export interface ComponentConfigResponse {
  component: string;
  domain: string;
  config: Record<string, string>;
  applied_at: string | null;
  updated_at: string;
}

export interface ComponentHealth {
  component: string;
  healthy: boolean;
  container_status: string | null;
  version?: string | null;
}

export interface ComponentStatus {
  component: string;
  label: string;
  health: ComponentHealth;
  domains: ComponentConfigResponse[];
}

export interface ApplyResult {
  success: boolean;
  message: string;
  override_preview: Record<string, string> | null;
}

export interface RestartResult {
  success: boolean;
  message: string;
}

/* ── Wiki types ── */

export interface WikiPage {
  id: string;
  title: string;
  content: string;
  filePaths: string[];
  importance: "high" | "medium" | "low";
  relatedPages: string[];
}

export interface WikiSection {
  id: string;
  title: string;
  pages: string[];
  subsections?: string[];
}

export interface WikiStructure {
  id: string;
  title: string;
  description: string;
  pages: WikiPage[];
  sections: WikiSection[];
  rootSections: string[];
}

export interface WikiData {
  wiki_structure: WikiStructure;
  generated_pages: Record<string, WikiPage>;
}

export interface WikiResponse {
  status: "ready" | "not_generated";
  wiki: WikiData | null;
  stale: boolean;
}

export interface WikiGenerateResponse {
  status: string;
  message: string;
}

export interface WikiStatus {
  running: boolean;
  current: number;
  total: number;
  page_title: string;
  error: string | null;
}

/* ── Ask / Evidence types ── */

export interface EvidenceItem {
  id: string;
  type: "code" | "wiki";
  title: string;
  content: string;
  file?: string;
  line_range?: string;
}

export interface AskContextResponse {
  evidence: EvidenceItem[];
  sources_found: number;
  query: string;
}

/* ── Repo-centric types (仓库中心架构) ── */

export interface RepoDetail {
  repo: {
    id: string;
    name: string;
    source_type: SourceType;
    source_uri: string;
    local_path: string | null;
    branch: string;
    last_indexed_at: string | null;
  };
  wiki: {
    status: "ready" | "not_generated";
    generated_at: string | null;
    stale: boolean;
  };
  graph: {
    status: "ready" | "not_analyzed";
    analyzed_at: string | null;
    stats: {
      node_count: number;
      edge_count: number;
      process_count: number;
      community_count: number;
    } | null;
  };
}

export interface RepoAnalysisItem {
  id: string;
  task_type: TaskType;
  status: TaskStatus;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface PaginatedAnalyses {
  items: RepoAnalysisItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface RepoGraphResponse {
  status: "ready" | "not_analyzed";
  graph: GraphData | null;
  metadata: Record<string, unknown> | null;
  analyzed_at: string | null;
}

/* ── Analysis (Joern + Semgrep) types ── */

export type SeverityLevel = "ERROR" | "WARNING" | "INFO";

export interface SemgrepFinding {
  check_id: string;
  path: string;
  start: { line: number; col: number };
  end: { line: number; col: number };
  extra: {
    message: string;
    severity: SeverityLevel;
    metadata: {
      category?: string;
      cwe?: string[];
      owasp?: string[];
      [key: string]: unknown;
    };
    dataflow_trace?: {
      taint_source?: [string, { line: number }[]];
      intermediate_vars?: Array<[string, { line: number }]>;
      taint_sink?: [string, { line: number }[]];
    };
    fix?: string;
    lines?: string;
  };
}

/** Returned by GET /api/repos/{id}/analysis/summary */
export interface AnalysisSummary {
  repo_id: string;
  repo_name: string;
  tools: {
    joern: {
      healthy: boolean;
      status: string | null;
      capabilities: string[];
    };
  };
}

/** Enriched summary built from a full semgrep scan result */
export interface SemgrepScanSummary {
  total: number;
  by_severity: Record<SeverityLevel, number>;
  by_category: Record<string, number>;
}

export interface JoernMethodBranch {
  control_structure_type: string;
  condition: string | null;
  line_number: number | null;
  filename?: string;
  children: Array<{ code: string; label: string }>;
}

export interface JoernErrorPath {
  kind: "throw" | "try-catch" | "error-return";
  code: string;
  line_number: number | null;
  filename?: string;
}

export interface JoernBoundaryValue {
  code: string;
  line_number: number | null;
  filename?: string;
  operands: Array<{ code: string; type: string }>;
}

export interface JoernCallContext {
  caller: string;
  callerFile: string;
  callerLine: number;
  callSites: Array<{ line: number; args: string[] }>;
  callerBranches: Array<{ type: string; condition: string; line: number }>;
}

export interface JoernCalleeImpact {
  callee: string;
  calleeFile: string;
  calleeLine: number;
  errorReturns: Array<{ code: string; line: number }>;
  callSitesInTarget: Array<{ line: number; code: string }>;
}

export interface TaintPath {
  elements: Array<{ code: string; filename: string; line_number: number | null; is_source?: boolean }>;
  method?: string;
  file?: string;
}

/** Matches _normalize_test_point() output in test_point_generator.py */
export interface TestPoint {
  id?: string;
  scenario: string;
  input_conditions: string;
  expected_behavior: string;
  risk_scenario: string;
  boundary_values?: string | null;
  risk_level: "high" | "medium" | "low";
  source_location?: string | null;
  category?: string;
}

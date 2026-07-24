export type WorkflowNodeKind =
  | "input"
  | "output"
  | "agent"
  | "semantic_retrieve"
  | "memory_retrieve"
  | "local_scope_discover"
  | "evidence_validate"
  | "report_render"
  | "artifact_export";

export type WorkflowEdgeKind = "data" | "dependency";

export interface WorkflowPosition {
  x: number;
  y: number;
}

export interface WorkflowPortDefinition {
  id: string;
  type: string;
  required?: boolean;
  label?: string;
  collection?: boolean;
}

export interface WorkflowExecutionProfile {
  id: "rapid" | "deep";
  label: string;
  delivery_class: "bounded_analysis" | "full_test_delivery";
  expected_duration_minutes: [number, number];
  max_subagents: number;
  stage_overrides?: Record<string, Record<string, unknown>>;
}

export interface WorkflowNodeConfig {
  contract_id?: string;
  output_id?: string;
  step_id?: string;
  label?: string;
  type?: string;
  required?: boolean;
  resolver?: "manual" | "workspace" | "local" | "agent_mcp";
  role?: string;
  default_value?: unknown;
  schema?: Record<string, unknown>;
  goal?: string;
  provider?: string;
  mcp_profiles?: string[];
  skill_ids?: string[];
  skill_instructions?: Array<Record<string, unknown>>;
  timeout_sec?: number;
  idle_timeout_sec?: number;
  retry_policy?: { max_attempts: number; backoff_seconds: number };
  failure_policy?: "stop" | "continue_independent";
  required_artifacts?: string[];
  input_ports?: WorkflowPortDefinition[];
  output_ports?: WorkflowPortDefinition[];
  artifact?: string;
  companion_artifacts?: string[];
  default_enabled?: boolean;
  source_node_id?: string;
  source_port_id?: string;
  evidence_memory?: boolean | Record<string, unknown>;
  semantic_import?: boolean | Record<string, unknown>;
  quality_rules?: Array<Record<string, unknown>>;
  global_input?: boolean;
  [key: string]: unknown;
}

export interface WorkflowGraphNode {
  id: string;
  kind: WorkflowNodeKind;
  label: string;
  position: WorkflowPosition;
  config: WorkflowNodeConfig;
}

export interface WorkflowGraphEndpoint {
  node_id: string;
  port_id: string;
}

export interface WorkflowGraphEdge {
  id: string;
  kind: WorkflowEdgeKind;
  source: WorkflowGraphEndpoint;
  target: WorkflowGraphEndpoint;
}

export interface AuthoringGraphV2 {
  schema_version: 2;
  workflow_id: string;
  name: string;
  description: string;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
  settings: {
    stop_on_error: boolean;
    max_parallelism: 1;
    execution_profiles?: WorkflowExecutionProfile[];
    default_execution_profile?: WorkflowExecutionProfile["id"];
  };
}

export interface WorkflowValidationIssue {
  code: string;
  message: string;
  node_id?: string;
  field?: string;
}

export interface WorkflowValidationResult {
  valid: boolean;
  errors: WorkflowValidationIssue[];
  warnings: WorkflowValidationIssue[];
}

export interface WorkflowPlanNode {
  node_id: string;
  graph_node_id: string;
  type: string;
  depends_on: string[];
  resolved_input_bindings: Record<
    string,
    | { source_node_id: string; source_port_id: string }
    | Array<{ source_node_id: string; source_port_id: string }>
  >;
  provider: string;
  mcp_profiles: string[];
  skill_ids: string[];
  output_contracts: Array<Record<string, unknown>>;
  timeout_sec: number;
  idle_timeout_sec: number;
  failure_policy: "stop" | "continue_independent";
}

export interface CompiledWorkflowPlan {
  plan_version: 1;
  workflow_version_id: string;
  topological_order: string[];
  nodes: WorkflowPlanNode[];
  max_parallelism: 1;
  stop_on_error: boolean;
  execution_profiles?: WorkflowExecutionProfile[];
  default_execution_profile?: WorkflowExecutionProfile["id"];
}

export interface WorkflowCompileResult {
  compiled_definition: Record<string, unknown>;
  compiled_plan: CompiledWorkflowPlan;
  validation_result: WorkflowValidationResult;
}

export interface WorkflowHeader {
  workflow_id: string;
  name: string;
  description: string;
  status: "active" | "archived";
  published_version_id: string | null;
  current_draft_version_id: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface WorkflowVersion {
  version_id: string;
  workflow_id: string;
  version_number: number;
  state: "draft" | "published" | "archived";
  authoring_graph: AuthoringGraphV2 | Record<string, unknown>;
  compiled_definition: Record<string, unknown> | null;
  compiled_plan: CompiledWorkflowPlan | null;
  validation: WorkflowValidationResult | null;
  based_on_version_id: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface WorkflowListItem {
  id: string;
  name: string;
  description?: string;
  version: number;
  authoring_graph?: AuthoringGraphV2;
  v2?: WorkflowHeader;
  inputs?: unknown[];
  steps?: unknown[];
  outputs?: unknown[];
}

export interface WorkflowDetail extends WorkflowListItem {
  authoring_graph: AuthoringGraphV2;
}

export interface WorkflowSkillCapability {
  id: string;
  label: string;
  description?: string;
  default_enabled?: boolean;
}

export interface WorkflowCapabilities {
  input_types: string[];
  input_resolvers: string[];
  step_types: string[];
  output_types: string[];
  skill_catalog?: WorkflowSkillCapability[];
}

export interface WorkflowNodeRegistryUi {
  label: string;
  palette_label: string;
  palette_group: string;
  description: string;
}

export interface WorkflowNodeRegistryEntry {
  kind: WorkflowNodeKind;
  version: number;
  ui: WorkflowNodeRegistryUi;
  default_ports: {
    input_ports: WorkflowPortDefinition[];
    output_ports: WorkflowPortDefinition[];
  };
  default_config: WorkflowNodeConfig;
  config_schema: Record<string, unknown>;
}

export interface WorkflowNodeRegistry {
  schema_version: number;
  nodes: WorkflowNodeRegistryEntry[];
}

export interface WorkflowProviderCapability {
  provider: string;
  display_name: string;
  status: string;
  capabilities?: {
    mcp_profiles?: string[];
    supports_mcp?: boolean;
    supports_artifact_export?: boolean;
  };
}

export interface WorkflowTrialRunResult {
  status: "prepared";
  task_run_id: string;
  workflow_id: string;
  workflow_version_id: string;
  workspace_id: string;
  compiled_plan: CompiledWorkflowPlan;
  diagnostic: {
    kind: "node_trial" | "workflow_trial";
    node_id: string | null;
    not_a_formal_delivery: true;
  };
}

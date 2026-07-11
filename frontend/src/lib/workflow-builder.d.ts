export interface WorkflowDesignerNode {
  id: string;
  kind: string;
  title?: string;
  subtitle?: string;
  x?: number;
  y?: number;
  source?: string;
  config?: Record<string, unknown>;
}

export interface WorkflowDesignerEdge {
  id?: string;
  source: string;
  target: string;
  label?: string;
}

export interface WorkflowDesignerLayout {
  nodes?: WorkflowDesignerNode[];
  edges?: WorkflowDesignerEdge[];
  hidden_node_ids?: string[];
  hidden_edge_ids?: string[];
}

export interface WorkflowDesignerSkill {
  id: string;
  label?: string;
  source?: string;
  prompt_hint?: string;
  description?: string;
}

export interface BuildWorkflowFromDesignerOptions {
  workflowId: string;
  workflowName: string;
  provider?: string;
  mcpProfile?: string;
  goal?: string;
  skillIds?: string[];
  selectedSkills?: WorkflowDesignerSkill[];
  inputSpec?: string;
  outputSpec?: string;
  artifacts?: string;
  inputLabels?: Record<string, string>;
  outputLabels?: Record<string, string>;
  inputSchemas?: Record<string, unknown>;
  outputSchemas?: Record<string, unknown>;
  evidenceMappings?: Record<string, unknown>;
  semanticImports?: Record<string, unknown>;
  layout?: WorkflowDesignerLayout;
}

export function buildWorkflowFromDesigner(
  options: BuildWorkflowFromDesignerOptions,
): Record<string, unknown>;

export function mergeDesignerWorkflowWithDraft(
  generatedWorkflow: Record<string, unknown>,
  draftWorkflow: Record<string, unknown>,
): Record<string, unknown>;

export function mergeDesignerWorkflowWithSpecializedDraft(
  generatedWorkflow: Record<string, unknown>,
  draftWorkflow: Record<string, unknown>,
): Record<string, unknown>;

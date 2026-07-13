import type {
  AuthoringGraphV2,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowNodeKind,
} from "@/lib/types/workflow";

export function safeWorkflowId(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 96);
  return normalized || `workflow-${Date.now().toString(36)}`;
}

export function createStarterGraph(
  workflowId: string,
  name: string,
  description = "",
): AuthoringGraphV2 {
  const repo = createNode("input", 80, 180, "repo");
  repo.label = "源码工作区";
  repo.config = {
    contract_id: "repo_path",
    label: "源码工作区",
    type: "directory",
    required: true,
    resolver: "workspace",
    role: "选择已创建的 CodeTalk 工作空间",
  };
  const agent = createNode("agent", 390, 180, "analyze");
  agent.label = "源码分析";
  agent.config = {
    step_id: "analyze",
    goal: "基于工作区源码和输入材料完成分析，并写入指定交付文件。",
    provider: "builtin-llm",
    mcp_profiles: [],
    skill_ids: ["source-evidence-first", "artifact-contract"],
    required_artifacts: ["report.md"],
    input_ports: [{ id: "repo_path", type: "directory", required: true }],
    output_ports: [{ id: "report", type: "markdown" }],
    timeout_sec: 900,
    idle_timeout_sec: 120,
    retry_policy: { max_attempts: 1, backoff_seconds: 0 },
    failure_policy: "stop",
  };
  const output = createNode("output", 720, 180, "report");
  output.label = "分析报告";
  output.config = {
    output_id: "report",
    label: "分析报告",
    type: "markdown",
    artifact: "report.md",
    required: true,
    source_node_id: "analyze",
    source_port_id: "report",
  };
  return {
    schema_version: 2,
    workflow_id: workflowId,
    name,
    description,
    nodes: [repo, agent, output],
    edges: [
      edge("edge-repo-analyze", "data", "repo", "value", "analyze", "repo_path"),
      edge("edge-analyze-report", "data", "analyze", "report", "report", "value"),
    ],
    settings: { stop_on_error: true, max_parallelism: 1 },
  };
}

export function createNode(
  kind: WorkflowNodeKind,
  x: number,
  y: number,
  preferredId?: string,
): WorkflowGraphNode {
  const suffix = Date.now().toString(36).slice(-5);
  const id = safeWorkflowId(preferredId || `${kind}-${suffix}`);
  const common = { id, kind, label: nodeKindLabel(kind), position: { x, y } };
  if (kind === "input") {
    return {
      ...common,
      config: {
        contract_id: id,
        label: "新输入",
        type: "text",
        required: false,
        resolver: "manual",
        role: "",
      },
    };
  }
  if (kind === "output") {
    return {
      ...common,
      config: {
        output_id: id,
        label: "新输出",
        type: "markdown",
        artifact: `${id}.md`,
        required: true,
      },
    };
  }
  return {
    ...common,
    config: {
      step_id: id,
      goal: kind === "agent" ? "说明该节点要完成的分析目标。" : undefined,
      provider: kind === "agent" ? "builtin-llm" : undefined,
      mcp_profiles: [],
      skill_ids: [],
      required_artifacts: [],
      input_ports: [{ id: "input", type: "any" }],
      output_ports: [{ id: "output", type: "any" }],
      timeout_sec: 900,
      idle_timeout_sec: 120,
      retry_policy: { max_attempts: 1, backoff_seconds: 0 },
      failure_policy: "stop",
    },
  };
}

export function edge(
  id: string,
  kind: "data" | "dependency",
  sourceNode: string,
  sourcePort: string,
  targetNode: string,
  targetPort: string,
): WorkflowGraphEdge {
  return {
    id,
    kind,
    source: { node_id: sourceNode, port_id: sourcePort },
    target: { node_id: targetNode, port_id: targetPort },
  };
}

export function nodeKindLabel(kind: WorkflowNodeKind): string {
  return {
    input: "输入",
    output: "输出",
    agent: "Agent",
    semantic_retrieve: "语义检索",
    memory_retrieve: "证据检索",
    local_scope_discover: "源码范围发现",
    evidence_validate: "证据校验",
    report_render: "报告渲染",
    artifact_export: "产物导出",
  }[kind];
}

export function inputPortIds(node: WorkflowGraphNode): string[] {
  if (node.kind === "output") return ["value"];
  if (node.kind === "input") return [];
  const ports = node.config.input_ports ?? [];
  return ports.length ? ports.map((port) => port.id) : ["start"];
}

export function outputPortIds(node: WorkflowGraphNode): string[] {
  if (node.kind === "input") return ["value"];
  if (node.kind === "output") return [];
  const ports = node.config.output_ports ?? [];
  return ports.length ? ports.map((port) => port.id) : ["done"];
}

export function graphWithHeader(
  graph: AuthoringGraphV2,
  name: string,
  description: string,
): AuthoringGraphV2 {
  return { ...graph, name, description };
}

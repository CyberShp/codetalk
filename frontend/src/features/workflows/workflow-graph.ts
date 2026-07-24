import type {
  AuthoringGraphV2,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowNodeKind,
  WorkflowNodeConfig,
  WorkflowNodeRegistryEntry,
  WorkflowPortDefinition,
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

export function sanitizeWorkflowIdDraft(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^[._-]+/g, "")
    .slice(0, 96);
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
    input_ports: [
      { id: "repo_path", type: "directory", required: true },
      { id: "analysis_target", type: "text", required: true },
    ],
    output_ports: [{ id: "report", type: "markdown" }],
    timeout_sec: 900,
    idle_timeout_sec: 120,
    retry_policy: { max_attempts: 1, backoff_seconds: 0 },
    failure_policy: "stop",
  };
  const target = createNode("input", 80, 330, "analysis_target");
  target.label = "分析对象";
  target.config = {
    contract_id: "analysis_target",
    label: "分析对象",
    type: "text",
    required: true,
    resolver: "manual",
    role: "填写要分析的模块、协议、业务流程或变更范围，例如 NVMe/TCP TLS 握手。",
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
    nodes: [repo, target, agent, output],
    edges: [
      edge("edge-repo-analyze", "data", "repo", "value", "analyze", "repo_path"),
      edge("edge-target-analyze", "data", "analysis_target", "value", "analyze", "analysis_target"),
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

/**
 * Materialize a new persisted graph node from the backend-owned registry.
 * The registry owns defaults and port contracts; this function only assigns a
 * graph-local identity and position.
 */
export function createNodeFromRegistry(
  definition: WorkflowNodeRegistryEntry,
  x: number,
  y: number,
): WorkflowGraphNode {
  const suffix = Date.now().toString(36).slice(-5);
  const id = safeWorkflowId(`${definition.kind}-${suffix}`);
  const config = cloneWorkflowConfig(definition.default_config);

  if (definition.kind === "input") {
    config.contract_id = id;
    config.label = String(config.label || definition.ui.label);
  } else if (definition.kind === "output") {
    config.output_id = id;
    if (!String(config.artifact || "").trim() || config.artifact === "output.md") {
      config.artifact = `${id}.md`;
    }
  } else {
    config.step_id = id;
    config.input_ports = clonePorts(definition.default_ports.input_ports);
    config.output_ports = clonePorts(definition.default_ports.output_ports);
  }

  return {
    id,
    kind: definition.kind,
    label: definition.ui.label,
    position: { x: Math.max(20, x), y: Math.max(20, y) },
    config,
  };
}

function cloneWorkflowConfig(config: WorkflowNodeConfig): WorkflowNodeConfig {
  return JSON.parse(JSON.stringify(config)) as WorkflowNodeConfig;
}

function clonePorts(ports: WorkflowPortDefinition[]): WorkflowPortDefinition[] {
  return ports.map((port) => ({ ...port }));
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
  return inputPortDefinitions(node).map((port) => port.id);
}

export function inputPortDefinitions(node: WorkflowGraphNode): Array<{ id: string; type: string; required?: boolean; collection?: boolean }> {
  if (node.kind === "output") {
    return [{ id: "value", type: String(node.config.type ?? "any"), required: Boolean(node.config.required) }];
  }
  if (node.kind === "input") return [];
  const dataPorts = node.config.input_ports?.length
    ? node.config.input_ports.map((port) => ({ ...port, type: port.type || "any" }))
    : [];
  return dataPorts.some((port) => port.id === "start")
    ? dataPorts
    : [...dataPorts, { id: "start", type: "control" }];
}

export function outputPortIds(node: WorkflowGraphNode): string[] {
  return outputPortDefinitions(node).map((port) => port.id);
}

export function outputPortDefinitions(node: WorkflowGraphNode): Array<{ id: string; type: string }> {
  if (node.kind === "input") {
    return [{ id: "value", type: String(node.config.type ?? "any") }];
  }
  if (node.kind === "output") return [];
  const dataPorts = node.config.output_ports?.length
    ? node.config.output_ports.map((port) => ({ id: port.id, type: port.type || "any" }))
    : [];
  return dataPorts.some((port) => port.id === "done")
    ? dataPorts
    : [...dataPorts, { id: "done", type: "control" }];
}

export type ConnectionValidation =
  | { ok: true }
  | { ok: false; code: string; message: string };

export function connectionEdgeKind(
  sourceNode: WorkflowGraphNode,
  sourcePortId: string,
  targetNode: WorkflowGraphNode,
  targetPortId: string,
): WorkflowGraphEdge["kind"] {
  const syntheticDone = sourcePortId === "done" && !sourceNode.config.output_ports?.some((port) => port.id === "done");
  const syntheticStart = targetPortId === "start" && !targetNode.config.input_ports?.some((port) => port.id === "start");
  return syntheticDone && syntheticStart ? "dependency" : "data";
}

export function validateInputPortId(
  value: string,
  ports: WorkflowPortDefinition[],
  currentIndex: number,
  portLabel = "端口",
): string {
  const portId = value.trim();
  if (!portId) return `${portLabel}名称不能为空`;
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(portId)) {
    return `${portLabel}名称只能包含字母、数字、点、横线和下划线`;
  }
  if (ports.some((port, index) => index !== currentIndex && port.id === portId)) {
    return `${portLabel}名称已存在`;
  }
  return "";
}

export function validateConnection(
  graph: AuthoringGraphV2,
  sourceNodeId: string,
  sourcePortId: string,
  targetNodeId: string,
  targetPortId: string,
): ConnectionValidation {
  if (sourceNodeId === targetNodeId) {
    return { ok: false, code: "self_connection", message: "节点不能连接到自身" };
  }
  const sourceNode = graph.nodes.find((node) => node.id === sourceNodeId);
  const targetNode = graph.nodes.find((node) => node.id === targetNodeId);
  if (!sourceNode || !targetNode) {
    return { ok: false, code: "node_missing", message: "连接的节点不存在" };
  }
  const source = outputPortDefinitions(sourceNode).find((port) => port.id === sourcePortId);
  const target = inputPortDefinitions(targetNode).find((port) => port.id === targetPortId);
  if (!source || !target) {
    return { ok: false, code: "port_missing", message: "连接的端口不存在" };
  }
  const occupied = graph.edges.some(
    (item) =>
      item.kind === "data" &&
      item.target.node_id === targetNodeId &&
      item.target.port_id === targetPortId,
  );
  if (occupied && !target.collection) {
    return { ok: false, code: "target_input_occupied", message: "该输入已绑定" };
  }
  if (source.type !== target.type && source.type !== "any" && target.type !== "any") {
    return {
      ok: false,
      code: "port_type_mismatch",
      message: `不能连接：${source.type} 类型不能连接到 ${target.type} 输入`,
    };
  }
  return { ok: true };
}

export function connectionLabel(
  workflowEdge: WorkflowGraphEdge,
  nodesById: Map<string, WorkflowGraphNode>,
): string {
  const sourceNode = nodesById.get(workflowEdge.source.node_id);
  const targetNode = nodesById.get(workflowEdge.target.node_id);
  if (!sourceNode || !targetNode) return "";
  const sourceType = outputPortDefinitions(sourceNode).find(
    (port) => port.id === workflowEdge.source.port_id,
  )?.type ?? "any";
  const targetType = inputPortDefinitions(targetNode).find(
    (port) => port.id === workflowEdge.target.port_id,
  )?.type ?? "any";
  const sourceLabel = sourceNode.kind === "input"
    ? sourceNode.label
    : workflowEdge.source.port_id;
  return `${sourceLabel} · ${sourceType} → ${workflowEdge.target.port_id} · ${targetType}`;
}

export function graphWithHeader(
  graph: AuthoringGraphV2,
  name: string,
  description: string,
): AuthoringGraphV2 {
  return { ...graph, name, description };
}

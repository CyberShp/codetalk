function parseCommaSeparated(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  for (const value of values) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

function uniqueSkillInstructions(values) {
  const seen = new Set();
  const result = [];
  for (const value of values) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const id = String(value.id || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    result.push(value);
  }
  return result;
}

function parseWorkflowSpecList(value, defaultType) {
  return parseCommaSeparated(value).map((item) => {
    const [specPart, artifactPart] = item.split("=").map((part) => part.trim());
    const [typedPart, resolverPart] = specPart.split("@").map((part) => part.trim());
    const [id, type] = typedPart.split(":").map((part) => part.trim());
    if (!id) {
      throw new Error("Workflow builder entries must use id:type");
    }
    return {
      id,
      type: type || defaultType,
      ...(resolverPart ? { resolver: resolverPart } : {}),
      ...(artifactPart ? { artifact: artifactPart } : {}),
    };
  });
}

function workflowItemLabel(labels, id) {
  return String((labels || {})[id] || id).trim();
}

function safeStepId(value, fallback) {
  const text = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return text || fallback;
}

function asRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function nodeConfig(node) {
  return asRecord(node?.config);
}

function nodeContractId(node, fallback) {
  return safeStepId(nodeConfig(node).id || node.id, fallback || String(node?.id || "node"));
}

function nodeLabel(node, fallback) {
  return String(nodeConfig(node).label || node.title || fallback || node.id || "").trim();
}

function stringsFromNodeConfig(node, key) {
  const value = nodeConfig(node)[key];
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean);
  if (typeof value === "string") return parseCommaSeparated(value);
  return [];
}

function connectedContextNodesForAgent(agentNode, nodesById, edges) {
  if (!agentNode) return [];
  return incomingSources(edges, agentNode.id)
    .map((source) => nodesById.get(source))
    .filter((node) => node && node.kind === "context");
}

function outputArtifactForSpec(outputId, outputType, artifacts) {
  const normalizedOutput = outputId.replace(/[-_\s]/g, "").toLowerCase();
  const matchingArtifact = artifacts.find((artifact) => {
    const stem = artifact.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
    const normalizedStem = stem.replace(/[-_\s]/g, "").toLowerCase();
    return normalizedStem === normalizedOutput || normalizedStem.includes(normalizedOutput);
  });
  if (matchingArtifact) return matchingArtifact;
  if (["json", "scope_report", "test_cases"].includes(outputType)) {
    return `${outputId}.json`;
  }
  return "";
}

function schemaForSpec(id, type, allSchemas) {
  const schemas = asRecord(allSchemas);
  const direct = schemas[id];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) return direct;
  const normalized = String(id || "").replace(/[-_\s]/g, "").toLowerCase();
  const alias =
    normalized.includes("sfmea")
      ? "sfmea"
      : normalized.includes("blackbox") ||
          normalized.includes("blackcase") ||
          normalized.includes("testcase") ||
          normalized.includes("cases")
        ? "black_box_cases"
        : normalized.includes("evidence")
          ? "code_evidence"
          : normalized.includes("scope")
            ? "source_scope"
            : "";
  const aliased = alias ? schemas[alias] : null;
  if (aliased && typeof aliased === "object" && !Array.isArray(aliased)) {
    return aliased;
  }
  const byType = schemas[`type:${type}`];
  if (byType && typeof byType === "object" && !Array.isArray(byType)) return byType;
  const wildcard = schemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) return wildcard;
  return null;
}

function mappingForSpec(id, type, mappings, includeType = false) {
  const record = asRecord(mappings);
  const direct = record[id];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) return direct;
  if (includeType) {
    const byType = record[`type:${type}`];
    if (byType && typeof byType === "object" && !Array.isArray(byType)) return byType;
  }
  const wildcard = record["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) return wildcard;
  return null;
}

function visibleLayout(layout) {
  const source = asRecord(layout);
  const hiddenNodes = new Set(Array.isArray(source.hidden_node_ids) ? source.hidden_node_ids.map(String) : []);
  const hiddenEdges = new Set(Array.isArray(source.hidden_edge_ids) ? source.hidden_edge_ids.map(String) : []);
  const nodes = (Array.isArray(source.nodes) ? source.nodes : [])
    .filter((node) => node && typeof node === "object" && !hiddenNodes.has(String(node.id || "")))
    .map((node) => ({
      ...node,
      id: String(node.id || ""),
      kind: String(node.kind || "context"),
      title: String(node.title || node.id || ""),
    }))
    .filter((node) => node.id);
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (Array.isArray(source.edges) ? source.edges : [])
    .filter((edge) => edge && typeof edge === "object" && !hiddenEdges.has(String(edge.id || "")))
    .map((edge) => ({
      ...edge,
      id: String(edge.id || `${edge.source || ""}->${edge.target || ""}`),
      source: String(edge.source || ""),
      target: String(edge.target || ""),
    }))
    .filter((edge) => edge.source && edge.target && nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return { nodes, edges };
}

function validateExecutableCanvas(nodes, edges) {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const customNodes = nodes.filter((node) => node.source === "canvas");
  if (!customNodes.length) return;

  const allowedTargets = {
    input: new Set(["context", "agent"]),
    context: new Set(["context", "agent"]),
    agent: new Set(["agent", "output", "verify"]),
    output: new Set(["verify"]),
    verify: new Set(),
  };
  for (const edge of edges) {
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    if (!source || !target) {
      throw new Error(`连线“${edge.id}”引用了不存在的节点，请删除后重新连接。`);
    }
    const targets = allowedTargets[source.kind];
    if (!targets || !targets.has(target.kind)) {
      throw new Error(`不支持从“${source.title}”连接到“${target.title}”，请按输入/能力 -> Agent -> 输出 -> 校验的顺序连接。`);
    }
  }

  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) {
    outgoing.get(edge.source)?.push(edge.target);
    incoming.get(edge.target)?.push(edge.source);
  }
  function reachesAgent(nodeId, seen = new Set()) {
    if (seen.has(nodeId)) return false;
    seen.add(nodeId);
    return (outgoing.get(nodeId) || []).some((targetId) => {
      const target = nodesById.get(targetId);
      return target?.kind === "agent" || (target?.kind === "context" && reachesAgent(targetId, seen));
    });
  }
  for (const node of customNodes) {
    if ((node.kind === "input" || node.kind === "context") && !reachesAgent(node.id)) {
      throw new Error(`${node.kind === "input" ? "输入" : "能力"}节点“${node.title}”必须连接到上下文或 Agent 节点。`);
    }
    if (node.kind === "output" && !incoming.get(node.id)?.some((id) => nodesById.get(id)?.kind === "agent")) {
      throw new Error(`输出节点“${node.title}”必须连接到 Agent 节点。`);
    }
    if (node.kind === "agent" && !(incoming.get(node.id)?.length || outgoing.get(node.id)?.length)) {
      throw new Error(`Agent 节点“${node.title}”尚未接入执行链。`);
    }
  }

  const visiting = new Set();
  const visited = new Set();
  function visit(nodeId) {
    if (visiting.has(nodeId)) throw new Error("工作流画布存在环路，请删除造成循环的连线。");
    if (visited.has(nodeId)) return;
    visiting.add(nodeId);
    for (const targetId of outgoing.get(nodeId) || []) visit(targetId);
    visiting.delete(nodeId);
    visited.add(nodeId);
  }
  for (const node of nodes) visit(node.id);
}

function incomingSources(edges, targetId) {
  return edges.filter((edge) => edge.target === targetId).map((edge) => edge.source);
}

function agentSourceForOutput(output, outputNodesById, agentIds, edges) {
  const outputNode = outputNodesById.get(output.id);
  if (outputNode) {
    const incomingAgent = incomingSources(edges, outputNode.id)
      .map((source) => safeStepId(source, source))
      .find((source) => agentIds.includes(source));
    if (incomingAgent) return incomingAgent;
  }
  return agentIds[agentIds.length - 1] || "render_report";
}

function mergeWorkflowItemsById(generatedItems, draftItems) {
  const draftById = new Map(
    asArray(draftItems)
      .filter((item) => item && typeof item === "object" && !Array.isArray(item))
      .map((item) => [String(item.id || ""), item])
      .filter(([id]) => id),
  );
  return asArray(generatedItems).map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    const draft = draftById.get(String(item.id || ""));
    return draft && typeof draft === "object" && !Array.isArray(draft)
      ? { ...draft, ...item }
      : item;
  });
}

export function mergeDesignerWorkflowWithDraft(generatedWorkflow, draftWorkflow) {
  const generated = asRecord(generatedWorkflow);
  const draft = asRecord(draftWorkflow);
  if (!Object.keys(draft).length) return generated;
  const generatedUi = asRecord(generated.ui);
  const draftUi = asRecord(draft.ui);
  return {
    ...draft,
    ...generated,
    inputs: mergeWorkflowItemsById(generated.inputs, draft.inputs),
    steps: mergeWorkflowItemsById(generated.steps, draft.steps),
    outputs: mergeWorkflowItemsById(generated.outputs, draft.outputs),
    ui: {
      ...draftUi,
      ...generatedUi,
      layout: generatedUi.layout ?? draftUi.layout,
    },
  };
}

function mergeSpecializedItems(
  generatedItems,
  draftItems,
  { preserveDependencies = false, includeGeneratedItem = () => true } = {},
) {
  const generatedById = new Map(
    asArray(generatedItems)
      .filter((item) => item && typeof item === "object" && !Array.isArray(item) && item.id)
      .map((item) => [String(item.id), item]),
  );
  const merged = asArray(draftItems).map((draftItem) => {
    if (!draftItem || typeof draftItem !== "object" || Array.isArray(draftItem)) return draftItem;
    const generatedItem = generatedById.get(String(draftItem.id || ""));
    if (!generatedItem) return draftItem;
    generatedById.delete(String(draftItem.id || ""));
    const item = { ...draftItem, ...generatedItem };
    if (preserveDependencies) {
      const dependencies = uniqueStrings([
        ...asArray(draftItem.depends_on),
        ...asArray(generatedItem.depends_on),
      ]);
      if (dependencies.length) item.depends_on = dependencies;
    }
    return item;
  });
  return [
    ...merged,
    ...[...generatedById.values()].filter((item) => includeGeneratedItem(item)),
  ];
}

export function mergeDesignerWorkflowWithSpecializedDraft(generatedWorkflow, draftWorkflow) {
  const generated = asRecord(generatedWorkflow);
  const draft = asRecord(draftWorkflow);
  const generatedUi = asRecord(generated.ui);
  const draftUi = asRecord(draft.ui);
  const layoutNodes = asArray(asRecord(generatedUi.layout).nodes);
  const canvasAgentIds = new Set(
    layoutNodes
      .filter((node) => node && node.kind === "agent" && node.source === "canvas")
      .map((node) => nodeContractId(node, String(node.id || "agent"))),
  );
  const contractAgentIds = new Set(
    layoutNodes
      .filter((node) => node && node.kind === "agent" && node.source === "contract")
      .map((node) => nodeContractId(node, String(node.id || "agent"))),
  );
  const rawDraftSteps = asArray(draft.steps);
  const draftAgentCount = rawDraftSteps.filter((step) => step?.type === "agent_task").length;
  const referencedDraftStepIds = new Set([
    ...asArray(draft.outputs).map((output) => String(output?.from || output?.source || "")),
    ...rawDraftSteps.flatMap((step) => asArray(step?.depends_on).map(String)),
  ]);
  const cleanedDraftSteps = rawDraftSteps.filter((step) => {
    if (!step || step.type !== "agent_task" || draftAgentCount < 2) return true;
    const stepId = String(step.id || "");
    return !contractAgentIds.has(stepId) || referencedDraftStepIds.has(stepId);
  });
  const draftStepTypes = new Set(cleanedDraftSteps.map((step) => String(step?.type || "")));
  const hasDraftAgent = draftStepTypes.has("agent_task");
  const generatedSupportTypes = new Set(["evidence_validate", "report_render", "artifact_export"]);
  return {
    ...draft,
    ...generated,
    inputs: mergeSpecializedItems(generated.inputs, draft.inputs),
    steps: mergeSpecializedItems(generated.steps, cleanedDraftSteps, {
      preserveDependencies: true,
      includeGeneratedItem: (item) => {
        if (item?.type === "agent_task") {
          return !hasDraftAgent || canvasAgentIds.has(String(item.id || ""));
        }
        if (generatedSupportTypes.has(String(item?.type || ""))) {
          return !draftStepTypes.has(String(item.type));
        }
        return true;
      },
    }),
    outputs: mergeSpecializedItems(generated.outputs, draft.outputs),
    ui: {
      ...draftUi,
      ...generatedUi,
      layout: generatedUi.layout ?? draftUi.layout,
    },
  };
}

export function buildWorkflowFromDesigner(options) {
  const workflowId = String(options.workflowId || "").trim();
  const workflowName = String(options.workflowName || "").trim();
  if (!workflowId || !workflowName) {
    throw new Error("Workflow builder requires workflow id and name");
  }

  const inputSchemas = asRecord(options.inputSchemas);
  const outputSchemas = asRecord(options.outputSchemas);
  const evidenceMappings = asRecord(options.evidenceMappings);
  const semanticImports = asRecord(options.semanticImports);
  const requiredArtifacts = uniqueStrings(parseCommaSeparated(options.artifacts));
  const labels = asRecord(options.inputLabels);
  const outputLabels = asRecord(options.outputLabels);
  const layout = asRecord(options.layout);
  const { nodes, edges } = visibleLayout(layout);
  validateExecutableCanvas(nodes, edges);
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const inputNodes = nodes.filter((node) => node.kind === "input");
  const canvasInputNodes = inputNodes.filter((node) => node.source === "canvas");
  const agentNodes = nodes.filter((node) => node.kind === "agent");
  const outputNodesById = new Map(
    nodes
      .filter((node) => node.kind === "output" && node.source === "canvas")
      .map((node) => [nodeContractId(node, node.id), node]),
  );
  const verifyNodes = nodes.filter((node) => node.kind === "verify");

  const specInputs = parseWorkflowSpecList(options.inputSpec || "", "free_text");
  for (const node of canvasInputNodes) {
    const config = nodeConfig(node);
    const nodeId = nodeContractId(node, node.id);
    if (!specInputs.some((input) => input.id === nodeId)) {
      specInputs.push({
        id: nodeId,
        type: String(config.type || "free_text").trim() || "free_text",
        ...(config.resolver ? { resolver: String(config.resolver) } : {}),
      });
    }
  }
  const inputs = specInputs.map((input) => {
    const inputNode = inputNodes.find((node) => nodeContractId(node, node.id) === input.id);
    const label = inputNode ? nodeLabel(inputNode, input.id) : workflowItemLabel(labels, input.id);
    const schema = schemaForSpec(input.id, input.type, inputSchemas);
    return {
      id: input.id,
      label,
      type: input.type,
      required: input.type !== "file" && input.type !== "file_set",
      resolver:
        input.resolver ||
        (input.type === "mr_link" || input.type === "external_link" ? "agent_mcp" : "manual"),
      role:
        input.resolver === "agent_mcp" || input.type === "mr_link"
          ? "由智能体 CLI 通过 MCP 凭证解析远端变更源"
          : `用户提供: ${label}`,
      ...(schema ? { schema } : {}),
    };
  });

  const agentIds = agentNodes.length > 0
    ? agentNodes.map((node, index) => nodeContractId(node, `agent_${index + 1}`))
    : ["agent_collect"];
  const outputSpecs = parseWorkflowSpecList(options.outputSpec || "", "json");
  for (const node of outputNodesById.values()) {
    const config = nodeConfig(node);
    const outputId = nodeContractId(node, node.id);
    if (!outputSpecs.some((output) => output.id === outputId)) {
      outputSpecs.push({
        id: outputId,
        type: String(config.type || "json").trim() || "json",
        ...(config.artifact ? { artifact: String(config.artifact) } : {}),
      });
    }
  }
  const outputs = outputSpecs.map((output) => {
    const outputNode = outputNodesById.get(output.id);
    const label = outputNode ? nodeLabel(outputNode, output.id) : workflowItemLabel(outputLabels, output.id);
    const artifact = output.artifact || outputArtifactForSpec(output.id, output.type, requiredArtifacts);
    const from = artifact ? agentSourceForOutput(output, outputNodesById, agentIds, edges) : "render_report";
    const schema = output.type === "json" ? schemaForSpec(output.id, output.type, outputSchemas) : null;
    const evidenceMemory =
      output.type === "json" || output.type === "scope_report"
        ? mappingForSpec(output.id, output.type, evidenceMappings)
        : null;
    const semanticImport =
      output.type === "test_cases"
        ? mappingForSpec(output.id, output.type, semanticImports, true)
        : null;
    return {
      id: output.id,
      label,
      type: output.type,
      from,
      ...(artifact ? { artifact } : {}),
      ...(schema ? { schema } : {}),
      ...(evidenceMemory ? { evidence_memory: evidenceMemory } : {}),
      ...(semanticImport ? { semantic_import: semanticImport } : {}),
    };
  });

  const selectedSkills = Array.isArray(options.selectedSkills) ? options.selectedSkills : [];
  const skillInstructions = selectedSkills.map((skill) => ({
    id: String(skill.id || ""),
    label: String(skill.label || skill.id || ""),
    source: String(skill.source || ""),
    prompt_hint: String(skill.prompt_hint || skill.description || skill.label || skill.id || ""),
  })).filter((skill) => skill.id);

  const outputsByAgent = new Map(agentIds.map((id) => [id, []]));
  for (const output of outputs) {
    if (outputsByAgent.has(output.from) && output.artifact) {
      outputsByAgent.get(output.from).push(output.artifact);
    }
  }
  const steps = agentIds.map((agentId) => {
    const sourceNode = agentNodes.find((node) => nodeContractId(node, node.id) === agentId);
    const config = nodeConfig(sourceNode);
    const contextNodes = connectedContextNodesForAgent(sourceNode, nodesById, edges);
    const contextMcpProfiles = contextNodes.flatMap((node) => [
      ...stringsFromNodeConfig(node, "mcp_profiles"),
      ...stringsFromNodeConfig(node, "mcp_profile"),
    ]);
    const explicitMcpProfiles = uniqueStrings([
      String(config.mcp_profile || "").trim(),
      ...contextMcpProfiles,
    ]);
    const fallbackMcpProfiles = uniqueStrings([String(options.mcpProfile || "").trim()]);
    const mcpProfile = (explicitMcpProfiles.length ? explicitMcpProfiles : fallbackMcpProfiles).join("+");
    const contextSkills = contextNodes.flatMap((node) => stringsFromNodeConfig(node, "skill_ids"));
    const nodeSkills = stringsFromNodeConfig(sourceNode, "skill_ids");
    const skills = uniqueStrings([
      ...nodeSkills,
      ...contextSkills,
      ...(Array.isArray(options.skillIds) ? options.skillIds.map(String) : []),
    ]);
    const nodeSkillInstructions = [
      ...contextNodes.flatMap((node) => asArray(nodeConfig(node).skill_instructions)),
      ...asArray(config.skill_instructions),
    ]
      .filter((item) => item && typeof item === "object" && !Array.isArray(item))
      .map((skill) => ({
        id: String(skill.id || ""),
        label: String(skill.label || skill.id || ""),
        source: String(skill.source || ""),
        prompt_hint: String(skill.prompt_hint || skill.description || skill.label || skill.id || ""),
      }))
      .filter((skill) => skill.id);
    const dependsOn = incomingSources(edges, sourceNode?.id || agentId)
      .map((source) => {
        const sourceNodeForEdge = nodesById.get(source);
        return sourceNodeForEdge && sourceNodeForEdge.kind === "agent"
          ? nodeContractId(sourceNodeForEdge, source)
          : safeStepId(source, source);
      })
      .filter((source) => agentIds.includes(source));
    return {
      id: agentId,
      type: "agent_task",
      provider: String(config.provider || options.provider || "").trim() || "claude-code",
      mcp_profile: mcpProfile,
      skills,
      skill_instructions: uniqueSkillInstructions([...skillInstructions, ...nodeSkillInstructions]),
      goal: String(config.goal || options.goal || "").trim(),
      required_artifacts: uniqueStrings([
        ...stringsFromNodeConfig(sourceNode, "required_artifacts"),
        ...(outputsByAgent.get(agentId) || []),
        ...requiredArtifacts,
      ]),
      ...(dependsOn.length ? { depends_on: dependsOn } : {}),
    };
  });

  if (verifyNodes.length > 0 || outputs.length > 0) {
    steps.push({ id: "validate_evidence", type: "evidence_validate" });
  }
  if (!outputs.every((output) => output.artifact)) {
    steps.push({ id: "render_report", type: "report_render" });
  }

  return {
    id: workflowId,
    name: workflowName,
    version: 1,
    inputs,
    steps,
    outputs,
    ui: { layout },
  };
}

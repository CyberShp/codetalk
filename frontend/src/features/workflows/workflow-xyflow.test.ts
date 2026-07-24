import assert from "node:assert/strict";
import test from "node:test";

import { createNodeFromRegistry, createStarterGraph } from "./workflow-graph.ts";
import type { WorkflowNodeRegistryEntry } from "@/lib/types/workflow";
import {
  authoringGraphToFlow,
  applyFlowPositions,
} from "./workflow-xyflow.ts";

test("AuthoringGraph adapts to xyflow nodes and typed port edges without changing persistence", () => {
  const graph = createStarterGraph("xyflow-adapter", "xyflow adapter");

  const flow = authoringGraphToFlow(graph);

  assert.equal(flow.nodes.length, graph.nodes.length);
  assert.equal(flow.edges.length, graph.edges.length);
  const repoToAnalyze = flow.edges.find((edge) => edge.id === "edge-repo-analyze");
  assert.equal(repoToAnalyze?.sourceHandle, "out:value");
  assert.equal(repoToAnalyze?.targetHandle, "in:repo_path");
  assert.equal(repoToAnalyze?.data?.label, "源码工作区 · directory → repo_path · directory");

  const moved = flow.nodes.map((node) =>
    node.id === "analyze" ? { ...node, position: { x: 640, y: 280 } } : node,
  );
  const updated = applyFlowPositions(graph, moved);
  assert.deepEqual(
    updated.nodes.find((node) => node.id === "analyze")?.position,
    { x: 640, y: 280 },
  );
  assert.deepEqual(updated.edges, graph.edges);
});

test("new nodes materialize their executable ports and config from the Node Registry", () => {
  const definition: WorkflowNodeRegistryEntry = {
    kind: "agent",
    version: 1,
    ui: { label: "智能体", palette_label: "智能体模块", palette_group: "execution", description: "执行分析" },
    default_ports: {
      input_ports: [{ id: "repo_path", type: "directory", required: true }],
      output_ports: [{ id: "analysis", type: "markdown" }],
    },
    default_config: {
      step_id: "agent",
      provider: "builtin-llm",
      goal: "说明该节点要完成的分析目标。",
    },
    config_schema: {},
    ui_schema: { inspector: { field_order: [] } },
  };

  const node = createNodeFromRegistry(definition, 80, 120);

  assert.equal(node.kind, "agent");
  assert.equal(node.label, "智能体");
  assert.equal(node.config.provider, "builtin-llm");
  assert.equal(node.config.step_id, node.id);
  assert.deepEqual(node.config.input_ports, definition.default_ports.input_ports);
  assert.deepEqual(node.config.output_ports, definition.default_ports.output_ports);
  assert.notEqual(node.config.input_ports, definition.default_ports.input_ports);
});

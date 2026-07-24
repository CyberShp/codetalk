import assert from "node:assert/strict";
import test from "node:test";

import {
  connectionLabel,
  connectionEdgeKind,
  createStarterGraph,
  edge,
  inputPortDefinitions,
  outputPortDefinitions,
  validateConnection,
  validateInputPortId,
} from "./workflow-graph.ts";

test("directory and file bind to distinct typed agent inputs", () => {
  const graph = createStarterGraph("typed-inputs", "Typed inputs");
  const analyze = graph.nodes.find((node) => node.id === "analyze");
  assert.ok(analyze);
  analyze.config.input_ports = [
    { id: "repo_path", type: "directory", required: true },
    { id: "design_doc", type: "file", required: false },
  ];
  graph.nodes.push({
    id: "design_doc",
    kind: "input",
    label: "开发设计文档",
    position: { x: 80, y: 360 },
    config: { contract_id: "design_doc", type: "file", resolver: "local" },
  });
  graph.edges = graph.edges.filter((item) => item.target.node_id !== "analyze");

  const repoResult = validateConnection(graph, "repo", "value", "analyze", "repo_path");
  graph.edges.push(edge("repo-analyze", "data", "repo", "value", "analyze", "repo_path"));
  const docResult = validateConnection(graph, "design_doc", "value", "analyze", "design_doc");
  assert.deepEqual(repoResult, { ok: true });
  assert.deepEqual(docResult, { ok: true });
});

test("a scalar input rejects a second edge before it is created", () => {
  const graph = createStarterGraph("occupied-input", "Occupied input");
  graph.nodes.push({
    id: "backup_repo",
    kind: "input",
    label: "备用源码工作区",
    position: { x: 80, y: 360 },
    config: { contract_id: "backup_repo", type: "directory", resolver: "local" },
  });

  assert.deepEqual(
    validateConnection(graph, "backup_repo", "value", "analyze", "repo_path"),
    { ok: false, code: "target_input_occupied", message: "该输入已绑定" },
  );
});

test("file cannot connect to a directory input", () => {
  const graph = createStarterGraph("type-mismatch", "Type mismatch");
  graph.edges = graph.edges.filter((item) => item.target.port_id !== "repo_path");
  graph.nodes.push({
    id: "design_doc",
    kind: "input",
    label: "开发设计文档",
    position: { x: 80, y: 360 },
    config: { contract_id: "design_doc", type: "file", resolver: "local" },
  });

  assert.deepEqual(
    validateConnection(graph, "design_doc", "value", "analyze", "repo_path"),
    {
      ok: false,
      code: "port_type_mismatch",
      message: "不能连接：file 类型不能连接到 directory 输入",
    },
  );
});

test("edge labels expose both user label and typed port contract", () => {
  const graph = createStarterGraph("edge-label", "Edge label");
  const repoEdge = edge("repo-edge", "data", "repo", "value", "analyze", "repo_path");
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));

  assert.equal(
    connectionLabel(repoEdge, nodes),
    "源码工作区 · directory → repo_path · directory",
  );
});

test("agent input port ids reject empty unsafe and duplicate names", () => {
  const ports = [
    { id: "repo_path", type: "directory" },
    { id: "design_doc", type: "file" },
  ];
  assert.equal(validateInputPortId("", ports, 1), "端口名称不能为空");
  assert.equal(validateInputPortId("bad port", ports, 1), "端口名称只能包含字母、数字、点、横线和下划线");
  assert.equal(validateInputPortId("repo_path", ports, 1), "端口名称已存在");
  assert.equal(validateInputPortId("new_doc", ports, 1), "");
});

test("an explicit done output remains a data edge", () => {
  const graph = createStarterGraph("done-data", "Done data");
  const analyze = graph.nodes.find((node) => node.id === "analyze");
  const report = graph.nodes.find((node) => node.id === "report");
  assert.ok(analyze);
  assert.ok(report);
  analyze.config.output_ports = [{ id: "done", type: "markdown" }];

  assert.equal(connectionEdgeKind(analyze, "done", report, "value"), "data");
});

test("executable nodes retain visible control ports alongside typed data ports", () => {
  const graph = createStarterGraph("control-ports", "Control ports");
  const analyze = graph.nodes.find((node) => node.id === "analyze");
  assert.ok(analyze);

  assert.deepEqual(inputPortDefinitions(analyze).map((port) => port.id), ["repo_path", "analysis_target", "start"]);
  assert.deepEqual(outputPortDefinitions(analyze).map((port) => port.id), ["report", "done"]);
  assert.equal(connectionEdgeKind(analyze, "done", analyze, "start"), "dependency");
});

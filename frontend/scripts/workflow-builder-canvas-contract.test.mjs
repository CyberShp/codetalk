import test from "node:test";
import assert from "node:assert/strict";

import { buildWorkflowFromDesigner } from "../src/lib/workflow-builder.mjs";

test("workflow designer canvas nodes and edges materialize into executable DSL", () => {
  const workflow = buildWorkflowFromDesigner({
    workflowId: "custom_canvas_flow",
    workflowName: "Custom Canvas Flow",
    provider: "claude-code",
    mcpProfile: "gitnexus+cgc",
    goal: "分析 iSCSI login 并生成测试交付件",
    skillIds: ["source-evidence-first", "black-box-test-design"],
    selectedSkills: [
      {
        id: "source-evidence-first",
        label: "源码证据优先",
        source: "codetalk_builtin",
        prompt_hint: "先查源码证据",
      },
    ],
    inputSpec: "requirements:file, mr_link:mr_link@agent_mcp",
    outputSpec: "sfmea:json=sfmea.json, cases:test_cases=black_box_cases.json",
    artifacts: "sfmea.json, black_box_cases.json",
    inputLabels: {
      requirements: "需求文档",
      mr_link: "MR 链接",
    },
    outputLabels: {
      sfmea: "SFMEA 表",
      cases: "黑盒测试用例",
    },
    inputSchemas: {},
    outputSchemas: {
      sfmea: { type: "array" },
    },
    evidenceMappings: {},
    semanticImports: {},
    layout: {
      nodes: [
        { id: "requirements", kind: "input", title: "需求文档", subtitle: "", x: 20, y: 20, source: "canvas" },
        { id: "agent_collect", kind: "agent", title: "源码分析 Agent", subtitle: "", x: 260, y: 20, source: "canvas" },
        { id: "agent_review", kind: "agent", title: "复核 Agent", subtitle: "", x: 520, y: 20, source: "canvas" },
        { id: "sfmea", kind: "output", title: "SFMEA 表", subtitle: "", x: 780, y: 20, source: "canvas" },
        { id: "cases", kind: "output", title: "黑盒测试用例", subtitle: "", x: 780, y: 160, source: "canvas" },
        { id: "validation", kind: "verify", title: "验收", subtitle: "", x: 1040, y: 20, source: "canvas" },
      ],
      edges: [
        { id: "e1", source: "requirements", target: "agent_collect" },
        { id: "e2", source: "agent_collect", target: "agent_review" },
        { id: "e3", source: "agent_review", target: "sfmea" },
        { id: "e4", source: "agent_review", target: "cases" },
        { id: "e5", source: "cases", target: "validation" },
      ],
      hidden_node_ids: [],
      hidden_edge_ids: [],
    },
  });

  assert.equal(workflow.id, "custom_canvas_flow");
  assert.deepEqual(workflow.inputs.map((item) => item.id), ["requirements", "mr_link"]);
  assert.deepEqual(
    workflow.steps.filter((step) => step.type === "agent_task").map((step) => step.id),
    ["agent_collect", "agent_review"],
  );
  assert.equal(workflow.steps.find((step) => step.id === "agent_review").depends_on[0], "agent_collect");
  assert.equal(workflow.outputs.find((output) => output.id === "sfmea").from, "agent_review");
  assert.equal(workflow.outputs.find((output) => output.id === "cases").from, "agent_review");
  assert.ok(workflow.steps.some((step) => step.type === "evidence_validate"));
  assert.equal(workflow.ui.layout.nodes.length, 6);
});

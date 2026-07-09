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

test("workflow designer uses per-node config for inputs agents mcp skills and outputs", () => {
  const workflow = buildWorkflowFromDesigner({
    workflowId: "node_config_flow",
    workflowName: "Node Config Flow",
    provider: "claude-code",
    mcpProfile: "",
    goal: "全局目标会被节点目标覆盖",
    skillIds: [],
    selectedSkills: [],
    inputSpec: "",
    outputSpec: "",
    artifacts: "",
    inputSchemas: {},
    outputSchemas: {},
    evidenceMappings: {},
    semanticImports: {},
    layout: {
      nodes: [
        {
          id: "req-node",
          kind: "input",
          title: "需求文件",
          source: "canvas",
          config: { id: "requirements_doc", type: "file", label: "需求说明书" },
        },
        {
          id: "gitnexus-node",
          kind: "context",
          title: "GitNexus",
          source: "canvas",
          config: { mcp_profile: "gitnexus" },
        },
        {
          id: "skills-node",
          kind: "context",
          title: "测试技能",
          source: "canvas",
          config: {
            skill_ids: ["storage-test-design", "sfmea"],
            skill_instructions: [
              {
                id: "storage-test-design",
                label: "存储测试设计",
                prompt_hint: "从黑盒测试人员视角拆分场景",
              },
            ],
          },
        },
        {
          id: "agent-node",
          kind: "agent",
          title: "Claude Agent",
          source: "canvas",
          config: {
            id: "deep_analysis",
            provider: "agent-runtime:default-claude-code",
            goal: "定向阅读源码并生成测试设计",
            required_artifacts: ["design.md"],
          },
        },
        {
          id: "design-output",
          kind: "output",
          title: "测试设计",
          source: "canvas",
          config: {
            id: "test_design",
            type: "markdown",
            label: "测试设计报告",
            artifact: "design.md",
          },
        },
      ],
      edges: [
        { id: "e1", source: "req-node", target: "agent-node" },
        { id: "e2", source: "gitnexus-node", target: "agent-node" },
        { id: "e3", source: "skills-node", target: "agent-node" },
        { id: "e4", source: "agent-node", target: "design-output" },
      ],
    },
  });

  assert.deepEqual(workflow.inputs, [
    {
      id: "requirements_doc",
      label: "需求说明书",
      type: "file",
      required: false,
      resolver: "manual",
      role: "用户提供: 需求说明书",
    },
  ]);
  assert.deepEqual(workflow.outputs, [
    {
      id: "test_design",
      label: "测试设计报告",
      type: "markdown",
      from: "deep_analysis",
      artifact: "design.md",
    },
  ]);
  const step = workflow.steps.find((item) => item.id === "deep_analysis");
  assert.equal(step.provider, "agent-runtime:default-claude-code");
  assert.equal(step.mcp_profile, "gitnexus");
  assert.deepEqual(step.skills, ["storage-test-design", "sfmea"]);
  assert.equal(step.goal, "定向阅读源码并生成测试设计");
  assert.deepEqual(step.required_artifacts, ["design.md"]);
  assert.deepEqual(step.skill_instructions, [
    {
      id: "storage-test-design",
      label: "存储测试设计",
      source: "",
      prompt_hint: "从黑盒测试人员视角拆分场景",
    },
  ]);
});

test("workflow designer aliases named SFMEA outputs to the SFMEA schema", () => {
  const workflow = buildWorkflowFromDesigner({
    workflowId: "schema_alias_flow",
    workflowName: "Schema Alias Flow",
    provider: "claude-code",
    mcpProfile: "",
    goal: "生成登录 SFMEA",
    skillIds: [],
    selectedSkills: [],
    inputSpec: "repo_path:directory@local",
    outputSpec: "login_sfmea:json=login_sfmea.json",
    artifacts: "login_sfmea.json",
    inputSchemas: {},
    outputSchemas: {
      sfmea: { type: "array", items: { type: "object" } },
    },
    evidenceMappings: {},
    semanticImports: {},
    layout: {
      nodes: [],
      edges: [],
    },
  });

  assert.deepEqual(workflow.outputs.find((output) => output.id === "login_sfmea").schema, {
    type: "array",
    items: { type: "object" },
  });
});

test("workflow designer does not materialize contract display nodes as fake inputs or outputs", () => {
  const workflow = buildWorkflowFromDesigner({
    workflowId: "contract_display_flow",
    workflowName: "Contract Display Flow",
    provider: "claude-code",
    mcpProfile: "",
    goal: "验证默认画布节点只做展示",
    skillIds: [],
    selectedSkills: [],
    inputSpec: "repo_path:directory@local",
    outputSpec: "sfmea:json=sfmea.json",
    artifacts: "sfmea.json",
    inputSchemas: {},
    outputSchemas: {
      sfmea: { type: "array" },
    },
    evidenceMappings: {},
    semanticImports: {},
    layout: {
      nodes: [
        { id: "inputs", kind: "input", title: "输入", source: "contract" },
        { id: "outputs", kind: "output", title: "输出", source: "contract" },
        { id: "agent-task", kind: "agent", title: "Agent", source: "contract" },
      ],
      edges: [
        { id: "e1", source: "inputs", target: "agent-task" },
        { id: "e2", source: "agent-task", target: "outputs" },
      ],
    },
  });

  assert.deepEqual(workflow.inputs.map((input) => input.id), ["repo_path"]);
  assert.deepEqual(workflow.outputs.map((output) => output.id), ["sfmea"]);
});

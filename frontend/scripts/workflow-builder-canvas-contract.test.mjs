import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildWorkflowFromDesigner,
  mergeDesignerWorkflowWithDraft,
  mergeDesignerWorkflowWithSpecializedDraft,
} from "../src/lib/workflow-builder.mjs";

const workflowViewSource = readFileSync(
  new URL("../src/app/workbench/workflow-view.tsx", import.meta.url),
  "utf8",
);
const runViewSource = readFileSync(
  new URL("../src/app/workbench/run-view.tsx", import.meta.url),
  "utf8",
);
const controllerSource = readFileSync(
  new URL("../src/app/workbench/workbench-controller.ts", import.meta.url),
  "utf8",
);

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

test("workflow designer accepts input through source context before the agent", () => {
  const workflow = buildWorkflowFromDesigner({
    workflowId: "context_chain",
    workflowName: "Context Chain",
    provider: "claude-code",
    goal: "先构建上下文再分析",
    inputSpec: "analysis_target:free_text",
    outputSpec: "report:markdown=report.md",
    artifacts: "report.md",
    layout: {
      nodes: [
        { id: "input", kind: "input", title: "输入", source: "canvas", config: { id: "analysis_target" } },
        { id: "context", kind: "context", title: "源码上下文", source: "canvas" },
        { id: "agent", kind: "agent", title: "Agent", source: "canvas" },
        { id: "report", kind: "output", title: "报告", source: "canvas" },
      ],
      edges: [
        { id: "e1", source: "input", target: "context" },
        { id: "e2", source: "context", target: "agent" },
        { id: "e3", source: "agent", target: "report" },
      ],
    },
  });
  assert.equal(workflow.steps.filter((step) => step.type === "agent_task").length, 1);
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

test("workflow designer save preserves advanced JSON DSL extensions while applying builder changes", () => {
  const generated = buildWorkflowFromDesigner({
    workflowId: "advanced_json_merge_flow",
    workflowName: "Advanced JSON Merge Flow",
    provider: "opencode",
    mcpProfile: "gitnexus",
    goal: "Use the latest builder settings.",
    skillIds: ["sfmea"],
    selectedSkills: [],
    inputSpec: "analysis_target:free_text",
    outputSpec: "sfmea:json=sfmea.json",
    artifacts: "sfmea.json",
    inputLabels: { analysis_target: "分析对象" },
    outputLabels: { sfmea: "SFMEA" },
    inputSchemas: {},
    outputSchemas: {},
    evidenceMappings: {},
    semanticImports: {},
    layout: {
      nodes: [
        {
          id: "agent-task",
          kind: "agent",
          title: "Agent",
          source: "contract",
          config: { id: "agent_collect" },
        },
      ],
      edges: [],
    },
  });
  const draft = {
    id: "advanced_json_merge_flow",
    name: "Advanced JSON Merge Flow",
    version: 7,
    x_product_note: "must survive save",
    inputs: [
      {
        id: "analysis_target",
        type: "free_text",
        required: false,
        x_prompt_hint: "用户手写的输入提示",
      },
    ],
    steps: [
      {
        id: "agent_collect",
        type: "agent_task",
        provider: "claude-code",
        x_timeout_policy: { soft_sec: 120 },
      },
    ],
    outputs: [
      {
        id: "sfmea",
        type: "json",
        artifact: "old_sfmea.json",
        x_download_group: "交付件",
      },
    ],
    ui: {
      collapsed_panels: ["advanced-json"],
      layout: { nodes: [], edges: [] },
    },
  };

  const merged = mergeDesignerWorkflowWithDraft(generated, draft);

  assert.equal(merged.x_product_note, "must survive save");
  assert.equal(merged.version, 1);
  assert.equal(merged.inputs[0].label, "分析对象");
  assert.equal(merged.inputs[0].required, true);
  assert.equal(merged.inputs[0].x_prompt_hint, "用户手写的输入提示");
  assert.equal(merged.steps[0].provider, "opencode");
  assert.deepEqual(merged.steps[0].x_timeout_policy, { soft_sec: 120 });
  assert.equal(merged.outputs[0].artifact, "sfmea.json");
  assert.equal(merged.outputs[0].x_download_group, "交付件");
  assert.deepEqual(merged.ui.collapsed_panels, ["advanced-json"]);
  assert.equal(merged.ui.layout.nodes[0].id, "agent-task");
});

test("workflow designer rejects cycles instead of saving a non-executable canvas", () => {
  assert.throws(
    () => buildWorkflowFromDesigner({
      workflowId: "cycle_flow",
      workflowName: "Cycle Flow",
      provider: "claude-code",
      goal: "不应保存环路",
      inputSpec: "requirements:file",
      outputSpec: "report:markdown=report.md",
      artifacts: "report.md",
      layout: {
        nodes: [
          { id: "requirements", kind: "input", title: "需求", source: "canvas" },
          { id: "agent_a", kind: "agent", title: "Agent A", source: "canvas" },
          { id: "agent_b", kind: "agent", title: "Agent B", source: "canvas" },
          { id: "report", kind: "output", title: "报告", source: "canvas" },
        ],
        edges: [
          { id: "e1", source: "requirements", target: "agent_a" },
          { id: "e2", source: "agent_a", target: "agent_b" },
          { id: "e3", source: "agent_b", target: "agent_a" },
          { id: "e4", source: "agent_b", target: "report" },
        ],
      },
    }),
    /工作流画布存在环路/,
  );
});

test("workflow designer rejects disconnected custom outputs with an actionable message", () => {
  assert.throws(
    () => buildWorkflowFromDesigner({
      workflowId: "disconnected_output",
      workflowName: "Disconnected Output",
      provider: "claude-code",
      goal: "输出必须接到 Agent",
      inputSpec: "requirements:file",
      outputSpec: "report:markdown=report.md",
      artifacts: "report.md",
      layout: {
        nodes: [
          { id: "requirements", kind: "input", title: "需求", source: "canvas" },
          { id: "agent", kind: "agent", title: "Agent", source: "canvas" },
          { id: "report", kind: "output", title: "报告", source: "canvas" },
        ],
        edges: [{ id: "e1", source: "requirements", target: "agent" }],
      },
    }),
    /输出节点“报告”必须连接到 Agent 节点/,
  );
});

test("specialized workflows keep local steps while canvas contracts become executable", () => {
  const generated = buildWorkflowFromDesigner({
    workflowId: "specialized_canvas",
    workflowName: "Specialized Canvas",
    provider: "claude-code",
    goal: "保留本地步骤并执行新增输入",
    inputSpec: "analysis_target:free_text",
    outputSpec: "report:markdown=report.md",
    artifacts: "report.md",
    layout: {
      nodes: [
        { id: "inputs", kind: "input", title: "输入", source: "contract" },
        { id: "agent-task", kind: "agent", title: "Agent", source: "contract", config: { id: "agent_collect" } },
        {
          id: "mr-node",
          kind: "input",
          title: "MR 链接",
          source: "canvas",
          config: { id: "mr_link_rc", type: "mr_link", resolver: "agent_mcp", label: "MR 链接" },
        },
      ],
      edges: [{ id: "e-mr", source: "mr-node", target: "agent-task" }],
    },
  });
  const draft = {
    id: "specialized_canvas",
    name: "Specialized Canvas",
    version: 3,
    inputs: [{ id: "analysis_target", type: "free_text", required: true }],
    steps: [
      { id: "discover", type: "local_scope_discover" },
      { id: "agent_collect", type: "agent_task", depends_on: ["discover"] },
    ],
    outputs: [{ id: "report", type: "markdown", from: "agent_collect", artifact: "report.md" }],
    ui: { layout: { nodes: [], edges: [] } },
  };

  const merged = mergeDesignerWorkflowWithSpecializedDraft(generated, draft);

  assert.deepEqual(merged.inputs.map((item) => item.id), ["analysis_target", "mr_link_rc"]);
  assert.ok(merged.steps.some((step) => step.id === "discover" && step.type === "local_scope_discover"));
  assert.deepEqual(merged.steps.find((step) => step.id === "agent_collect").depends_on, ["discover"]);
  assert.equal(merged.steps.some((step) => step.id === "agent_task"), false);
  assert.ok(merged.ui.layout.nodes.some((node) => node.id === "mr-node"));
});

test("saving a local-only specialized workflow does not invent an agent step", () => {
  const generated = {
    id: "source_flow_sfmea_blackbox",
    name: "Source flow",
    version: 1,
    inputs: [],
    steps: [
      { id: "agent_task", type: "agent_task", provider: "claude-code" },
      { id: "validate_evidence", type: "evidence_validate" },
    ],
    outputs: [{ id: "sfmea", type: "json", from: "agent_task", artifact: "sfmea.json" }],
    ui: {
      layout: {
        nodes: [
          { id: "agent-task", kind: "agent", source: "contract", config: { id: "agent_task" } },
        ],
        edges: [],
      },
    },
  };
  const draft = {
    id: "source_flow_sfmea_blackbox",
    name: "Source flow",
    version: 1,
    inputs: [],
    steps: [
      { id: "analyze_source_flow", type: "local_source_flow_sfmea_blackbox" },
      { id: "validate_evidence", type: "evidence_validate" },
      { id: "render_report", type: "report_render" },
    ],
    outputs: [
      { id: "sfmea", type: "json", from: "analyze_source_flow", artifact: "sfmea.json" },
    ],
    ui: { layout: { nodes: [], edges: [] } },
  };

  const merged = mergeDesignerWorkflowWithSpecializedDraft(generated, draft);

  assert.deepEqual(
    merged.steps.map((step) => step.id),
    ["analyze_source_flow", "validate_evidence", "render_report"],
  );
  assert.equal(merged.outputs[0].from, "analyze_source_flow");
});

test("designer selector can reload saved custom workflows instead of listing presets only", () => {
  assert.match(workflowViewSource, /已保存自定义工作流/);
  assert.match(workflowViewSource, /saved:\$\{workflow\.id\}/);
  assert.match(workflowViewSource, /从模板库导入/);
  assert.match(controllerSource, /applyWorkflowLayout\(selectedDefinition\)/);
  assert.match(controllerSource, /defaultWorkflowCanvasEdgeIds/);
});

test("cockpit workflow selector prefers saved labels over internal id fallback", () => {
  assert.match(
    runViewSource,
    /\{\[\.\.\.workflowOptions, selectedWorkflowId\]/,
    "saved workflow options must be deduplicated before the id-only fallback",
  );
  assert.doesNotMatch(runViewSource, /\{\[selectedWorkflowId, \.\.\.workflowOptions\]/);
});

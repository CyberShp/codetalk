"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ClipboardList,
  Database,
  Library,
  Loader2,
  PlayCircle,
  RefreshCw,
  Save,
  Search,
  MessageSquareText,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { api } from "@/lib/api";
import type {
  EvidenceMemoryItem,
  EvidenceSourceSlice,
  ExternalAgentStartupProbeResult,
  AgentCommandResolutionDetail,
  AgentRunExecutionResult,
  ArtifactValidationResult,
  MaterializeEvidenceResult,
  MaterializeWorkflowOutputsResult,
  PreparedWorkbenchTaskRun,
  SemanticCase,
  SemanticCaseImportResult,
  TaskRerunExecutionResult,
  TaskRerunHistory,
  TaskRerunPlan,
  TaskRerunPlanValidation,
  WorkbenchDeploymentProbeResult,
  WorkflowDefinition,
  WorkflowExecutionResult,
  WorkflowPreset,
  WorkbenchAcceptanceAudit,
  WorkbenchProviderCapabilitiesMatrix,
  WorkbenchProviderTaskProbeResult,
  WorkbenchSmokeE2EResult,
  WorkbenchSystemAudit,
  WorkbenchTaskArtifact,
  WorkbenchTaskArtifactContent,
  WorkbenchTaskArtifactManifest,
  WorkflowDraftServerAudit,
} from "@/lib/types";

gsap.registerPlugin(useGSAP);

const DEFAULT_WORKFLOW = {
  id: "mr-blackbox-workflow",
  name: "MR 黑盒测试工作流",
  version: 1,
  inputs: [
    {
      id: "mr_link",
      type: "mr_link",
      required: true,
      resolver: "agent_mcp",
      role: "由智能体执行器通过 MCP 凭证读取 MR",
    },
    { id: "design_doc", type: "file", required: false, role: "设计文档" },
    { id: "coverage_report", type: "coverage_report", required: false },
  ],
  steps: [
    {
      id: "agent_collect_mr",
      type: "agent_task",
      provider: "claude-code",
      mcp_profile: "codehub-mcp",
      goal: "读取 MR 差异并产出可校验产物；禁止修改代码。",
      required_artifacts: ["mr_snapshot.json", "diff.patch", "changed_files.json"],
    },
    { id: "validate_evidence", type: "evidence_validate" },
    { id: "render_black_box_cases", type: "report_render" },
  ],
  outputs: [
    { id: "mr_scope", type: "scope_report", from: "validate_evidence" },
    {
      id: "black_box_cases",
      type: "test_cases",
      from: "render_black_box_cases",
      artifact: "black_box_cases.json",
      semantic_import: {
        enabled: true,
        defaults: {
          test_level: "black_box",
          tags: ["mr_blackbox_test"],
        },
      },
    },
  ],
};

const DEFAULT_INPUTS = {
  mr_link: "https://codehub.example.local/group/project/-/merge_requests/1",
  design_doc: "",
  coverage_report: "",
};

type WorkbenchView = "run" | "workflow" | "knowledge" | "diagnostics";

const WORKBENCH_VIEWS: Array<{
  id: WorkbenchView;
  label: string;
  description: string;
}> = [
  {
    id: "run",
    label: "运行驾驶舱",
    description: "准备、执行、验收与复跑",
  },
  {
    id: "workflow",
    label: "工作流设计",
    description: "编排输入、步骤、输出契约",
  },
  {
    id: "knowledge",
    label: "证据与语义",
    description: "沉淀事实、复用测试语义",
  },
  {
    id: "diagnostics",
    label: "执行器体检",
    description: "智能体 CLI、MCP 与部署探测",
  },
];

const WORKFLOW_NAME_ZH: Record<string, string> = {
  "MR Black-box Test Workflow": "MR 黑盒测试工作流",
  "MR Black-box Test Design": "MR 黑盒测试工作流",
  "MR Blackbox Test Workflow": "MR 黑盒测试工作流",
  "Module Analysis": "模块分析工作流",
  "Resource Leak and Error Branch Hunt": "资源/异常路径排查工作流",
  "Resource Leak Hunt": "资源/异常路径排查工作流",
  "Patch Impact Review": "补丁影响面评审工作流",
  "custom_mr_blackbox": "自定义 MR 黑盒测试工作流",
  "mr-blackbox-workflow": "MR 黑盒测试工作流",
  mr_blackbox_test: "MR 黑盒测试工作流",
  module_analysis: "模块分析工作流",
  resource_leak_hunt: "资源/异常路径排查工作流",
  patch_impact: "补丁影响面计划工作流",
};

function workflowDisplayName(workflow: Pick<WorkflowDefinition, "id" | "name"> | string): string {
  const id = typeof workflow === "string" ? workflow : workflow.id;
  const name = typeof workflow === "string" ? "" : String(workflow.name ?? "").trim();
  const normalizedName = WORKFLOW_NAME_ZH[name] ?? name;
  if (normalizedName && !/[A-Za-z]{4,}/.test(normalizedName)) return normalizedName;
  return WORKFLOW_NAME_ZH[id] ?? normalizedName ?? id;
}

const WORKFLOW_BUILDER_SCENARIOS = {
  module_analysis: {
    name: "模块分析",
    inputs: "analysis_object:free_text, design_doc:file, coverage_report:coverage_report",
    outputs: "source_scope:scope_report, risk_findings:json, test_cases:test_cases",
    goal: "分析指定模块，校验源码范围，识别风险路径，并生成面向黑盒验证的测试用例。",
    artifacts: "source_scope.json, risk_findings.json, black_box_cases.json",
  },
  issue_hunt: {
    name: "资源/异常路径排查",
    inputs: "analysis_object:free_text, issue_type:free_text, design_doc:file",
    outputs: "issue_candidates:json, repro_paths:json, test_cases:test_cases",
    goal: "围绕指定问题类型排查资源泄漏或异常分支缺陷，产出可核验源码证据和可观察测试。",
    artifacts: "issue_candidates.json, repro_paths.json, black_box_cases.json",
  },
  mr_blackbox: {
    name: "MR 黑盒测试",
    inputs: "mr_link:mr_link, design_doc:file, coverage_report:coverage_report",
    outputs: "mr_scope:scope_report, changed_behavior:json, black_box_cases:test_cases",
    goal: "使用智能体自持 MCP 凭证读取 MR，识别变更行为和影响范围，并生成黑盒测试用例。",
    artifacts: "mr_snapshot.json, diff.patch, changed_files.json, black_box_cases.json",
  },
  patch_impact: {
    name: "补丁影响面计划",
    inputs: "patch_file:patch, design_doc:file, analysis_object:free_text",
    outputs: "before_after_flow:markdown, impact_scope:scope_report, test_cases:test_cases",
    goal: "读取补丁方案，对比变更前后流程，校验影响范围，并生成实现与测试建议。",
    artifacts: "patch_summary.json, before_after_flow.md, impact_scope.json, black_box_cases.json",
  },
} as const;

const DEFAULT_BUILDER_OUTPUT_SCHEMAS = {
  changed_behavior: {
    type: "object",
    required: ["summary"],
    properties: {
      summary: { type: "string" },
      affected_files: { type: "array" },
    },
  },
  black_box_cases: {
    type: "object",
    required: ["cases"],
    properties: {
      cases: { type: "array" },
    },
  },
};

const DEFAULT_BUILDER_EVIDENCE_MAPPINGS = {
  risk_findings: {
    enabled: true,
    kind: "resource_risk_finding",
    subject_key_field: "finding_id",
    path_field: "file_path",
    symbol_field: "function",
    status: "candidate_output",
    text_fields: ["summary", "risk", "resource", "function"],
  },
  issue_candidates: {
    enabled: true,
    kind: "issue_candidate",
    subject_key_field: "issue_id",
    path_field: "file_path",
    symbol_field: "function",
    status: "candidate_output",
    text_fields: ["summary", "issue_type", "trigger", "function"],
  },
  changed_behavior: {
    enabled: true,
    kind: "changed_behavior",
    subject_key_field: "behavior_id",
    path_field: "file_path",
    symbol_field: "symbol",
    status: "candidate_output",
    text_fields: ["summary", "before", "after", "test_scope"],
  },
  impact_scope: {
    enabled: true,
    kind: "patch_impact_scope",
    subject_key_field: "impact_id",
    path_field: "file_path",
    symbol_field: "symbol",
    status: "candidate_output",
    text_fields: ["summary", "flow_delta", "impact", "risk", "test_scope"],
  },
};

const DEFAULT_BUILDER_SEMANTIC_IMPORTS = {
  black_box_cases: {
    enabled: true,
    defaults: {
      test_level: "black_box",
      reuse_rule: "terminology_only_not_source_truth",
    },
  },
  test_cases: {
    enabled: true,
    defaults: {
      test_level: "black_box",
      reuse_rule: "terminology_only_not_source_truth",
    },
  },
};

const DEFAULT_BUILDER_INPUT_SCHEMAS = {
  patch_file: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  patch_diff: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  design_doc: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  coverage_report: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
};

const DEFAULT_SEMANTIC_CASE = {
  case_id: "nvme_tcp_tls_handshake_fail",
  feature: "NVMe TCP TLS",
  module: "nvmf_tcp",
  test_level: "black_box",
  scenario: "TLS handshake fails and connection is released",
  terms: ["TLS negotiation", "queue pair", "connection release"],
  tags: ["resource_cleanup", "exception_branch"],
  preconditions: ["Target configured with TLS enabled"],
  actions: [
    "Create an NVMe TCP connection with invalid TLS credentials",
    "Observe connection setup failure",
  ],
  expected: [
    "The session is rejected",
    "All allocated connection resources are released",
  ],
  assertion_style: "Prefer observable status, logs, counters, and connection lifecycle checks",
};

const DEFAULT_SEMANTIC_LINES = [
  "TLS handshake fails with invalid credentials -> connection is rejected and resources are released",
  "TLS disabled by configuration -> connection uses the non-TLS path and reports the selected mode",
].join("\n");

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON must be an object");
  }
  return parsed as Record<string, unknown>;
}

function parseJsonValue(value: string): unknown {
  return JSON.parse(value) as unknown;
}

function parseCommaSeparated(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseWorkflowSpecList(value: string, defaultType: string): Array<{
  id: string;
  type: string;
  resolver?: string;
  artifact?: string;
}> {
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

function outputArtifactForSpec(outputId: string, outputType: string, artifacts: string[]): string {
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

function outputSchemaForSpec(
  outputId: string,
  allSchemas: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allSchemas[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const wildcard = allSchemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function outputEvidenceMappingForSpec(
  outputId: string,
  allMappings: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allMappings[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const wildcard = allMappings["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function outputSemanticImportForSpec(
  outputId: string,
  outputType: string,
  allMappings: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allMappings[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const byType = allMappings[`type:${outputType}`];
  if (byType && typeof byType === "object" && !Array.isArray(byType)) {
    return byType as Record<string, unknown>;
  }
  const wildcard = allMappings["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function inputSchemaForSpec(
  inputId: string,
  inputType: string,
  allSchemas: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allSchemas[inputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const byType = allSchemas[`type:${inputType}`];
  if (byType && typeof byType === "object" && !Array.isArray(byType)) {
    return byType as Record<string, unknown>;
  }
  const wildcard = allSchemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function workflowInputsFromJson(value: string): Array<Record<string, unknown>> {
  try {
    const payload = parseJsonObject(value);
    return Array.isArray(payload.inputs)
      ? payload.inputs.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
  } catch {
    return [];
  }
}

type WorkflowDraftAudit = {
  status: "ready" | "warning" | "invalid";
  inputCount: number;
  stepCount: number;
  agentStepCount: number;
  outputCount: number;
  evidenceMemoryOutputCount: number;
  semanticImportOutputCount: number;
  requiredArtifacts: string[];
  warnings: string[];
  blocking: string[];
};

function workflowDraftAudit(value: string): WorkflowDraftAudit {
  const empty: WorkflowDraftAudit = {
    status: "invalid",
    inputCount: 0,
    stepCount: 0,
    agentStepCount: 0,
    outputCount: 0,
    evidenceMemoryOutputCount: 0,
    semanticImportOutputCount: 0,
    requiredArtifacts: [],
    warnings: [],
    blocking: [],
  };
  let payload: Record<string, unknown>;
  try {
    payload = parseJsonObject(value);
  } catch (error) {
    return {
      ...empty,
      blocking: [error instanceof Error ? error.message : "Workflow JSON is invalid"],
    };
  }
  const inputs = Array.isArray(payload.inputs)
    ? payload.inputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const steps = Array.isArray(payload.steps)
    ? payload.steps.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const outputs = Array.isArray(payload.outputs)
    ? payload.outputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const stepIds = new Set(steps.map((step) => String(step.id ?? "")).filter(Boolean));
  const warnings: string[] = [];
  const blocking: string[] = [];
  if (!String(payload.id ?? "").trim()) blocking.push("workflow id is required");
  if (!String(payload.name ?? "").trim()) warnings.push("workflow name is empty");
  if (steps.length === 0) blocking.push("workflow needs at least one step");
  if (outputs.length === 0) warnings.push("workflow has no declared outputs");

  const agentSteps = steps.filter((step) => String(step.type ?? "") === "agent_task");
  const requiredArtifacts = agentSteps.flatMap((step) =>
    Array.isArray(step.required_artifacts)
      ? step.required_artifacts.map((item) => String(item)).filter(Boolean)
      : [],
  );
  for (const step of agentSteps) {
    if (!String(step.provider ?? "").trim()) {
      blocking.push(`agent step ${String(step.id ?? "agent_task")} is missing provider`);
    }
    if (!Array.isArray(step.required_artifacts) || step.required_artifacts.length === 0) {
      warnings.push(`agent step ${String(step.id ?? "agent_task")} has no required_artifacts`);
    }
  }
  for (const output of outputs) {
    const outputId = String(output.id ?? "output");
    const from = String(output.from ?? "");
    const type = String(output.type ?? "");
    if (from && !stepIds.has(from)) {
      blocking.push(`output ${outputId} references unknown step ${from}`);
    }
    if (["json", "scope_report", "test_cases"].includes(type) && !String(output.artifact ?? "")) {
      warnings.push(`output ${outputId} has no artifact path`);
    }
    const evidenceMemory = output.evidence_memory;
    if (
      evidenceMemory &&
      typeof evidenceMemory === "object" &&
      !Array.isArray(evidenceMemory) &&
      !String((evidenceMemory as Record<string, unknown>).kind ?? "").trim()
    ) {
      warnings.push(`output ${outputId} evidence_memory has no kind`);
    }
  }
  const status = blocking.length > 0 ? "invalid" : warnings.length > 0 ? "warning" : "ready";
  return {
    status,
    inputCount: inputs.length,
    stepCount: steps.length,
    agentStepCount: agentSteps.length,
    outputCount: outputs.length,
    evidenceMemoryOutputCount: outputs.filter((output) => Boolean(output.evidence_memory)).length,
    semanticImportOutputCount: outputs.filter((output) => Boolean(output.semantic_import)).length,
    requiredArtifacts: Array.from(new Set(requiredArtifacts)),
    warnings,
    blocking,
  };
}

function inputTextValue(inputs: Record<string, unknown>, input: Record<string, unknown>): string {
  const inputId = String(input.id ?? "");
  const inputType = String(input.type ?? "");
  const value = inputs[inputId];
  if (inputType === "file_set") {
    if (Array.isArray(value)) {
      return value
        .map((item) =>
          item && typeof item === "object" && !Array.isArray(item)
            ? String((item as Record<string, unknown>).path ?? "")
            : String(item ?? ""),
        )
        .filter(Boolean)
        .join("\n");
    }
    return String(value ?? "");
  }
  if (isFileLikeWorkflowInput(inputType)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return String((value as Record<string, unknown>).path ?? "");
    }
    return String(value ?? "");
  }
  return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
}

function updateInputsJsonValue(
  inputsJson: string,
  input: Record<string, unknown>,
  rawValue: string,
): string {
  const payload = parseJsonObject(inputsJson || "{}");
  const inputId = String(input.id ?? "");
  const inputType = String(input.type ?? "");
  if (!inputId) return inputsJson;
  if (inputType === "file_set") {
    payload[inputId] = parseCommaSeparated(rawValue.replace(/\r?\n/g, ",")).map((path) => ({
      path,
    }));
  } else if (isFileLikeWorkflowInput(inputType)) {
    payload[inputId] = rawValue.trim() ? { path: rawValue.trim() } : "";
  } else if (inputType === "boolean") {
    payload[inputId] = rawValue === "true";
  } else if (inputType === "number") {
    payload[inputId] = rawValue.trim() ? Number(rawValue) : "";
  } else {
    payload[inputId] = rawValue;
  }
  return pretty(payload);
}

function isFileLikeWorkflowInput(inputType: string): boolean {
  return ["file", "patch", "diff", "coverage_report"].includes(inputType);
}

function semanticCasesFromLines({
  feature,
  module,
  text,
}: {
  feature: string;
  module: string;
  text: string;
}): Record<string, unknown> {
  const safeFeature = feature.trim() || "Imported feature";
  const safeModule = module.trim() || "module";
  const cases = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const [scenarioText, expectedText] = line.split(/\s*->\s*/, 2);
      const caseSuffix = scenarioText
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 48) || `case_${index + 1}`;
      return {
        case_id: `${safeModule}_${caseSuffix}_${index + 1}`,
        feature: safeFeature,
        module: safeModule,
        test_level: "black_box",
        scenario: scenarioText || line,
        terms: Array.from(new Set([
          ...safeFeature.split(/\s+/),
          ...safeModule.split(/[/_.\-\s]+/),
        ])).filter(Boolean),
        tags: ["imported_semantic_case"],
        preconditions: [],
        actions: [scenarioText || line],
        expected: [expectedText || "Expected observable behavior matches the existing feature case."],
        assertion_style: "Prefer existing black-box terminology, observable status, logs, counters, and lifecycle checks.",
        source_ref: "workbench_semantic_text_import",
      };
    });
  return {
    defaults: {
      feature: safeFeature,
      module: safeModule,
      test_level: "black_box",
    },
    source_ref: "workbench_semantic_text_import",
    cases,
  };
}

function isBulkSemanticImportPayload(value: unknown): boolean {
  if (Array.isArray(value)) return true;
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return Array.isArray(payload.cases) || Array.isArray(payload.items);
}

function fastContextDecisionSummary(taskBundle: Record<string, unknown>): string {
  const decisions = taskBundle.context_discovery_decision;
  if (!decisions || typeof decisions !== "object" || Array.isArray(decisions)) {
    return "";
  }
  const fastContext = (decisions as Record<string, unknown>)["fast-context"];
  if (!fastContext || typeof fastContext !== "object" || Array.isArray(fastContext)) {
    return "";
  }
  const decision = fastContext as Record<string, unknown>;
  if (decision.codetalk_callable === true) {
    return "fast-context: CodeTalk callable";
  }
  const fallbackPath = Array.isArray(decision.fallback_path)
    ? decision.fallback_path.map((item) => String(item)).filter(Boolean)
    : [];
  const lastFallback = fallbackPath[fallbackPath.length - 1] || "local_search";
  return `fast-context: fallback to ${lastFallback}`;
}

type InputContextFileSummary = {
  inputId: string;
  kind: string;
  filename: string;
  suffix: string;
  chunkCount: number;
  textTruncated: boolean;
  parseWarnings: string[];
};

type InputContextSummary = {
  fileCount: number;
  inputs: InputContextFileSummary[];
};

type AgentMcpRequestSummary = {
  inputId: string;
  inputType: string;
  credentialOwner: string;
  codetalkFetchAllowed: boolean;
  mcpProfiles: string[];
  requiredArtifacts: string[];
};

type ProviderReadinessSummary = {
  status: string;
  repoStatus: string;
  blockingReasons: string[];
  warnings: string[];
  agentProviders: Array<{
    provider: string;
    status: string;
    reason: string;
    startupProbeEndpoint: string;
    manualProbeCommand: string;
    configuredCommand: string;
    usedFallback: boolean;
    deploymentTaskProbeStatus: string;
    deploymentProbeId: string;
    deploymentEvidenceConflict: boolean;
  }>;
  codetalkProviders: Array<{ provider: string; status: string; nextCheck: string }>;
};

function inputContextSummary(taskBundle: Record<string, unknown>): InputContextSummary | null {
  const inputContext = taskBundle.input_context;
  if (!inputContext || typeof inputContext !== "object" || Array.isArray(inputContext)) {
    return null;
  }
  const payload = inputContext as Record<string, unknown>;
  const rawInputs = Array.isArray(payload.inputs) ? payload.inputs : [];
  const inputs = rawInputs.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const rawInput = item as Record<string, unknown>;
    const rawFiles = Array.isArray(rawInput.files) ? rawInput.files : [rawInput];
    return rawFiles
      .filter((file): file is Record<string, unknown> =>
        Boolean(file && typeof file === "object" && !Array.isArray(file)),
      )
      .map((file) => ({
        inputId: String(file.input_id ?? rawInput.input_id ?? ""),
        kind: String(file.kind ?? rawInput.kind ?? ""),
        filename: String(file.filename ?? file.original_path ?? file.copied_path ?? ""),
        suffix: String(file.suffix ?? ""),
        chunkCount: Number(file.chunk_count ?? 0) || 0,
        textTruncated: file.text_truncated === true,
        parseWarnings: Array.isArray(file.parse_warnings)
          ? file.parse_warnings.map((warning) => String(warning)).filter(Boolean)
          : [],
      }))
      .filter((file) => file.filename || file.inputId);
  });
  const fileCount = Number(payload.file_count ?? inputs.length) || inputs.length;
  if (!fileCount && inputs.length === 0) return null;
  return { fileCount, inputs };
}

function agentMcpRequestSummary(taskBundle: Record<string, unknown>): AgentMcpRequestSummary[] {
  const rawRequests = Array.isArray(taskBundle.agent_mcp_requests)
    ? taskBundle.agent_mcp_requests
    : [];
  return rawRequests.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const request = item as Record<string, unknown>;
    const artifactValidation =
      request.artifact_validation &&
      typeof request.artifact_validation === "object" &&
      !Array.isArray(request.artifact_validation)
        ? (request.artifact_validation as Record<string, unknown>)
        : {};
    return [{
      inputId: String(request.input_id ?? ""),
      inputType: String(request.input_type ?? ""),
      credentialOwner: String(request.credential_owner ?? ""),
      codetalkFetchAllowed: request.codetalk_fetch_allowed === true,
      mcpProfiles: Array.isArray(request.mcp_profiles)
        ? request.mcp_profiles.map((value) => String(value)).filter(Boolean)
        : [],
      requiredArtifacts: Array.isArray(artifactValidation.required_artifacts)
        ? artifactValidation.required_artifacts.map((value) => String(value)).filter(Boolean)
        : [],
    }];
  });
}

function providerReadinessSummary(
  taskBundle: Record<string, unknown>,
): ProviderReadinessSummary | null {
  const raw = taskBundle.provider_readiness;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const readiness = raw as Record<string, unknown>;
  const summary =
    readiness.summary && typeof readiness.summary === "object" && !Array.isArray(readiness.summary)
      ? (readiness.summary as Record<string, unknown>)
      : {};
  const repo =
    readiness.repo && typeof readiness.repo === "object" && !Array.isArray(readiness.repo)
      ? (readiness.repo as Record<string, unknown>)
      : {};
  const agentProviders = Object.entries(
    readiness.agent_cli_providers &&
      typeof readiness.agent_cli_providers === "object" &&
      !Array.isArray(readiness.agent_cli_providers)
      ? (readiness.agent_cli_providers as Record<string, unknown>)
      : {},
  ).flatMap(([provider, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const payload = value as Record<string, unknown>;
    const deploymentEvidence =
      payload.deployment_evidence &&
      typeof payload.deployment_evidence === "object" &&
      !Array.isArray(payload.deployment_evidence)
        ? (payload.deployment_evidence as Record<string, unknown>)
        : {};
    return [{
      provider,
      status: String(payload.status ?? "unknown"),
      reason: String(payload.reason ?? ""),
      startupProbeEndpoint: String(payload.startup_probe_endpoint ?? ""),
      manualProbeCommand: String(payload.manual_probe_command ?? ""),
      configuredCommand: String(payload.configured_command ?? payload.command ?? ""),
      usedFallback: Boolean(payload.used_fallback ?? false),
      deploymentTaskProbeStatus: String(deploymentEvidence.task_probe_status ?? ""),
      deploymentProbeId: String(deploymentEvidence.probe_id ?? ""),
      deploymentEvidenceConflict: Boolean(payload.deployment_evidence_conflict ?? false),
    }];
  });
  const codetalkProviders = Object.entries(
    readiness.codetalk_providers &&
      typeof readiness.codetalk_providers === "object" &&
      !Array.isArray(readiness.codetalk_providers)
      ? (readiness.codetalk_providers as Record<string, unknown>)
      : {},
  ).flatMap(([provider, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const payload = value as Record<string, unknown>;
    return [{
      provider,
      status: String(payload.status ?? "unknown"),
      nextCheck: String(payload.next_check ?? ""),
    }];
  });
  return {
    status: String(summary.status ?? "unknown"),
    repoStatus: String(repo.status ?? "unknown"),
    blockingReasons: Array.isArray(summary.blocking_reasons)
      ? summary.blocking_reasons.map((item) => String(item)).filter(Boolean)
      : [],
    warnings: Array.isArray(summary.warnings)
      ? summary.warnings.map((item) => String(item)).filter(Boolean)
      : [],
    agentProviders,
    codetalkProviders,
  };
}

type EvidenceValidationSummary = {
  acceptedCount: number;
  rejectedCount: number;
  acceptedDetails: Array<{ artifact: string; sha256: string; sourceStepId: string }>;
  rejectedDetails: Array<{ artifact: string; reason: string; sourceStepId: string }>;
};

type WorkflowOutputMaterializationSummary = {
  evidenceCount: number;
  rejectedCount: number;
  workflowOutputsSha: string;
  outputCount: number;
  auditSummary: {
    declaredOutputCount: number;
    evidenceMemoryDeclaredCount: number;
    materializedOutputCount: number;
    rejectedOutputCount: number;
    rejectedItemCount: number;
  };
  auditOutputs: Array<{
    outputId: string;
    declaredType: string;
    artifact: string;
    from: string;
    producedStatus: string;
    materializationStatus: string;
    evidenceMemoryDeclared: boolean;
    mappingKind: string;
    materializedCount: number;
    rejectedCount: number;
    rejectionReasons: string[];
  }>;
  materializedEvidence: Array<{
    evidenceId: string;
    kind: string;
    subjectKey: string;
    outputId: string;
    sourceStepId: string;
    mappingKind: string;
  }>;
  firstRejected?: {
    output: string;
    reason: string;
    status: string;
    schemaErrorCount: number;
  };
};

type ReplayPlanSummary = {
  replayStatus: string;
  provider: string;
  turnId: string;
  promptSource: string;
  promptTransport: string;
  cwd: string;
  timeoutSec: number;
  readonlyRequired: boolean;
  validatesOutputs: boolean;
  hashCount: number;
  taskBundleSha: string;
  executionInputSha: string;
  contractSha: string;
};

type ExecutionInputSummary = {
  provider: string;
  turnId: string;
  promptTransport: string;
  promptTransportReason: string;
  timeoutSec: number;
  cwd: string;
  stdinRedacted: boolean;
  stdinSha: string;
  readonlyEnv: string;
  outputContractSha: string;
};

type BlackBoxGenerationPolicySummary = {
  termCount: number;
  caseCount: number;
  firstCaseId: string;
  firstTerms: string[];
  allowedUses: string[];
  mustNotUse: string[];
  authorityRule: string;
};

type MemoryArtifactSummary = {
  kind: "memory_retrieval" | "context_bundle";
  query: string;
  evidenceCount: number;
  deploymentCount: number;
  semanticCount: number;
  sourceSliceCount: number;
  firstSubject: string;
  firstReuseReason: string;
  firstDeploymentSubject: string;
};

type InputMaterialsSummary = {
  materialCount: number;
  readOrder: string[];
  firstInputId: string;
  firstRole: string;
  firstFilename: string;
  firstSha: string;
  firstChunksPath: string;
  mustRead: boolean;
  materialsAreSourceTruth: boolean;
};

type FailureRetryContextSummary = {
  stepId: string;
  failureKind: string;
  retryable: boolean;
  exitCode: string;
  missingArtifacts: string[];
  stdoutExcerpt: string;
  stderrExcerpt: string;
  mustProduceArtifacts: string[];
  doNotRepeat: string[];
};

function commandResolutionLines(resolution?: AgentCommandResolutionDetail): string[] {
  if (!resolution) return [];
  const lines = [
    resolution.method ? `method:${resolution.method}` : "",
    resolution.which ? `which:${resolution.which}` : "",
    resolution.where_exe ? `where:${resolution.where_exe}` : "",
    typeof resolution.where_returncode === "number" ? `where_exit:${resolution.where_returncode}` : "",
    resolution.common_dir_path ? `common:${resolution.common_dir_path}` : "",
    resolution.powershell_get_command ? `ps:${resolution.powershell_get_command}` : "",
    resolution.path ? `path:${resolution.path}` : "",
  ].filter(Boolean);
  if (resolution.where_stderr && lines.length < 6) {
    lines.push(`where_stderr:${resolution.where_stderr}`);
  }
  return lines.slice(0, 6);
}

type AcceptanceProviderIssue = {
  provider: string;
  status: string;
  reason: string;
  startupProbeEndpoint: string;
  usedFallback: boolean;
  deploymentTaskProbeStatus: string;
  deploymentProbeId: string;
  deploymentEvidenceConflict: boolean;
};

type AcceptanceWorkflowOutputIssue = {
  outputId: string;
  status: string;
  reason: string;
  artifact: string;
  schemaErrorCount: number;
};

type AcceptanceInstructionPolicyIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  expectedFiles: string[];
};

type AcceptanceInputRedactionIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  stdinSha: string;
};

function acceptanceProviderIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceProviderIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => String(item.id ?? "").startsWith("provider_readiness_agent:"))
    .map((item) => ({
      provider: String(item.provider ?? String(item.id ?? "").split(":")[1] ?? "agent"),
      status: String(item.provider_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      startupProbeEndpoint: String(item.startup_probe_endpoint ?? ""),
      usedFallback: Boolean(item.used_fallback ?? false),
      deploymentTaskProbeStatus: String(item.deployment_task_probe_status ?? ""),
      deploymentProbeId: String(item.deployment_probe_id ?? ""),
      deploymentEvidenceConflict: Boolean(item.deployment_evidence_conflict ?? false),
    }))
    .filter((item) => item.provider);
}

function acceptanceCodetalkProviderIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceProviderIssue[] {
  if (!audit) return [];
  return audit.missing_recommended
    .filter((item) => String(item.id ?? "").startsWith("provider_readiness_codetalk:"))
    .map((item) => ({
      provider: String(item.provider ?? String(item.id ?? "").split(":")[1] ?? "provider"),
      status: String(item.provider_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      startupProbeEndpoint: String(item.startup_probe_endpoint ?? item.next_check ?? ""),
      usedFallback: false,
      deploymentTaskProbeStatus: "",
      deploymentProbeId: "",
      deploymentEvidenceConflict: false,
    }))
    .filter((item) => item.provider);
}

function acceptanceWorkflowOutputIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceWorkflowOutputIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => String(item.id ?? "").startsWith("workflow_output:"))
    .map((item) => ({
      outputId: String(item.output_id ?? String(item.id ?? "").split(":")[1] ?? "output"),
      status: String(item.output_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      artifact: String(item.artifact ?? ""),
      schemaErrorCount: Array.isArray(item.schema_errors) ? item.schema_errors.length : 0,
    }))
    .filter((item) => item.outputId);
}

function acceptanceInstructionPolicyIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceInstructionPolicyIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => {
      const id = String(item.id ?? "");
      return (
        id.startsWith("agent_instruction_policy:") ||
        id.startsWith("agent_turn_instruction_policy:")
      );
    })
    .map((item) => {
      const id = String(item.id ?? "");
      const parts = id.split(":");
      const expectedFiles = Array.isArray(item.expected_files)
        ? item.expected_files
            .filter((file): file is Record<string, unknown> =>
              Boolean(file && typeof file === "object" && !Array.isArray(file)),
            )
            .map((file) => String(file.relative_path ?? ""))
            .filter(Boolean)
        : [];
      const label = id.startsWith("agent_turn_instruction_policy:")
        ? `${parts[1] ?? "step"} ${parts[2] ?? "turn"} ${parts[3] ?? "artifact"}`
        : `${parts[1] ?? "step"} ${parts[2] ?? "artifact"}`;
      return {
        id,
        label,
        reason: String(item.reason ?? ""),
        relativePath: String(item.relative_path ?? ""),
        expectedFiles,
      };
    });
}

function acceptanceInputRedactionIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceInputRedactionIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => {
      const id = String(item.id ?? "");
      return (
        id.startsWith("agent_stdin_redaction:") ||
        id.startsWith("agent_turn_stdin_redaction:")
      );
    })
    .map((item) => {
      const id = String(item.id ?? "");
      const parts = id.split(":");
      const label = id.startsWith("agent_turn_stdin_redaction:")
        ? `${parts[1] ?? "step"} ${parts[2] ?? "turn"} ${parts[3] ?? "artifact"}`
        : `${parts[1] ?? "step"} ${parts[2] ?? "artifact"}`;
      return {
        id,
        label,
        reason: String(item.reason ?? ""),
        relativePath: String(item.relative_path ?? ""),
        stdinSha: String(item.stdin_json_sha256 ?? ""),
      };
    });
}

function evidenceValidationSummary(
  artifact: WorkbenchTaskArtifactContent,
): EvidenceValidationSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "evidence_validation" &&
    !("accepted_artifact_details" in payload) &&
    !("rejected_artifact_details" in payload)
  ) {
    return null;
  }
  const acceptedDetails = Array.isArray(payload.accepted_artifact_details)
    ? payload.accepted_artifact_details
        .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
        .map((item) => ({
          artifact: String(item.artifact ?? ""),
          sha256: String(item.sha256 ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
        }))
        .filter((item) => item.artifact)
    : [];
  const rejectedDetails = Array.isArray(payload.rejected_artifact_details)
    ? payload.rejected_artifact_details
        .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
        .map((item) => ({
          artifact: String(item.artifact ?? ""),
          reason: String(item.reason ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
        }))
        .filter((item) => item.artifact || item.reason)
    : [];
  return {
    acceptedCount: Number(payload.accepted_count ?? acceptedDetails.length) || 0,
    rejectedCount: Number(payload.rejected_count ?? rejectedDetails.length) || 0,
    acceptedDetails,
    rejectedDetails,
  };
}

function workflowOutputMaterializationSummary(
  artifact: WorkbenchTaskArtifactContent,
): WorkflowOutputMaterializationSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "workflow_output_materialization" &&
    !("workflow_outputs_artifact" in payload)
  ) {
    return null;
  }
  const workflowOutputsArtifact =
    payload.workflow_outputs_artifact &&
    typeof payload.workflow_outputs_artifact === "object" &&
    !Array.isArray(payload.workflow_outputs_artifact)
      ? (payload.workflow_outputs_artifact as Record<string, unknown>)
      : {};
  const rejectedOutputs = Array.isArray(payload.rejected_outputs)
    ? payload.rejected_outputs
    : [];
  const firstRejectedPayload =
    rejectedOutputs[0] &&
    typeof rejectedOutputs[0] === "object" &&
    !Array.isArray(rejectedOutputs[0])
      ? (rejectedOutputs[0] as Record<string, unknown>)
      : null;
  const schemaErrors = Array.isArray(firstRejectedPayload?.schema_errors)
    ? firstRejectedPayload.schema_errors
    : [];
  const materializedEvidence = Array.isArray(payload.materialized_evidence)
    ? payload.materialized_evidence
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => ({
          evidenceId: String(item.evidence_id ?? ""),
          kind: String(item.kind ?? ""),
          subjectKey: String(item.subject_key ?? ""),
          outputId: String(item.output_id ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
          mappingKind: String(item.mapping_kind ?? ""),
        }))
        .filter((item) => item.evidenceId || item.kind || item.subjectKey)
    : [];
  const audit =
    payload.materialization_audit &&
    typeof payload.materialization_audit === "object" &&
    !Array.isArray(payload.materialization_audit)
      ? (payload.materialization_audit as Record<string, unknown>)
      : {};
  const auditSummary =
    audit.summary && typeof audit.summary === "object" && !Array.isArray(audit.summary)
      ? (audit.summary as Record<string, unknown>)
      : {};
  const auditOutputs = Array.isArray(audit.outputs)
    ? audit.outputs
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => {
          const mapping =
            item.evidence_memory_mapping &&
            typeof item.evidence_memory_mapping === "object" &&
            !Array.isArray(item.evidence_memory_mapping)
              ? (item.evidence_memory_mapping as Record<string, unknown>)
              : {};
          return {
            outputId: String(item.output_id ?? ""),
            declaredType: String(item.declared_type ?? ""),
            artifact: String(item.artifact ?? ""),
            from: String(item.from ?? ""),
            producedStatus: String(item.produced_status ?? ""),
            materializationStatus: String(item.materialization_status ?? ""),
            evidenceMemoryDeclared: Boolean(item.evidence_memory_declared),
            mappingKind: String(mapping.kind ?? ""),
            materializedCount: Number(item.materialized_count ?? 0) || 0,
            rejectedCount: Number(item.rejected_count ?? 0) || 0,
            rejectionReasons: Array.isArray(item.rejection_reasons)
              ? item.rejection_reasons.map((reason) => String(reason)).filter(Boolean)
              : [],
          };
        })
        .filter((item) => item.outputId)
    : [];
  return {
    evidenceCount: Number(payload.evidence_count ?? 0) || 0,
    rejectedCount: rejectedOutputs.length,
    workflowOutputsSha: String(workflowOutputsArtifact.sha256 ?? ""),
    outputCount: Number(workflowOutputsArtifact.output_count ?? 0) || 0,
    auditSummary: {
      declaredOutputCount: Number(auditSummary.declared_output_count ?? 0) || 0,
      evidenceMemoryDeclaredCount:
        Number(auditSummary.evidence_memory_declared_count ?? 0) || 0,
      materializedOutputCount: Number(auditSummary.materialized_output_count ?? 0) || 0,
      rejectedOutputCount: Number(auditSummary.rejected_output_count ?? 0) || 0,
      rejectedItemCount: Number(auditSummary.rejected_item_count ?? 0) || 0,
    },
    auditOutputs,
    materializedEvidence,
    firstRejected: firstRejectedPayload
      ? {
          output: String(firstRejectedPayload.output ?? ""),
          reason: String(firstRejectedPayload.reason ?? ""),
          status: String(firstRejectedPayload.output_status ?? ""),
          schemaErrorCount: schemaErrors.length,
        }
      : undefined,
  };
}

function materializationAuditOutputs(
  result: MaterializeWorkflowOutputsResult,
): WorkflowOutputMaterializationSummary["auditOutputs"] {
  const outputs = result.materialization_audit?.outputs;
  if (!Array.isArray(outputs)) return [];
  return outputs
    .filter((item): item is Record<string, unknown> =>
      Boolean(item && typeof item === "object" && !Array.isArray(item)),
    )
    .map((item) => {
      const mapping =
        item.evidence_memory_mapping &&
        typeof item.evidence_memory_mapping === "object" &&
        !Array.isArray(item.evidence_memory_mapping)
          ? (item.evidence_memory_mapping as Record<string, unknown>)
          : {};
      return {
        outputId: String(item.output_id ?? ""),
        declaredType: String(item.declared_type ?? ""),
        artifact: String(item.artifact ?? ""),
        from: String(item.from ?? ""),
        producedStatus: String(item.produced_status ?? ""),
        materializationStatus: String(item.materialization_status ?? ""),
        evidenceMemoryDeclared: Boolean(item.evidence_memory_declared),
        mappingKind: String(mapping.kind ?? ""),
        materializedCount: Number(item.materialized_count ?? 0) || 0,
        rejectedCount: Number(item.rejected_count ?? 0) || 0,
        rejectionReasons: Array.isArray(item.rejection_reasons)
          ? item.rejection_reasons.map((reason) => String(reason)).filter(Boolean)
          : [],
      };
    })
    .filter((item) => item.outputId);
}

function replayPlanSummary(
  artifact: WorkbenchTaskArtifactContent,
): ReplayPlanSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_replay_plan" &&
    artifact.kind !== "agent_turn_replay_plan" &&
    !("replay_status" in payload)
  ) {
    return null;
  }
  const safety =
    payload.safety_boundary &&
    typeof payload.safety_boundary === "object" &&
    !Array.isArray(payload.safety_boundary)
      ? (payload.safety_boundary as Record<string, unknown>)
      : {};
  const hashes =
    payload.artifact_hashes &&
    typeof payload.artifact_hashes === "object" &&
    !Array.isArray(payload.artifact_hashes)
      ? (payload.artifact_hashes as Record<string, unknown>)
      : {};
  return {
    replayStatus: String(payload.replay_status ?? "unknown"),
    provider: String(payload.provider ?? ""),
    turnId: String(payload.turn_id ?? ""),
    promptSource: String(payload.prompt_source ?? ""),
    promptTransport: String(payload.prompt_transport ?? ""),
    cwd: String(payload.cwd ?? ""),
    timeoutSec: Number(payload.timeout_sec ?? 0) || 0,
    readonlyRequired: Boolean(safety.readonly_env_required ?? false),
    validatesOutputs: Boolean(safety.codetalk_validates_outputs ?? false),
    hashCount: Object.keys(hashes).length,
    taskBundleSha: String(hashes["task_bundle.json"] ?? hashes.task_bundle_sha256 ?? ""),
    executionInputSha: String(hashes["execution_input.json"] ?? hashes.stdin_json_sha256 ?? ""),
    contractSha: String(
      hashes["agent_output_contract.json"] ?? hashes.agent_output_contract_sha256 ?? "",
    ),
  };
}

function executionInputSummary(
  artifact: WorkbenchTaskArtifactContent,
): ExecutionInputSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_execution_input" &&
    artifact.kind !== "agent_turn_execution_input" &&
    !("stdin_redacted" in payload)
  ) {
    return null;
  }
  const envHints =
    payload.env_hints &&
    typeof payload.env_hints === "object" &&
    !Array.isArray(payload.env_hints)
      ? (payload.env_hints as Record<string, unknown>)
      : {};
  return {
    provider: String(payload.provider ?? ""),
    turnId: String(payload.turn_id ?? ""),
    promptTransport: String(payload.prompt_transport ?? ""),
    promptTransportReason: String(payload.prompt_transport_reason ?? ""),
    timeoutSec: Number(payload.timeout_sec ?? 0) || 0,
    cwd: String(payload.cwd ?? ""),
    stdinRedacted: Boolean(payload.stdin_redacted ?? false),
    stdinSha: String(payload.stdin_json_sha256 ?? ""),
    readonlyEnv: String(envHints.CODETALK_AGENT_READONLY ?? ""),
    outputContractSha: String(payload.agent_output_contract_sha256 ?? ""),
  };
}

function blackBoxGenerationPolicySummary(
  artifact: WorkbenchTaskArtifactContent,
): BlackBoxGenerationPolicySummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "black_box_generation_policy" &&
    !("semantic_terms" in payload)
  ) {
    return null;
  }
  const semanticTerms = Array.isArray(payload.semantic_terms)
    ? payload.semantic_terms.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const firstTerm = semanticTerms[0] ?? {};
  const firstTerms = Array.isArray(firstTerm.terms)
    ? firstTerm.terms.map((item) => String(item)).filter(Boolean)
    : [];
  return {
    termCount: Number(payload.semantic_term_count ?? 0) || firstTerms.length,
    caseCount: Number(payload.semantic_case_count ?? 0) || semanticTerms.length,
    firstCaseId: String(firstTerm.case_id ?? ""),
    firstTerms,
    allowedUses: Array.isArray(payload.allowed_uses)
      ? payload.allowed_uses.map((item) => String(item)).filter(Boolean)
      : [],
    mustNotUse: Array.isArray(payload.must_not_use_semantics_as)
      ? payload.must_not_use_semantics_as.map((item) => String(item)).filter(Boolean)
      : [],
    authorityRule: String(payload.authority_rule ?? ""),
  };
}

function memoryArtifactSummary(
  artifact: WorkbenchTaskArtifactContent,
): MemoryArtifactSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  const isMemoryRetrieval =
    artifact.kind === "memory_retrieval" ||
    "retrieved_count" in payload ||
    "deployment_retrieved_count" in payload;
  const isContextBundle =
    artifact.kind === "context_bundle" ||
    ("evidence" in payload && "semantic_cases" in payload);
  if (!isMemoryRetrieval && !isContextBundle) return null;
  const evidenceItems = Array.isArray(payload.items)
    ? payload.items
    : Array.isArray(payload.evidence)
      ? payload.evidence
      : [];
  const deploymentItems = Array.isArray(payload.deployment_items)
    ? payload.deployment_items
    : Array.isArray(payload.deployment_evidence)
      ? payload.deployment_evidence
      : [];
  const semanticItems = Array.isArray(payload.semantic_cases) ? payload.semantic_cases : [];
  const firstEvidence =
    evidenceItems[0] && typeof evidenceItems[0] === "object" && !Array.isArray(evidenceItems[0])
      ? (evidenceItems[0] as Record<string, unknown>)
      : {};
  const firstDeployment =
    deploymentItems[0] &&
    typeof deploymentItems[0] === "object" &&
    !Array.isArray(deploymentItems[0])
      ? (deploymentItems[0] as Record<string, unknown>)
      : {};
  const sourceSliceCount =
    Number(payload.source_slice_count ?? 0) ||
    evidenceItems.reduce((total, item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return total;
      const record = item as Record<string, unknown>;
      if (Array.isArray(record.source_slices)) return total + record.source_slices.length;
      if (Array.isArray(record.source_slice_refs)) return total + record.source_slice_refs.length;
      return total + (Number(record.source_slice_count ?? 0) || 0);
    }, 0);
  return {
    kind: isMemoryRetrieval ? "memory_retrieval" : "context_bundle",
    query: String(payload.query ?? ""),
    evidenceCount: Number(payload.retrieved_count ?? 0) || evidenceItems.length,
    deploymentCount: Number(payload.deployment_retrieved_count ?? 0) || deploymentItems.length,
    semanticCount: Number(payload.semantic_retrieved_count ?? 0) || semanticItems.length,
    sourceSliceCount,
    firstSubject: String(firstEvidence.subject_key ?? firstEvidence.path ?? ""),
    firstReuseReason: String(firstEvidence.reuse_reason ?? firstEvidence.reason ?? ""),
    firstDeploymentSubject: String(
      firstDeployment.subject_key ?? firstDeployment.provider ?? firstDeployment.symbol ?? "",
    ),
  };
}

function inputMaterialsSummary(
  artifact: WorkbenchTaskArtifactContent,
): InputMaterialsSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (artifact.kind !== "input_materials" && payload.kind !== "input_materials") {
    return null;
  }
  const materials = Array.isArray(payload.materials)
    ? payload.materials.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const first = materials[0] ?? {};
  const rules =
    payload.rules && typeof payload.rules === "object" && !Array.isArray(payload.rules)
      ? (payload.rules as Record<string, unknown>)
      : {};
  return {
    materialCount: Number(payload.material_count ?? 0) || materials.length,
    readOrder: Array.isArray(payload.read_order)
      ? payload.read_order.map((item) => String(item)).filter(Boolean)
      : [],
    firstInputId: String(first.input_id ?? ""),
    firstRole: String(first.material_role ?? ""),
    firstFilename: String(first.filename ?? ""),
    firstSha: String(first.sha256 ?? ""),
    firstChunksPath: String(first.chunks_path ?? ""),
    mustRead: Boolean(rules.agent_must_read_materials ?? false),
    materialsAreSourceTruth: Boolean(rules.materials_are_source_truth ?? false),
  };
}

function failureRetryContextSummary(
  artifact: WorkbenchTaskArtifactContent,
): FailureRetryContextSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_failure_retry_context" &&
    payload.kind !== "agent_failure_retry_context"
  ) {
    return null;
  }
  const previousExecution =
    payload.previous_execution &&
    typeof payload.previous_execution === "object" &&
    !Array.isArray(payload.previous_execution)
      ? (payload.previous_execution as Record<string, unknown>)
      : {};
  const previousOutput =
    payload.previous_output &&
    typeof payload.previous_output === "object" &&
    !Array.isArray(payload.previous_output)
      ? (payload.previous_output as Record<string, unknown>)
      : {};
  const retryInstructions =
    payload.retry_instructions &&
    typeof payload.retry_instructions === "object" &&
    !Array.isArray(payload.retry_instructions)
      ? (payload.retry_instructions as Record<string, unknown>)
      : {};
  return {
    stepId: String(payload.step_id ?? ""),
    failureKind: String(payload.failure_kind ?? ""),
    retryable: Boolean(payload.retryable ?? false),
    exitCode: String(previousExecution.exit_code ?? ""),
    missingArtifacts: Array.isArray(payload.missing_artifacts)
      ? payload.missing_artifacts.map((item) => String(item)).filter(Boolean)
      : [],
    stdoutExcerpt: artifact.content_redacted ? "" : String(previousOutput.stdout_excerpt ?? ""),
    stderrExcerpt: artifact.content_redacted ? "" : String(previousOutput.stderr_excerpt ?? ""),
    mustProduceArtifacts: Array.isArray(retryInstructions.must_produce_artifacts)
      ? retryInstructions.must_produce_artifacts.map((item) => String(item)).filter(Boolean)
      : [],
    doNotRepeat: Array.isArray(retryInstructions.do_not_repeat)
      ? retryInstructions.do_not_repeat.map((item) => String(item)).filter(Boolean)
      : [],
  };
}

function rejectedOutputLabel(item: Record<string, unknown>): string {
  return String(
    item.output ??
      item.output_type ??
      item.path ??
      item.file_path ??
      item.card_id ??
      item.function_name ??
      "output",
  );
}

function rejectedOutputReason(item: Record<string, unknown>): string {
  const reason = String(item.reason ?? item.validation_error ?? "rejected");
  const path = item.path || item.file_path ? String(item.path ?? item.file_path) : "";
  const cardId = item.card_id ? String(item.card_id) : "";
  const status = item.output_status ? String(item.output_status) : "";
  const details = [
    path ? `path:${path}` : "",
    cardId ? `card:${cardId}` : "",
    status ? `status:${status}` : "",
  ].filter(Boolean);
  return details.length > 0 ? `${reason} (${details.join(" / ")})` : reason;
}

function evidenceAuditRefs(provenance: Record<string, unknown>): Array<{
  label: string;
  artifact: string;
  sha256: string;
}> {
  const refs: Array<{ key: string; label: string }> = [
    { key: "agent_replay_plan", label: "Replay" },
    { key: "agent_execution_input", label: "Input" },
    { key: "agent_execution_result", label: "Result" },
    { key: "workflow_outputs_artifact", label: "Output" },
    { key: "agent_output_contract", label: "Contract" },
  ];
  return refs
    .map(({ key, label }) => {
      const value = provenance[key];
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      const payload = value as Record<string, unknown>;
      const artifact = String(payload.artifact ?? "");
      if (!artifact) return null;
      return {
        label,
        artifact,
        sha256: String(payload.sha256 ?? ""),
      };
    })
    .filter((item): item is { label: string; artifact: string; sha256: string } =>
      Boolean(item),
    );
}

const AUDIT_ARTIFACT_KIND_ORDER = [
  "task_bundle",
  "input_snapshot",
  "input_context",
  "input_materials",
  "input_file_metadata",
  "input_file_set_manifest",
  "input_parsed_text",
  "input_chunks",
  "input_original_file",
  "input_artifact",
  "agent_task_bundle",
  "agent_output_contract",
  "agent_provider_diagnostics",
  "agent_replay_plan",
  "agent_run_lifecycle",
  "agent_failure_recovery",
  "agent_failure_retry_context",
  "agent_turn_task_bundle",
  "agent_turn_output_contract",
  "agent_turn_provider_diagnostics",
  "agent_turn_execution_input",
  "agent_turn_execution_result",
  "agent_turn_replay_plan",
  "agent_turn_source_slice_requests",
  "agent_turn_source_slices",
  "agent_turn_raw_output",
  "agent_turn_run",
  "agent_instructions",
  "provider_snapshot",
  "provider_readiness",
  "workflow_contract",
  "agent_mcp_requests",
  "context_discovery_decision",
  "context_bundle",
  "output_schemas",
  "memory_retrieval",
  "source_read_chain",
  "evidence_consumption_trajectory",
  "degraded_retrieval",
  "evidence_validation",
  "workflow_outputs",
  "semantic_import_outputs",
  "workflow_output_materialization",
  "workflow_execution",
  "task_acceptance_audit",
  "task_rerun_plan",
  "task_rerun_execution",
  "task_rerun_history",
];

function prioritizedAuditArtifacts(artifacts: WorkbenchTaskArtifact[]): WorkbenchTaskArtifact[] {
  return [...artifacts].sort((left, right) => {
    const leftOutputRank = workflowOutputArtifactRank(left.relative_path);
    const rightOutputRank = workflowOutputArtifactRank(right.relative_path);
    if (leftOutputRank !== rightOutputRank) {
      return leftOutputRank - rightOutputRank;
    }
    const leftRank = AUDIT_ARTIFACT_KIND_ORDER.indexOf(left.kind);
    const rightRank = AUDIT_ARTIFACT_KIND_ORDER.indexOf(right.kind);
    const normalizedLeftRank = leftRank === -1 ? AUDIT_ARTIFACT_KIND_ORDER.length : leftRank;
    const normalizedRightRank = rightRank === -1 ? AUDIT_ARTIFACT_KIND_ORDER.length : rightRank;
    if (normalizedLeftRank !== normalizedRightRank) {
      return normalizedLeftRank - normalizedRightRank;
    }
    return left.relative_path.localeCompare(right.relative_path);
  });
}

function workflowOutputArtifactRank(relativePath: string): number {
  const name = relativePath.split("/").pop() ?? relativePath;
  const order = [
    "risk_findings.json",
    "test_hooks.json",
    "source_scope.json",
    "evidence_cards.json",
    "black_box_cases.json",
    "impact_scope.json",
    "flow_delta.json",
    "test_recommendations.json",
    "workflow_output_materialization.json",
    "report.md",
  ];
  const rank = order.indexOf(name);
  return rank === -1 ? order.length : rank;
}

function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="ct-workbench-panel ct-reveal ct-liquid-glass min-w-0 rounded-[24px] p-5">
      <h2 className="ct-workbench-panel-title mb-4 flex items-center gap-2 text-base font-semibold text-on-surface">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

function ProviderFactRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="ct-provider-kv-row">
      <span className="ct-provider-kv-label">{label}</span>
      <span className="ct-provider-kv-value">{value}</span>
    </div>
  );
}

function ProviderSectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="ct-provider-section-title">{children}</p>;
}

export default function AgentWorkbenchPage() {
  const router = useRouter();
  const workbenchRootRef = useRef<HTMLDivElement | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [workflowPresets, setWorkflowPresets] = useState<WorkflowPreset[]>([]);
  const [workflowJson, setWorkflowJson] = useState(pretty(DEFAULT_WORKFLOW));
  const [builderScenario, setBuilderScenario] =
    useState<keyof typeof WORKFLOW_BUILDER_SCENARIOS>("mr_blackbox");
  const [builderWorkflowId, setBuilderWorkflowId] = useState("custom_mr_blackbox");
  const [builderWorkflowName, setBuilderWorkflowName] = useState("自定义 MR 黑盒测试工作流");
  const [builderInputSpec, setBuilderInputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.inputs,
  );
  const [builderOutputSpec, setBuilderOutputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.outputs,
  );
  const [builderProvider, setBuilderProvider] = useState("claude-code");
  const [builderMcpProfile, setBuilderMcpProfile] = useState("codehub-mcp");
  const [builderGoal, setBuilderGoal] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.goal,
  );
  const [builderArtifacts, setBuilderArtifacts] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.artifacts,
  );
  const [builderOutputSchemas, setBuilderOutputSchemas] = useState(
    pretty(DEFAULT_BUILDER_OUTPUT_SCHEMAS),
  );
  const [builderEvidenceMappings, setBuilderEvidenceMappings] = useState(
    pretty(DEFAULT_BUILDER_EVIDENCE_MAPPINGS),
  );
  const [builderSemanticImports, setBuilderSemanticImports] = useState(
    pretty(DEFAULT_BUILDER_SEMANTIC_IMPORTS),
  );
  const [builderInputSchemas, setBuilderInputSchemas] = useState(
    pretty(DEFAULT_BUILDER_INPUT_SCHEMAS),
  );
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(DEFAULT_WORKFLOW.id);
  const [workspaceId, setWorkspaceId] = useState("manual-workspace");
  const [repoPath, setRepoPath] = useState("");
  const [providerOverride, setProviderOverride] = useState("");
  const [inputsJson, setInputsJson] = useState(pretty(DEFAULT_INPUTS));
  const [semanticJson, setSemanticJson] = useState(pretty(DEFAULT_SEMANTIC_CASE));
  const [semanticFeature, setSemanticFeature] = useState("NVMe TCP TLS");
  const [semanticModule, setSemanticModule] = useState("nvmf_tcp");
  const [semanticLines, setSemanticLines] = useState(DEFAULT_SEMANTIC_LINES);
  const [semanticFile, setSemanticFile] = useState<File | null>(null);
  const [semanticQuery, setSemanticQuery] = useState("tls cleanup");
  const [semanticResults, setSemanticResults] = useState<SemanticCase[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("nvme tcp tls");
  const [manualEvidenceSubject, setManualEvidenceSubject] = useState("nvmf_tgt_accept");
  const [manualEvidencePath, setManualEvidencePath] = useState("lib/nvmf/nvmf.c");
  const [manualEvidenceText, setManualEvidenceText] = useState(
    "SPDK NVMe-oF target accept path evidence for connect-flow black-box validation.",
  );
  const [memoryResults, setMemoryResults] = useState<EvidenceMemoryItem[]>([]);
  const [memorySlices, setMemorySlices] = useState<Record<string, EvidenceSourceSlice[]>>({});
  const [providerMatrix, setProviderMatrix] =
    useState<WorkbenchProviderCapabilitiesMatrix | null>(null);
  const [systemAudit, setSystemAudit] = useState<WorkbenchSystemAudit | null>(null);
  const [providerProbeResults, setProviderProbeResults] = useState<
    Record<string, ExternalAgentStartupProbeResult>
  >({});
  const [providerTaskProbeResults, setProviderTaskProbeResults] = useState<
    Record<string, WorkbenchProviderTaskProbeResult>
  >({});
  const [deploymentProbeResult, setDeploymentProbeResult] =
    useState<WorkbenchDeploymentProbeResult | null>(null);
  const [smokeE2EResult, setSmokeE2EResult] =
    useState<WorkbenchSmokeE2EResult | null>(null);
  const [taskRuns, setTaskRuns] = useState<PreparedWorkbenchTaskRun[]>([]);
  const [preparedRun, setPreparedRun] = useState<PreparedWorkbenchTaskRun | null>(null);
  const [artifactManifest, setArtifactManifest] =
    useState<WorkbenchTaskArtifactManifest | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<WorkbenchTaskArtifactContent | null>(null);
  const [workflowExecution, setWorkflowExecution] = useState<WorkflowExecutionResult | null>(null);
  const [taskRerunPlan, setTaskRerunPlan] = useState<TaskRerunPlan | null>(null);
  const [taskRerunPlanValidation, setTaskRerunPlanValidation] =
    useState<TaskRerunPlanValidation | null>(null);
  const [taskRerunExecution, setTaskRerunExecution] =
    useState<TaskRerunExecutionResult | null>(null);
  const [taskRerunHistory, setTaskRerunHistory] = useState<TaskRerunHistory | null>(null);
  const [taskAcceptanceAudit, setTaskAcceptanceAudit] =
    useState<WorkbenchAcceptanceAudit | null>(null);
  const [workflowOutputMaterialize, setWorkflowOutputMaterialize] =
    useState<MaterializeWorkflowOutputsResult | null>(null);
  const [workflowDraftServerAudit, setWorkflowDraftServerAudit] =
    useState<WorkflowDraftServerAudit | null>(null);
  const [semanticOutputImport, setSemanticOutputImport] =
    useState<SemanticCaseImportResult | null>(null);
  const [executionResults, setExecutionResults] = useState<
    Record<string, AgentRunExecutionResult>
  >({});
  const [validationResults, setValidationResults] = useState<
    Record<string, ArtifactValidationResult>
  >({});
  const [materializeResults, setMaterializeResults] = useState<
    Record<string, MaterializeEvidenceResult>
  >({});
  const [activeWorkbenchView, setActiveWorkbenchView] = useState<WorkbenchView>("run");
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [openingConversation, setOpeningConversation] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useGSAP(
    () => {
      const root = workbenchRootRef.current;
      if (!root || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

      const handlePointerMove = (event: PointerEvent) => {
        const bounds = root.getBoundingClientRect();
        gsap.set(root, {
          "--ct-wb-x": `${event.clientX - bounds.left}px`,
          "--ct-wb-y": `${event.clientY - bounds.top}px`,
        });
      };

      root.addEventListener("pointermove", handlePointerMove, { passive: true });

      return () => root.removeEventListener("pointermove", handlePointerMove);
    },
    { scope: workbenchRootRef },
  );

  const workflowOptions = useMemo(
    () => workflows.map((workflow) => ({
      id: workflow.id,
      label: workflowDisplayName(workflow),
    })),
    [workflows],
  );
  const builderProviderOptions = useMemo(() => {
    const providers = (providerMatrix?.providers ?? [])
      .filter((provider) => provider.agent_owned || provider.command.length > 0)
      .map((provider) => ({
        id: provider.provider,
        label: provider.display_name || provider.provider,
        status: provider.status,
      }));
    if (!providers.some((provider) => provider.id === "claude-code")) {
      providers.unshift({ id: "claude-code", label: "Claude Code", status: "configured" });
    }
    return providers;
  }, [providerMatrix]);
  const semanticImportOutputIds = useMemo(
    () =>
      (workflowExecution?.outputs ?? [])
        .filter((output) => {
          const outputId = String(output.id ?? "").toLowerCase();
          const outputType = String(output.type ?? "").toLowerCase();
          const artifact = String(output.artifact ?? output.path ?? "").toLowerCase();
          return (
            output.status === "ok" &&
            (outputType === "test_cases" ||
              outputId === "black_box_cases" ||
              outputId === "test_cases" ||
              artifact.endsWith("black_box_cases.json") ||
              artifact.endsWith("test_cases.json"))
          );
        })
        .map((output) => String(output.id ?? "").trim())
        .filter(Boolean),
    [workflowExecution],
  );
  const selectedWorkflowInputs = useMemo(() => {
    const registered = workflows.find((workflow) => workflow.id === selectedWorkflowId);
    if (registered?.inputs?.length) return registered.inputs;
    return workflowInputsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflows]);
  const selectedWorkflowAudit = useMemo(
    () => workflows.find((workflow) => workflow.id === selectedWorkflowId)?.audit,
    [selectedWorkflowId, workflows],
  );
  const workflowDraftAuditSummary = useMemo(
    () => workflowDraftAudit(workflowJson),
    [workflowJson],
  );
  const parsedPrepareInputs = useMemo(() => {
    try {
      return parseJsonObject(inputsJson || "{}");
    } catch {
      return {};
    }
  }, [inputsJson]);
  const builderOutputPreview = useMemo(() => {
    try {
      const requiredArtifacts = parseCommaSeparated(builderArtifacts);
      const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
      const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
      const semanticImports = parseJsonObject(builderSemanticImports || "{}");
      return parseWorkflowSpecList(builderOutputSpec, "json").map((output) => {
        const artifact =
          output.artifact || outputArtifactForSpec(output.id, output.type, requiredArtifacts);
        const schema =
          output.type === "json" ? outputSchemaForSpec(output.id, outputSchemas) : null;
        const evidenceMemory =
          output.type === "json" || output.type === "scope_report"
            ? outputEvidenceMappingForSpec(output.id, evidenceMappings)
            : null;
        const semanticImport =
          output.type === "test_cases"
            ? outputSemanticImportForSpec(output.id, output.type, semanticImports)
            : null;
        return {
          id: output.id,
          type: output.type,
          artifact,
          schema: Boolean(schema),
          evidenceMemory: Boolean(evidenceMemory),
          evidenceKind: evidenceMemory ? String(evidenceMemory.kind ?? "") : "",
          semanticImport: Boolean(semanticImport),
        };
      });
    } catch {
      return [];
    }
  }, [
    builderArtifacts,
    builderEvidenceMappings,
    builderOutputSchemas,
    builderOutputSpec,
    builderSemanticImports,
  ]);
  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [
        workflowData,
        taskRunData,
        providerData,
        systemAuditData,
      ] = await Promise.all([
        api.workbench.workflows.list(),
        api.workbench.taskRuns.list({ limit: 10 }),
        api.workbench.providerCapabilities(),
        api.workbench.systemAudit(),
      ]);
      const presetData = await api.workbench.workflows.presets();
      setWorkflows(workflowData);
      setWorkflowPresets(presetData.items);
      setProviderMatrix(providerData);
      setSystemAudit(systemAuditData);
      if (!selectedPresetId && presetData.items.length > 0) {
        setSelectedPresetId(presetData.items[0].id);
      }
      setTaskRuns(taskRunData.items);
      if (
        workflowData.length > 0 &&
        !workflowData.some((item) => item.id === selectedWorkflowId)
      ) {
        setSelectedWorkflowId(workflowData[0].id);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load workbench data");
    } finally {
      setLoading(false);
    }
  }, [selectedPresetId, selectedWorkflowId]);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  async function runAction(name: string, action: () => Promise<void>) {
    setBusyAction(name);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusyAction(null);
    }
  }

  async function refreshArtifactManifest(taskRunId: string) {
    const manifest = await api.workbench.taskRuns.artifacts(taskRunId);
    setArtifactManifest(manifest);
  }

  async function restoreTaskRun(taskRunId: string) {
    const run = await api.workbench.taskRuns.get(taskRunId);
    const manifest = await api.workbench.taskRuns.artifacts(taskRunId);
    setPreparedRun(run);
    setArtifactManifest(manifest);
    setTaskRuns((current) => [
      run,
      ...current.filter((item) => item.task_run_id !== run.task_run_id),
    ].slice(0, 10));
    setExecutionResults({});
    setValidationResults({});
    setMaterializeResults({});
    setArtifactContent(null);
    setWorkflowOutputMaterialize(null);
    setSemanticOutputImport(null);
    setWorkflowExecution(null);
    setTaskRerunPlan(null);
    setTaskRerunPlanValidation(null);
    setTaskRerunExecution(null);
    setTaskRerunHistory(null);
    setTaskAcceptanceAudit(null);

    const artifactPaths = new Set(manifest.artifacts.map((item) => item.relative_path));
    if (artifactPaths.has("workflow_execution.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_execution.json",
      );
      const parsed = JSON.parse(content.content || "{}") as WorkflowExecutionResult;
      setWorkflowExecution(parsed);
      setTaskRerunPlan((parsed.rerun_plan as TaskRerunPlan | undefined) ?? null);
    }
    if (artifactPaths.has("workflow_output_materialization.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_output_materialization.json",
      );
      const parsed = JSON.parse(content.content || "{}") as MaterializeWorkflowOutputsResult;
      setWorkflowOutputMaterialize(parsed);
    }
    if (artifactPaths.has("semantic_output_import.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "semantic_output_import.json",
      );
      const parsed = JSON.parse(content.content || "{}") as {
        result?: SemanticCaseImportResult;
      };
      setSemanticOutputImport(parsed.result ?? null);
    }
    if (artifactPaths.has("task_rerun_plan.json")) {
      const [plan, validation, history] = await Promise.all([
        api.workbench.taskRuns.rerunPlan(taskRunId),
        api.workbench.taskRuns.rerunPlanValidation(taskRunId),
        api.workbench.taskRuns.rerunHistory(taskRunId),
      ]);
      setTaskRerunPlan(plan);
      setTaskRerunPlanValidation(validation);
      setTaskRerunHistory(history);
    }
    if (artifactPaths.has("task_acceptance_audit.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "task_acceptance_audit.json",
      );
      const parsed = JSON.parse(content.content || "{}") as WorkbenchAcceptanceAudit;
      setTaskAcceptanceAudit(parsed);
    }
  }

  function applyBuilderScenario(scenarioId: keyof typeof WORKFLOW_BUILDER_SCENARIOS) {
    const scenario = WORKFLOW_BUILDER_SCENARIOS[scenarioId];
    setBuilderScenario(scenarioId);
    setBuilderWorkflowName(`自定义 ${scenario.name}`);
    setBuilderInputSpec(scenario.inputs);
    setBuilderOutputSpec(scenario.outputs);
    setBuilderGoal(scenario.goal);
    setBuilderArtifacts(scenario.artifacts);
    setBuilderInputSchemas(pretty(DEFAULT_BUILDER_INPUT_SCHEMAS));
    setBuilderEvidenceMappings(pretty(DEFAULT_BUILDER_EVIDENCE_MAPPINGS));
    setBuilderSemanticImports(pretty(DEFAULT_BUILDER_SEMANTIC_IMPORTS));
  }

  function generateWorkflowFromBuilder() {
    const workflowId = builderWorkflowId.trim();
    const workflowName = builderWorkflowName.trim();
    if (!workflowId || !workflowName) {
      throw new Error("Workflow builder requires workflow id and name");
    }
    const inputSchemas = parseJsonObject(builderInputSchemas || "{}");
    const inputs = parseWorkflowSpecList(builderInputSpec, "free_text").map((input) => {
      const schema = inputSchemaForSpec(input.id, input.type, inputSchemas);
      return {
        id: input.id,
        type: input.type,
        required: input.type !== "file" && input.type !== "file_set",
        resolver:
          input.resolver ||
          (input.type === "mr_link" || input.type === "external_link"
            ? "agent_mcp"
            : "manual"),
        role:
          input.resolver === "agent_mcp" || input.type === "mr_link"
            ? "由智能体 CLI 通过 MCP 凭证解析远端变更源"
            : "用户提供的工作流输入",
        ...(schema ? { schema } : {}),
      };
    });
    const requiredArtifacts = parseCommaSeparated(builderArtifacts);
    const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
    const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
    const semanticImports = parseJsonObject(builderSemanticImports || "{}");
    const outputs = parseWorkflowSpecList(builderOutputSpec, "json").map((output) => {
      const artifact =
        output.artifact || outputArtifactForSpec(output.id, output.type, requiredArtifacts);
      const from = artifact ? "agent_collect" : "render_report";
      const schema = output.type === "json" ? outputSchemaForSpec(output.id, outputSchemas) : null;
      const evidenceMemory =
        output.type === "json" || output.type === "scope_report"
          ? outputEvidenceMappingForSpec(output.id, evidenceMappings)
          : null;
      const semanticImport =
        output.type === "test_cases"
          ? outputSemanticImportForSpec(output.id, output.type, semanticImports)
          : null;
      return {
        id: output.id,
        type: output.type,
        from,
        ...(artifact ? { artifact } : {}),
        ...(schema ? { schema } : {}),
        ...(evidenceMemory ? { evidence_memory: evidenceMemory } : {}),
        ...(semanticImport ? { semantic_import: semanticImport } : {}),
      };
    });
    const workflow = {
      id: workflowId,
      name: workflowName,
      version: 1,
      inputs,
      steps: [
        {
          id: "agent_collect",
          type: "agent_task",
          provider: builderProvider.trim() || "claude-code",
          mcp_profile: builderMcpProfile.trim(),
          goal: builderGoal.trim(),
          required_artifacts: requiredArtifacts,
        },
        { id: "validate_evidence", type: "evidence_validate" },
        { id: "semantic_retrieve", type: "semantic_retrieve" },
        { id: "render_report", type: "report_render" },
      ],
      outputs,
    };
    setWorkflowJson(pretty(workflow));
    setSelectedWorkflowId(workflow.id);
    setMessage(`工作流草稿已生成: ${workflow.id}`);
  }

  const generateWorkflowDraft = () =>
    runAction("generate-workflow", async () => {
      generateWorkflowFromBuilder();
    });

  const saveWorkflow = () =>
    runAction("save-workflow", async () => {
      const payload = parseJsonObject(workflowJson);
      const saved = await api.workbench.workflows.create(payload);
      setSelectedWorkflowId(saved.id);
      const warningCount = saved.audit?.warnings?.length ?? 0;
      setMessage(
        warningCount
          ? `工作流已保存: ${saved.id} (${warningCount} audit warning(s))`
          : `工作流已保存: ${saved.id}`,
      );
      await loadWorkflows();
    });

  const auditWorkflowDraft = () =>
    runAction("audit-workflow-draft", async () => {
      const payload = parseJsonObject(workflowJson);
      const audit = await api.workbench.workflows.auditDraft(payload);
      setWorkflowDraftServerAudit(audit);
      setMessage(
        audit.valid
          ? `工作流草稿审计: ${audit.status} (${audit.warnings.length} warning(s))`
          : `工作流草稿审计: invalid`,
      );
    });

  const loadSelectedWorkflowDraft = () => {
    const workflow = workflows.find((item) => item.id === selectedWorkflowId);
    if (!workflow) return;
    setWorkflowJson(pretty(workflow));
    setMessage(`已载入工作流: ${workflow.id}`);
  };

  const duplicateSelectedWorkflowDraft = () => {
    const workflow = workflows.find((item) => item.id === selectedWorkflowId);
    if (!workflow) return;
    const clone = {
      ...workflow,
      id: `${workflow.id}_copy`,
      name: `${workflowDisplayName(workflow)} 副本`,
      version: Number(workflow.version ?? 1) + 1,
    };
    setWorkflowJson(pretty(clone));
    setSelectedWorkflowId(clone.id);
    setMessage(`已复制为草稿: ${clone.id}`);
  };

  const applyPreset = () => {
    const preset = workflowPresets.find((item) => item.id === selectedPresetId);
    if (!preset) return;
    setWorkflowJson(pretty(preset.definition));
    setSelectedWorkflowId(preset.definition.id);
    setMessage(`已应用预设: ${workflowDisplayName(preset.definition)}`);
  };

  const installPreset = () =>
    runAction("install-preset", async () => {
      if (!selectedPresetId) return;
      const workflow = await api.workbench.workflows.installPreset(selectedPresetId);
      setWorkflowJson(pretty(workflow));
      setSelectedWorkflowId(workflow.id);
      setMessage(`预设已安装: ${workflowDisplayName(workflow)}`);
      await loadWorkflows();
    });

  const prepareTaskRun = () =>
    runAction("prepare-task-run", async () => {
      const inputs = parseJsonObject(inputsJson);
      const result = await api.workbench.taskRuns.prepare({
        workflow_id: selectedWorkflowId,
        workspace_id: workspaceId,
        repo_path: repoPath,
        inputs,
        provider_override: providerOverride.trim() || null,
      });
      setPreparedRun(result);
      setTaskRuns((current) => [
        result,
        ...current.filter((item) => item.task_run_id !== result.task_run_id),
      ].slice(0, 10));
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setWorkflowExecution(null);
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setTaskAcceptanceAudit(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`Task run prepared: ${result.task_run_id}`);
    });

  const createAndRunTaskRun = () =>
    runAction("create-and-run-task-run", async () => {
      const inputs = parseJsonObject(inputsJson);
      const result = await api.workbench.taskRuns.run(
        {
          workflow_id: selectedWorkflowId,
          workspace_id: workspaceId,
          repo_path: repoPath,
          inputs,
          provider_override: providerOverride.trim() || null,
        },
        90,
        true,
      );
      setPreparedRun(result.task_run);
      setTaskRuns((current) => [
        result.task_run,
        ...current.filter((item) => item.task_run_id !== result.task_run_id),
      ].slice(0, 10));
      setWorkflowExecution(result.execution);
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      setTaskRerunPlan((result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null);
      setTaskRerunPlanValidation(
        await api.workbench.taskRuns.rerunPlanValidation(result.task_run_id),
      );
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      await loadWorkflows();
      setMessage(
        `Task run ${result.status}: ${result.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
    });

  const restoreExistingTaskRun = (taskRunId: string) =>
    runAction(`restore-task-run-${taskRunId}`, async () => {
      await restoreTaskRun(taskRunId);
      setMessage(`Task run restored: ${taskRunId}`);
    });

  const runProviderStartupProbe = (provider: string) =>
    runAction(`provider-probe-${provider}`, async () => {
      const result = await api.tools.startupProbe(provider, repoPath.trim() || undefined);
      setProviderProbeResults((current) => ({ ...current, [provider]: result }));
      setMessage(`启动探测 ${result.status}: ${provider}`);
    });

  const runProviderTaskProbe = (provider: string) =>
    runAction(`provider-task-probe-${provider}`, async () => {
      const result = await api.workbench.providerTaskProbe(
        provider,
        repoPath.trim() || undefined,
        30,
      );
      setProviderTaskProbeResults((current) => ({ ...current, [provider]: result }));
      setPreparedRun(result.task_run);
      setTaskRuns((current) => [
        result.task_run,
        ...current.filter((item) => item.task_run_id !== result.task_run_id),
      ].slice(0, 10));
      setWorkflowExecution(result.execution);
      setTaskAcceptanceAudit(result.acceptance_audit);
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(
        `任务探测 ${result.status}: ${provider} contract ${result.summary.task_contract_status}`,
      );
    });

  const runAllAgentProviderStartupProbes = () =>
    runAction("provider-probe-all-agents", async () => {
      const providers = (providerMatrix?.providers ?? []).filter(
        (provider) => provider.agent_owned && provider.diagnostics?.startup_probe_endpoint,
      );
      const result = await api.workbench.deploymentProbe(
        repoPath.trim() || undefined,
        providers.map((provider) => provider.provider),
      );
      setDeploymentProbeResult(result);
      setProviderProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider) {
            next[provider] = item;
          }
        }
        return next;
      });
      setMessage(
        `部署探测 ${result.status}: ${result.summary.healthy_count}/${result.summary.provider_count} healthy`,
      );
    });

  const runAllAgentProviderTaskProbes = () =>
    runAction("provider-task-probe-all-agents", async () => {
      const providers = (providerMatrix?.providers ?? []).filter(
        (provider) => provider.agent_owned && provider.command.length > 0,
      );
      const result = await api.workbench.deploymentProbe(
        repoPath.trim() || undefined,
        providers.map((provider) => provider.provider),
        true,
        30,
      );
      setDeploymentProbeResult(result);
      setProviderProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider) {
            next[provider] = item;
          }
        }
        return next;
      });
      setProviderTaskProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider && item.task_probe) {
            next[provider] = item.task_probe;
          }
        }
        return next;
      });
      const ready = result.summary.task_ready_count ?? 0;
      const total = result.summary.provider_count;
      setMessage(`任务探测 deployment ${result.status}: ${ready}/${total} ready`);
    });

  const runSmokeE2E = () =>
    runAction("smoke-e2e", async () => {
      const result = await api.workbench.smokeE2E(repoPath.trim() || undefined, 30);
      setSmokeE2EResult(result);
      setPreparedRun(result.task_run);
      setTaskRuns((current) => [
        result.task_run,
        ...current.filter((item) => item.task_run_id !== result.task_run_id),
      ].slice(0, 10));
      setWorkflowExecution(result.execution);
      setTaskAcceptanceAudit(result.acceptance_audit);
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`全链路烟测 ${result.status}: ${result.task_run_id}`);
    });

  function updatePrepareInput(input: Record<string, unknown>, value: string) {
    setInputsJson((current) => updateInputsJsonValue(current, input, value));
  }

  const uploadPrepareInputFile = (
    input: Record<string, unknown>,
    files: FileList | null,
  ) =>
    runAction(`upload-input-${String(input.id ?? "input")}`, async () => {
      if (!files || files.length === 0) return;
      const inputId = String(input.id ?? "");
      const inputType = String(input.type ?? "");
      const uploads = await Promise.all(
        Array.from(files).map((file) => api.workbench.uploadInputFile(file, inputId)),
      );
      const paths = uploads.map((item) => item.path).filter(Boolean);
      if (inputType === "file_set") {
        setInputsJson((current) => {
          const existing = inputTextValue(parseJsonObject(current || "{}"), input)
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean);
          return updateInputsJsonValue(current, input, [...existing, ...paths].join("\n"));
        });
      } else if (paths[0]) {
        updatePrepareInput(input, paths[0]);
      }
      setMessage(`Input file uploaded: ${uploads.map((item) => item.filename).join(", ")}`);
    });

  const openPreparedConversation = async () => {
    if (!preparedRun) return;
    setOpeningConversation(true);
    try {
      const conversation = await api.aiConversations.createForScope({
        scope_type: "workbench_task_run",
        scope_id: preparedRun.task_run_id,
        workspace_id: preparedRun.workspace_id,
        memory_namespace: `workspace:${preparedRun.workspace_id}`,
        title: `${workflowDisplayName(preparedRun.workflow_id)} · AI 复盘`,
        initial_context: {
          workflow_id: preparedRun.workflow_id,
          workspace_id: preparedRun.workspace_id,
          memory_namespace: `workspace:${preparedRun.workspace_id}`,
          repo_path: preparedRun.repo_path,
        },
      });
      router.push(`/ai/${conversation.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "创建 AI 线程失败");
    } finally {
      setOpeningConversation(false);
    }
  };

  const loadPreparedArtifacts = () =>
    runAction("load-artifacts", async () => {
      if (!preparedRun) return;
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`产物已加载: ${preparedRun.task_run_id}`);
    });

  const loadTaskRerunPlan = () =>
    runAction("load-rerun-plan", async () => {
      if (!preparedRun) return;
      const [result, validation] = await Promise.all([
        api.workbench.taskRuns.rerunPlan(preparedRun.task_run_id),
        api.workbench.taskRuns.rerunPlanValidation(preparedRun.task_run_id),
      ]);
      const history = await api.workbench.taskRuns.rerunHistory(preparedRun.task_run_id);
      setTaskRerunPlan(result);
      setTaskRerunPlanValidation(validation);
      setTaskRerunHistory(history);
      setMessage(`Rerun plan ${result.status}: ${result.task_run_id}`);
    });

  const generateTaskAcceptanceAudit = () =>
    runAction("acceptance-audit", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.acceptanceAudit(
        preparedRun.task_run_id,
      );
      setTaskAcceptanceAudit(result);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Acceptance audit ${result.status}: ${result.summary.missing_required} missing required`,
      );
    });

  const executeTaskRerunPlan = () =>
    runAction("execute-rerun-plan", async () => {
      if (!preparedRun || !taskRerunPlanValidation?.can_rerun) return;
      const result = await api.workbench.taskRuns.executeRerunPlan(
        preparedRun.task_run_id,
        90,
        true,
      );
      setTaskRerunExecution(result);
      if (result.execution) {
        setWorkflowExecution({
          ...result.execution,
          evidence_materialization:
            result.evidence_materialization ?? result.execution.evidence_materialization,
          semantic_output_import:
            result.semantic_output_import ?? result.execution.semantic_output_import,
          acceptance_audit: result.acceptance_audit ?? result.execution.acceptance_audit,
        });
        setTaskRerunPlan((result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null);
      }
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlanValidation(result.validation_after ?? null);
      setTaskRerunHistory(await api.workbench.taskRuns.rerunHistory(preparedRun.task_run_id));
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Rerun execution ${result.execution?.status ?? result.status}: ${preparedRun.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
    });

  const previewArtifact = (relativePath: string) =>
    runAction(`preview-artifact-${relativePath}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.artifactContent(
        preparedRun.task_run_id,
        relativePath,
      );
      setArtifactContent(result);
      setMessage(`Artifact preview loaded: ${relativePath}`);
    });

  const executePreparedWorkflow = () =>
    runAction("execute-workflow", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.execute(
        preparedRun.task_run_id,
        90,
        true,
      );
      setWorkflowExecution(result);
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlan((result.rerun_plan as TaskRerunPlan | undefined) ?? null);
      setTaskRerunPlanValidation(
        await api.workbench.taskRuns.rerunPlanValidation(preparedRun.task_run_id),
      );
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Workflow execution ${result.status}: ${result.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
      await loadWorkflows();
    });

  const materializePreparedWorkflowOutputs = () =>
    runAction("materialize-workflow-outputs", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.materializeOutputs(
        preparedRun.task_run_id,
      );
      setWorkflowOutputMaterialize(result);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Workflow outputs materialized: ${result.evidence_count}; semantics ${result.semantic_output_import?.status ?? "skipped"}`,
      );
    });

  const importPreparedSemanticOutputs = () =>
    runAction("import-semantic-outputs", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.importSemanticOutputs(
        preparedRun.task_run_id,
        { output_ids: semanticImportOutputIds },
      );
      setSemanticOutputImport(result);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Semantic outputs imported: ${result.imported_count}, rejected: ${result.rejected_count}`,
      );
    });

  const executePreparedAgentRun = (stepId: string) =>
    runAction(`execute-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.executeAgentRun(
        preparedRun.task_run_id,
        stepId,
        90,
      );
      setExecutionResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`Agent run ${result.status}: ${result.run_id}`);
    });

  const validatePreparedAgentRun = (stepId: string, requiredArtifacts: string[]) =>
    runAction(`validate-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.validateMrArtifacts(
        preparedRun.task_run_id,
        stepId,
        requiredArtifacts,
      );
      setValidationResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`Artifact validation ${result.status}: ${stepId}`);
    });

  const materializePreparedAgentRun = (stepId: string, requiredArtifacts: string[]) =>
    runAction(`materialize-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.materializeEvidence(
        preparedRun.task_run_id,
        stepId,
        requiredArtifacts,
        `${preparedRun.workflow_id} ${preparedRun.task_run_id}`,
      );
      setMaterializeResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`证据已固化: ${result.evidence_count}`);
    });

  const importSemanticCase = () =>
    runAction("import-semantic-case", async () => {
      const payload = parseJsonValue(semanticJson);
      if (isBulkSemanticImportPayload(payload)) {
        const result = await api.workbench.semanticCases.importMany(payload);
        setMessage(
          `语义用例已导入: ${result.imported_count}, rejected: ${result.rejected_count}`,
        );
        return;
      }
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error("Semantic import JSON must be an object or array");
      }
      const result = await api.workbench.semanticCases.create(
        payload as Record<string, unknown>,
      );
      setMessage(`语义用例已保存: ${result.case_id}`);
    });

  const buildSemanticCasesFromText = () =>
    runAction("build-semantic-cases", async () => {
      const payload = semanticCasesFromLines({
        feature: semanticFeature,
        module: semanticModule,
        text: semanticLines,
      });
      setSemanticJson(pretty(payload));
      const count = Array.isArray(payload.cases) ? payload.cases.length : 0;
      setMessage(`语义导入草稿已生成: ${count} cases`);
    });

  const searchSemanticCases = () =>
    runAction("search-semantic-cases", async () => {
      const result = await api.workbench.semanticCases.search({
        q: semanticQuery,
        limit: 10,
      });
      setSemanticResults(result.items);
      setMessage(`语义搜索结果: ${result.items.length}`);
    });

  const importSemanticCaseFile = () =>
    runAction("import-semantic-file", async () => {
      if (!semanticFile) {
        throw new Error("Select a semantic case file first");
      }
      const result = await api.workbench.semanticCases.importFile(semanticFile, {
        feature: semanticFeature,
        module: semanticModule,
        test_level: "black_box",
      });
      setMessage(
        `语义文件已导入: ${result.imported_count}, rejected: ${result.rejected_count}`,
      );
      setSemanticFile(null);
    });

  const saveManualEvidence = () =>
    runAction("save-manual-evidence", async () => {
      const subject = manualEvidenceSubject.trim();
      if (!subject) {
        throw new Error("Evidence subject is required");
      }
      const run = await api.workbench.memory.createRun({
        workspace_id: workspaceId,
        repo_path: repoPath,
        object_text: subject,
        workflow_id: "manual_evidence_entry",
        status: "completed",
      });
      const result = await api.workbench.memory.createEvidence({
        run_id: run.run_id,
        workspace_id: workspaceId,
        kind: "manual_source_evidence",
        subject_key: subject,
        status: "accepted",
        source: "workbench_manual_entry",
        path: manualEvidencePath.trim(),
        reason: manualEvidenceText.trim(),
        text: manualEvidenceText.trim(),
        confidence: 1,
        provenance: {
          repo_path: repoPath,
          line_start: 1,
          entry_method: "workbench_manual_evidence_form",
        },
      });
      setMemoryQuery(subject);
      setMessage(
        `证据已保存: ${result.evidence_id}; source slices ${result.source_slice_count ?? 0}`,
      );
    });

  const searchMemory = () =>
    runAction("search-memory", async () => {
      const result = await api.workbench.memory.search({
        q: memoryQuery,
        limit: 10,
      });
      setMemoryResults(result.items);
      setMemorySlices({});
      setMessage(`证据搜索结果: ${result.items.length}`);
    });

  const loadMemorySlices = (evidenceId: string) =>
    runAction(`memory-slices-${evidenceId}`, async () => {
      const result = await api.workbench.memory.sourceSlices(evidenceId);
      setMemorySlices((current) => ({ ...current, [evidenceId]: result.items }));
      setMessage(`源码切片已加载: ${result.items.length}`);
    });

  return (
    <div ref={workbenchRootRef} className="ct-workbench-shell w-full px-4 xl:px-6">
      <div className="ct-workbench-hero ct-liquid-glass mb-6 overflow-hidden rounded-[28px] p-5 sm:p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-4xl">
            <p className="mb-2 font-data text-xs uppercase tracking-[0.16em] text-primary">
              外部智能体编排控制台
            </p>
            <h1 className="font-display text-3xl font-bold text-on-surface">
              智能体编排台
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">
              它不是一个普通页面，而是给黑盒测试人员用的外部智能体编排台：把 Claude Code、OpenCode
              或自定义 CLI 当成只读执行器，读取 MR、补丁、设计文档和覆盖率报告，产出可审计证据、黑盒用例和可复跑产物。
            </p>
          </div>
          <button
            onClick={() => void loadWorkflows()}
            disabled={loading}
            className="ct-liquid-button inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-primary disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            刷新状态
          </button>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div className="ct-workbench-stat rounded-xl border border-outline-variant/25 bg-surface-container/75 p-4">
            <p className="text-xs text-on-surface-variant">系统门禁</p>
            <p className="mt-2 font-display text-2xl font-semibold text-on-surface">
              {systemAudit?.status ?? "待检查"}
            </p>
          </div>
          <div className="ct-workbench-stat rounded-xl border border-outline-variant/25 bg-surface-container/75 p-4">
            <p className="text-xs text-on-surface-variant">工作流</p>
            <p className="mt-2 font-display text-2xl font-semibold text-on-surface">
              {workflows.length}
            </p>
          </div>
          <div className="ct-workbench-stat rounded-xl border border-outline-variant/25 bg-surface-container/75 p-4">
            <p className="text-xs text-on-surface-variant">外部智能体</p>
            <p className="mt-2 font-display text-2xl font-semibold text-on-surface">
              {providerMatrix?.providers.length ?? 0}
            </p>
          </div>
          <div className="ct-workbench-stat rounded-xl border border-outline-variant/25 bg-surface-container/75 p-4">
            <p className="text-xs text-on-surface-variant">最近任务</p>
            <p className="mt-2 font-display text-2xl font-semibold text-on-surface">
              {taskRuns.length}
            </p>
          </div>
        </div>
      </div>

      {(error || message) && (
        <div
          className={`mb-5 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-red-500/20 bg-red-500/10 text-red-400"
              : "border-green-500/20 bg-green-500/10 text-green-400"
          }`}
        >
          {error ?? message}
        </div>
      )}

      <div className="ct-workbench-switcher mb-5 grid gap-2 lg:grid-cols-4">
        {WORKBENCH_VIEWS.map((view) => {
          const selected = activeWorkbenchView === view.id;
          const badge =
            view.id === "run"
              ? preparedRun
                ? "已准备"
                : `${taskRuns.length} 任务`
              : view.id === "workflow"
                ? `${workflows.length} 工作流`
                : view.id === "knowledge"
                  ? `${semanticResults.length + memoryResults.length} 结果`
                  : `${providerMatrix?.providers.length ?? 0} 执行器`;
          return (
            <button
              key={view.id}
              type="button"
              onClick={() => setActiveWorkbenchView(view.id)}
              className={`ct-workbench-tab min-w-0 rounded-2xl border px-4 py-3 text-left transition-all ${
                selected
                  ? "is-active border-primary/35 bg-primary text-on-primary"
                  : "border-outline-variant/40 bg-surface-container/82 text-on-surface hover:border-primary/25 hover:bg-surface-container-high"
              }`}
              aria-pressed={selected}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2">
                  {view.id === "run" ? (
                    <PlayCircle size={16} />
                  ) : view.id === "workflow" ? (
                    <ClipboardList size={16} />
                  ) : view.id === "knowledge" ? (
                    <Library size={16} />
                  ) : (
                    <AlertTriangle size={16} />
                  )}
                  <span className="truncate text-sm font-semibold">{view.label}</span>
                </span>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 font-data text-[10px] ${
                    selected ? "bg-white/18 text-on-primary" : "bg-surface text-on-surface-variant"
                  }`}
                >
                  {badge}
                </span>
              </span>
              <span
                className={`mt-1 block truncate text-xs ${
                  selected ? "text-white/78" : "text-on-surface-variant"
                }`}
              >
                {view.description}
              </span>
            </button>
          );
        })}
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={activeWorkbenchView}
          initial={{ opacity: 0, y: 18, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -10, scale: 0.99 }}
          transition={{ duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
          className={`ct-workbench-stage grid grid-cols-1 gap-5 ${
            activeWorkbenchView === "knowledge" ? "2xl:grid-cols-2" : ""
          }`}
        >
      {activeWorkbenchView === "diagnostics" && (
      <Panel title="执行器矩阵" icon={<AlertTriangle size={16} />}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-on-surface-variant">
            这里检查本机后端能否调用外部智能体 CLI，以及这些执行器是否具备 MCP 凭证、产物导出和任务探测能力。
          </p>
          <button
            onClick={() => runAllAgentProviderStartupProbes()}
            disabled={
              busyAction === "provider-probe-all-agents" ||
              !(providerMatrix?.providers ?? []).some(
                (provider) => provider.agent_owned && provider.diagnostics?.startup_probe_endpoint,
              )
            }
            className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
          >
            {busyAction === "provider-probe-all-agents" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <PlayCircle size={13} />
            )}
            探测全部 Agent
          </button>
          <button
            onClick={() => runAllAgentProviderTaskProbes()}
            disabled={
              busyAction === "provider-task-probe-all-agents" ||
              !(providerMatrix?.providers ?? []).some(
                (provider) => provider.agent_owned && provider.command.length > 0,
              )
            }
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busyAction === "provider-task-probe-all-agents" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <PlayCircle size={13} />
            )}
            任务探测
          </button>
          <button
            onClick={runSmokeE2E}
            disabled={busyAction === "smoke-e2e"}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busyAction === "smoke-e2e" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <PlayCircle size={13} />
            )}
            全链路烟测
          </button>
        </div>
        {smokeE2EResult && (
          <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-on-surface">全链路烟测</span>
              <span
                className={
                  smokeE2EResult.status === "ready"
                    ? "font-data text-green-500"
                    : "font-data text-warning"
                }
              >
                {smokeE2EResult.status}
              </span>
              <span className="font-data">
                task:{smokeE2EResult.task_run_id}
              </span>
              <span className="font-data">
                execution:{smokeE2EResult.execution.status}
              </span>
              <span className="font-data">
                missing:{smokeE2EResult.acceptance_audit.summary.missing_required}
              </span>
            </div>
            <p className="mt-1 break-words font-data text-[10px]">
              artifact:{smokeE2EResult.artifact.path}
            </p>
          </div>
        )}
        {deploymentProbeResult && (
          <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-on-surface">部署探测</span>
              <span
                className={
                  deploymentProbeResult.status === "healthy"
                    ? "font-data text-green-500"
                    : "font-data text-warning"
                }
              >
                {deploymentProbeResult.status}
              </span>
              <span className="font-data">
                healthy:{deploymentProbeResult.summary.healthy_count}/
                {deploymentProbeResult.summary.provider_count}
              </span>
              <span className="font-data">
                failed:{deploymentProbeResult.summary.failed_count}
              </span>
              {deploymentProbeResult.summary.task_contract_probe && (
                <span className="font-data">
                  task-ready:{deploymentProbeResult.summary.task_ready_count ?? 0}/
                  {deploymentProbeResult.summary.provider_count}
                </span>
              )}
              {typeof deploymentProbeResult.evidence_count === "number" && (
                <span className="font-data">
                  evidence:{deploymentProbeResult.evidence_count}
                </span>
              )}
              <span className="font-data">
                probe:{deploymentProbeResult.probe_id}
              </span>
            </div>
            <p className="mt-1 break-words font-data text-[10px]">
              artifact:{deploymentProbeResult.artifact.latest_path || deploymentProbeResult.artifact.path}
            </p>
            {deploymentProbeResult.evidence_ids?.length ? (
              <p className="mt-1 break-words font-data text-[10px]">
                evidence_ids:{deploymentProbeResult.evidence_ids.join(", ")}
              </p>
            ) : null}
          </div>
        )}
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
          {(providerMatrix?.providers ?? []).map((provider) => (
            <div
              key={provider.provider}
              className="ct-provider-card min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs"
            >
              <div className="ct-provider-card-header flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <p className="ct-provider-name truncate text-sm font-semibold text-on-surface">
                    {provider.display_name || provider.provider}
                  </p>
                  <p className="ct-provider-slug font-data text-[11px] text-on-surface-variant">
                    {provider.provider}
                  </p>
                </div>
                <span className="ct-provider-status-badge shrink-0 rounded bg-surface-container px-2 py-0.5 font-data text-[10px] text-on-surface-variant">
                  {provider.status}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {provider.codetalk_callable && (
                  <span className="ct-provider-pill ct-provider-pill--green rounded bg-green-400/10 px-2 py-0.5 text-[11px] font-medium text-green-500">
                    CodeTalk 可直接调用
                  </span>
                )}
                {provider.agent_owned && (
                  <span className="ct-provider-pill ct-provider-pill--dark rounded bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                    Agent 持有凭证
                  </span>
                )}
                {!provider.codetalk_callable && !provider.agent_owned && (
                  <span className="ct-provider-pill ct-provider-pill--amber rounded bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-500">
                    委托或不可用
                  </span>
                )}
              </div>
              <div className="ct-provider-facts mt-3">
                <ProviderFactRow
                  label="归属"
                  value={<span className="font-data">{provider.owner}</span>}
                />
                <ProviderFactRow
                  label="命令"
                  value={
                    <span className="font-data">
                      {provider.command.length > 0 ? provider.command.join(" ") : "n/a"}
                    </span>
                  }
                />
                <ProviderFactRow
                  label="MCP"
                  value={
                    <span className="font-data">
                      {provider.capabilities.supports_mcp
                        ? provider.capabilities.mcp_profiles.length > 0
                          ? provider.capabilities.mcp_profiles.join(", ")
                          : "yes"
                        : "no"}
                    </span>
                  }
                />
                <ProviderFactRow
                  label="产物"
                  value={
                    <span className="font-data">
                      {provider.capabilities.supports_artifact_export ? "artifact" : "no-artifact"}
                    </span>
                  }
                />
                <ProviderFactRow
                  label="JSON"
                  value={
                    <span className="font-data">
                      {provider.capabilities.supports_json_output ? "json" : "no-json"}
                    </span>
                  }
                />
                {provider.env_hint_keys?.length ? (
                  <ProviderFactRow
                    label="环境变量"
                    value={<span className="font-data">{provider.env_hint_keys.join(", ")}</span>}
                  />
                ) : null}
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {provider.capabilities.supports_source_discovery && (
                  <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    源码发现
                  </span>
                )}
                {provider.capabilities.supports_call_graph && (
                  <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    调用图
                  </span>
                )}
                {provider.capabilities.supports_source_slices && (
                  <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    源码切片
                  </span>
                )}
                {provider.capabilities.supports_black_box_terms && (
                  <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    黑盒术语
                  </span>
                )}
              </div>
              {provider.credential_boundary && (
                <p className="ct-provider-note mt-3 text-xs leading-5 text-on-surface-variant">
                  {provider.credential_boundary}
                </p>
              )}
              {provider.diagnostics && (
                <div className="ct-provider-diagnostics mt-3 space-y-2 border-t border-outline-variant/30 pt-3 text-on-surface-variant">
                  <ProviderSectionTitle>启动探测</ProviderSectionTitle>
                  {provider.diagnostics.startup_probe_endpoint && (
                    <ProviderFactRow
                      label="Probe"
                      value={
                        <span className="font-data">
                          {provider.diagnostics.startup_probe_endpoint}
                        </span>
                      }
                    />
                  )}
                  {provider.diagnostics.startup_probe_transport && (
                    <ProviderFactRow
                      label="传输"
                      value={
                        <span className="font-data">
                          {provider.diagnostics.startup_probe_transport}
                        </span>
                      }
                    />
                  )}
                  {provider.diagnostics.command_resolution && (
                    <div className="ct-provider-diag-box rounded bg-surface-container px-2 py-1.5">
                      <p className="ct-provider-diag-head">
                        <span>解析</span>
                        <span className="font-data">
                          {provider.diagnostics.command_resolution.status || "unknown"}
                        </span>
                        {provider.diagnostics.command_resolution.used_fallback && (
                          <span className="ct-provider-mini-badge font-medium text-warning">fallback</span>
                        )}
                        {provider.diagnostics.command_resolution.launch_kind && (
                          <span className="ct-provider-mini-badge font-data text-on-surface">
                            launch:{provider.diagnostics.command_resolution.launch_kind}
                          </span>
                        )}
                      </p>
                      {provider.diagnostics.command_resolution.reason && (
                        <p className="mt-1 break-words">
                          原因: {provider.diagnostics.command_resolution.reason}
                        </p>
                      )}
                      {typeof provider.diagnostics.command_resolution.attempt_count ===
                        "number" && (
                        <p className="mt-1">
                          尝试次数:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.command_resolution.attempt_count}
                          </span>
                        </p>
                      )}
                      {(() => {
                        const attempts = provider.diagnostics.command_resolution?.attempts ?? [];
                        const lastAttempt = attempts[attempts.length - 1];
                        const resolutionLines = commandResolutionLines(lastAttempt?.resolution);
                        if (resolutionLines.length === 0) return null;
                        return (
                          <div className="mt-2 space-y-1">
                            {resolutionLines.map((line) => (
                              <p key={line} className="break-words font-data text-[11px] text-on-surface">
                                {line}
                              </p>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                  {provider.diagnostics.probe_recipe && (
                    <div className="rounded bg-surface-container px-2 py-1.5">
                      <p className="font-medium text-on-surface">探测配方</p>
                      {provider.diagnostics.probe_recipe.startup_probe_http && (
                        <p className="mt-1 break-words">
                          HTTP:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.startup_probe_http}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.probe_recipe.backend_command && (
                        <p className="mt-1 break-words">
                          后端命令:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.backend_command}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.probe_recipe.command_env && (
                        <p className="mt-1 break-words">
                          覆盖环境变量:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.command_env}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.probe_recipe.environment_checks?.length ? (
                        <p className="mt-1 break-words">
                          检查:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.environment_checks.join(", ")}
                          </span>
                        </p>
                      ) : null}
                    </div>
                  )}
                  {provider.diagnostics.manual_probe_command && (
                    <p className="break-words">
                      手工:{" "}
                      <span className="font-data text-on-surface">
                        {provider.diagnostics.manual_probe_command}
                      </span>
                    </p>
                  )}
                  {provider.diagnostics.troubleshooting?.[0] && (
                    <p className="leading-5">{provider.diagnostics.troubleshooting[0]}</p>
                  )}
                  {provider.diagnostics.startup_probe_endpoint && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        onClick={() => runProviderStartupProbe(provider.provider)}
                        disabled={busyAction === `provider-probe-${provider.provider}`}
                        className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        {busyAction === `provider-probe-${provider.provider}` ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <PlayCircle size={13} />
                        )}
                        启动探测
                      </button>
                      {provider.agent_owned && provider.command.length > 0 && (
                        <button
                          onClick={() => runProviderTaskProbe(provider.provider)}
                          disabled={busyAction === `provider-task-probe-${provider.provider}`}
                          className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                        >
                          {busyAction === `provider-task-probe-${provider.provider}` ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <PlayCircle size={13} />
                          )}
                          任务探测
                        </button>
                      )}
                    </div>
                  )}
                  {providerProbeResults[provider.provider] && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                      <p>
                        探测结果:{" "}
                        <span className="font-data text-on-surface">
                          {providerProbeResults[provider.provider].status}
                        </span>
                      </p>
                      <p className="mt-1 break-words">
                        {providerProbeResults[provider.provider].message}
                      </p>
                      {providerProbeResults[provider.provider].health?.reason && (
                        <p className="mt-1 break-words">
                          健康原因:{" "}
                          {providerProbeResults[provider.provider].health?.reason}
                        </p>
                      )}
                      {providerProbeResults[provider.provider].health?.launch_kind && (
                        <p className="mt-1">
                          探测启动:{" "}
                          <span className="font-data text-on-surface">
                            {providerProbeResults[provider.provider].health?.launch_kind}
                          </span>
                          {providerProbeResults[provider.provider].health?.used_fallback && (
                            <span className="ml-2 font-medium text-warning">fallback</span>
                          )}
                        </p>
                      )}
                      {providerProbeResults[provider.provider].health?.attempts && (
                        <p className="mt-1">
                          探测次数:{" "}
                          <span className="font-data text-on-surface">
                            {providerProbeResults[provider.provider].health?.attempts?.length}
                          </span>
                        </p>
                      )}
                      {(() => {
                        const attempts =
                          providerProbeResults[provider.provider].health?.attempts ?? [];
                        if (attempts.length === 0) return null;
                        return (
                          <div className="mt-2 space-y-1">
                            {attempts.slice(0, 3).map((attempt, index) => {
                              const resolutionLines = commandResolutionLines(attempt.resolution);
                              return (
                                <div
                                  key={`${attempt.command ?? attempt.executable ?? index}-${index}`}
                                  className="rounded border border-outline-variant/30 px-2 py-1"
                                >
                                  <p className="break-words font-data text-[10px] text-on-surface">
                                    attempt {index + 1}:{" "}
                                    {attempt.command || attempt.executable || "unknown"}{" "}
                                    {attempt.status || attempt.probe_status || "unknown"}
                                  </p>
                                  {(attempt.reason || attempt.probe_message) && (
                                    <p className="mt-1 break-words">
                                      {attempt.reason || attempt.probe_message}
                                    </p>
                                  )}
                                  {resolutionLines.length > 0 && (
                                    <div className="mt-1 space-y-0.5">
                                      {resolutionLines.map((line) => (
                                        <p
                                          key={line}
                                          className="break-words font-data text-[10px] text-on-surface"
                                        >
                                          {line}
                                        </p>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                            {attempts.length > 3 && (
                              <p className="font-data text-[10px]">
                                +{attempts.length - 3} more attempts in artifact
                              </p>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                  {providerTaskProbeResults[provider.provider] && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                      <p>
                        任务探测:{" "}
                        <span className="font-data text-on-surface">
                          {providerTaskProbeResults[provider.provider].status}
                        </span>
                        <span className="ml-2 font-data text-on-surface">
                          contract:
                          {providerTaskProbeResults[provider.provider].summary.task_contract_status}
                        </span>
                      </p>
                      <p className="mt-1">
                        Execution:{" "}
                        <span className="font-data text-on-surface">
                          {providerTaskProbeResults[provider.provider].summary.execution_status}
                        </span>
                        <span className="ml-2 font-data text-on-surface">
                          missing:
                          {providerTaskProbeResults[provider.provider].summary.missing_required}
                        </span>
                      </p>
                      {providerTaskProbeResults[provider.provider].summary.missing_artifacts.length > 0 && (
                        <p className="mt-1 break-words text-warning">
                          缺失产物:{" "}
                          {providerTaskProbeResults[provider.provider].summary.missing_artifacts.join(", ")}
                        </p>
                      )}
                      <p className="mt-1 break-words font-data text-[10px]">
                        artifact:{providerTaskProbeResults[provider.provider].artifact.path}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {!providerMatrix && (
            <p className="text-sm text-on-surface-variant">
              执行器诊断会随工作台数据一起加载。
            </p>
          )}
        </div>
        {providerMatrix?.notes?.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {providerMatrix.notes.map((note) => (
              <span
                key={note}
                className="rounded bg-surface px-2 py-1 text-xs text-on-surface-variant"
              >
                {note}
              </span>
            ))}
          </div>
        ) : null}
      </Panel>
      )}

      {activeWorkbenchView === "workflow" && (
        <Panel title="工作流编排" icon={<ClipboardList size={16} />}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {workflowPresets.length > 0 && (
              <select
                value={selectedPresetId}
                onChange={(event) => setSelectedPresetId(event.target.value)}
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                aria-label="工作流预设"
              >
                {workflowPresets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </select>
            )}
            <button
              onClick={applyPreset}
              disabled={!selectedPresetId}
              className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              应用预设
            </button>
            <button
              onClick={installPreset}
              disabled={busyAction === "install-preset" || !selectedPresetId}
              className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "install-preset" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Save size={14} />
              )}
              安装预设
            </button>
            <button
              onClick={loadSelectedWorkflowDraft}
              disabled={!workflows.some((item) => item.id === selectedWorkflowId)}
              className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              载入所选
            </button>
            <button
              onClick={duplicateSelectedWorkflowDraft}
              disabled={!workflows.some((item) => item.id === selectedWorkflowId)}
              className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              复制
            </button>
            <button
              onClick={saveWorkflow}
              disabled={busyAction === "save-workflow"}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busyAction === "save-workflow" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Save size={14} />
              )}
              保存工作流
            </button>
            <button
              onClick={auditWorkflowDraft}
              disabled={busyAction === "audit-workflow-draft"}
              className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "audit-workflow-draft" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Search size={14} />
              )}
              审计草稿
            </button>
            <span className="text-xs text-on-surface-variant">
              {workflows.length} 个已注册
            </span>
          </div>
          <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface p-3">
            <div className="mb-3 flex flex-wrap items-end gap-2">
              <label className="min-w-48 flex-1">
                <span className="mb-1 block text-xs text-on-surface-variant">场景</span>
                <select
                  value={builderScenario}
                  onChange={(event) =>
                    applyBuilderScenario(
                      event.target.value as keyof typeof WORKFLOW_BUILDER_SCENARIOS,
                    )
                  }
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder scenario"
                >
                  {Object.entries(WORKFLOW_BUILDER_SCENARIOS).map(([id, scenario]) => (
                    <option key={id} value={id}>
                      {scenario.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={generateWorkflowDraft}
                disabled={busyAction === "generate-workflow"}
                className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "generate-workflow" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <ClipboardList size={14} />
                )}
                生成草稿
              </button>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">工作流 ID</span>
                <input
                  value={builderWorkflowId}
                  onChange={(event) => setBuilderWorkflowId(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder id"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">工作流名称</span>
                <input
                  value={builderWorkflowName}
                  onChange={(event) => setBuilderWorkflowName(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder name"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">执行器预设</span>
                <select
                  value={
                    builderProviderOptions.some((provider) => provider.id === builderProvider)
                      ? builderProvider
                      : ""
                  }
                  onChange={(event) => {
                    if (event.target.value) {
                      setBuilderProvider(event.target.value);
                    }
                  }}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder provider preset"
                >
                  <option value="">自定义执行器</option>
                  {builderProviderOptions.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label} ({provider.id}:{provider.status})
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">执行器 ID</span>
                <input
                  value={builderProvider}
                  onChange={(event) => setBuilderProvider(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder provider"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">MCP 配置</span>
                <input
                  value={builderMcpProfile}
                  onChange={(event) => setBuilderMcpProfile(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder MCP 配置"
                />
              </label>
            </div>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                输入项，格式 id:type 或 id:type@resolver
              </span>
              <input
                value={builderInputSpec}
                onChange={(event) => setBuilderInputSpec(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder inputs"
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                输出项，格式 id:type 或 id:type=artifact
              </span>
              <input
                value={builderOutputSpec}
                onChange={(event) => setBuilderOutputSpec(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder outputs"
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                必需产物
              </span>
              <input
                value={builderArtifacts}
                onChange={(event) => setBuilderArtifacts(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder required artifacts"
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                输出 Schema JSON
              </span>
              <textarea
                value={builderOutputSchemas}
                onChange={(event) => setBuilderOutputSchemas(event.target.value)}
                className="h-28 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder output schemas"
                spellCheck={false}
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                证据映射 JSON
              </span>
              <textarea
                value={builderEvidenceMappings}
                onChange={(event) => setBuilderEvidenceMappings(event.target.value)}
                className="h-32 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder evidence mappings"
                spellCheck={false}
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                语义导入 JSON
              </span>
              <textarea
                value={builderSemanticImports}
                onChange={(event) => setBuilderSemanticImports(event.target.value)}
                className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder semantic imports"
                spellCheck={false}
              />
            </label>
            {builderOutputPreview.length > 0 && (
              <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container px-2 py-1.5">
                <p className="mb-1 text-xs font-medium text-on-surface-variant">
                  输出契约预览
                </p>
                <div className="space-y-1 font-data text-[10px] text-on-surface-variant">
                  {builderOutputPreview.map((output) => (
                    <div
                      key={`${output.id}:${output.type}`}
                      className="break-words rounded bg-surface px-1.5 py-1"
                    >
                      <span className="text-on-surface">
                        {output.id}:{output.type}
                      </span>
                      {output.artifact && <span> artifact:{output.artifact}</span>}
                      {output.schema && <span> schema</span>}
                      {output.evidenceMemory && (
                        <span>
                          {" "}
                          evidence_memory
                          {output.evidenceKind ? `:${output.evidenceKind}` : ""}
                        </span>
                      )}
                      {output.semanticImport && <span> semantic_import</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                输入 Schema JSON
              </span>
              <textarea
                value={builderInputSchemas}
                onChange={(event) => setBuilderInputSchemas(event.target.value)}
                className="h-28 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder input schemas"
                spellCheck={false}
              />
            </label>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">智能体目标</span>
              <textarea
                value={builderGoal}
                onChange={(event) => setBuilderGoal(event.target.value)}
                className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder goal"
              />
            </label>
          </div>
          <div
            className={`mb-2 rounded-lg border px-3 py-2 text-xs ${
              workflowDraftAuditSummary.status === "invalid"
                ? "border-red-400/20 bg-red-400/5 text-red-300"
                : workflowDraftAuditSummary.status === "warning"
                  ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                  : "border-outline-variant/30 bg-surface-container text-on-surface-variant"
            }`}
          >
            <div className="flex flex-wrap gap-2">
              <span className="font-medium">Draft:{workflowDraftAuditSummary.status}</span>
              <span>inputs:{workflowDraftAuditSummary.inputCount}</span>
              <span>steps:{workflowDraftAuditSummary.stepCount}</span>
              <span>agent:{workflowDraftAuditSummary.agentStepCount}</span>
              <span>outputs:{workflowDraftAuditSummary.outputCount}</span>
              <span>evidence:{workflowDraftAuditSummary.evidenceMemoryOutputCount}</span>
              <span>semantic:{workflowDraftAuditSummary.semanticImportOutputCount}</span>
              <span>artifacts:{workflowDraftAuditSummary.requiredArtifacts.length}</span>
            </div>
            {workflowDraftAuditSummary.blocking.length > 0 && (
              <div className="mt-1 space-y-0.5 font-data text-[10px]">
                {workflowDraftAuditSummary.blocking.slice(0, 3).map((item) => (
                  <div key={item}>blocking:{item}</div>
                ))}
              </div>
            )}
            {workflowDraftAuditSummary.warnings.length > 0 && (
              <div className="mt-1 space-y-0.5 font-data text-[10px]">
                {workflowDraftAuditSummary.warnings.slice(0, 3).map((item) => (
                  <div key={item}>warning:{item}</div>
                ))}
              </div>
            )}
          </div>
          {workflowDraftServerAudit && (
            <div
              className={`mb-2 rounded-lg border px-3 py-2 text-xs ${
                workflowDraftServerAudit.valid
                  ? workflowDraftServerAudit.status === "warning"
                    ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                    : "border-outline-variant/30 bg-surface-container text-on-surface-variant"
                  : "border-red-400/20 bg-red-400/5 text-red-300"
              }`}
            >
              <div className="flex flex-wrap gap-2">
                <span className="font-medium">
                  Server audit:{workflowDraftServerAudit.status}
                </span>
                <span>valid:{String(workflowDraftServerAudit.valid)}</span>
                <span>warnings:{workflowDraftServerAudit.warnings.length}</span>
              </div>
              {workflowDraftServerAudit.error && (
                <div className="mt-1 break-words font-data text-[10px]">
                  error:{workflowDraftServerAudit.error}
                </div>
              )}
              {workflowDraftServerAudit.warnings.length > 0 && (
                <div className="mt-1 space-y-0.5 font-data text-[10px]">
                  {workflowDraftServerAudit.warnings.slice(0, 4).map((warning) => (
                    <div key={`${warning.code}:${warning.path}`} className="break-words">
                      {warning.code}:{warning.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <textarea
            value={workflowJson}
            onChange={(event) => setWorkflowJson(event.target.value)}
            className="h-72 max-h-[54vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
            aria-label="Workflow JSON"
            spellCheck={false}
          />
        </Panel>
      )}

      {activeWorkbenchView === "run" && (
        <Panel title="任务运行" icon={<PlayCircle size={16} />}>
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">工作流</span>
              <select
                value={selectedWorkflowId}
                onChange={(event) => setSelectedWorkflowId(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              >
                {[selectedWorkflowId, ...workflowOptions]
                  .map((item) =>
                    typeof item === "string"
                      ? { id: item, label: workflowDisplayName(item) }
                      : item,
                  )
                  .filter(
                    (option, index, options) =>
                      option.id && options.findIndex((item) => item.id === option.id) === index,
                  )
                  .map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
              </select>
            </label>
            {selectedWorkflowAudit && selectedWorkflowAudit.warnings.length > 0 && (
              <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-300">
                <div className="flex items-start gap-2">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium">
                      工作流审计警告: {selectedWorkflowAudit.warnings.length}
                    </p>
                    <div className="mt-1 space-y-1">
                      {selectedWorkflowAudit.warnings.slice(0, 3).map((warning) => (
                        <p key={`${warning.code}-${warning.path}`} className="break-words">
                          {warning.code}: {warning.message}
                        </p>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">工作区 ID</span>
              <input
                aria-label="Workspace ID"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">仓库路径</span>
              <input
                aria-label="Repo path"
                value={repoPath}
                onChange={(event) => setRepoPath(event.target.value)}
                placeholder="E:\\repo\\nof"
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface outline-none focus:border-primary"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                执行器覆盖
              </span>
              <input
                aria-label="执行器覆盖"
                value={providerOverride}
                onChange={(event) => setProviderOverride(event.target.value)}
                placeholder="claude-code / opencode / internal-agent"
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
            </label>
            {selectedWorkflowInputs.length > 0 && (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                <p className="mb-2 text-xs font-medium text-on-surface">
                  工作流输入
                </p>
                <div className="space-y-2">
                  {selectedWorkflowInputs.map((input) => {
                    const inputId = String(input.id ?? "");
                    const inputType = String(input.type ?? "text");
                    const required = input.required === true;
                    const role = String(input.role ?? "");
                    const value = inputTextValue(parsedPrepareInputs, input);
                    if (!inputId) return null;
                    if (inputType === "boolean") {
                      return (
                        <label key={inputId} className="block">
                          <span className="mb-1 block text-xs text-on-surface-variant">
                            {inputId}:{inputType}
                            {required ? " *" : ""}
                          </span>
                          <select
                            aria-label={`Workflow input ${inputId}`}
                            value={value === "true" ? "true" : "false"}
                            onChange={(event) => updatePrepareInput(input, event.target.value)}
                            className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                          >
                            <option value="false">false</option>
                            <option value="true">true</option>
                          </select>
                        </label>
                      );
                    }
                    const multiline = inputType === "file_set" || inputType === "long_text";
                    return (
                      <label key={inputId} className="block">
                        <span className="mb-1 block text-xs text-on-surface-variant">
                          {inputId}:{inputType}
                          {required ? " *" : ""}
                        </span>
                        {multiline ? (
                          <>
                            <textarea
                              aria-label={`Workflow input ${inputId}`}
                              value={value}
                              onChange={(event) => updatePrepareInput(input, event.target.value)}
                              placeholder={
                                inputType === "file_set"
                                  ? "每行一个本地文件路径"
                                  : role || "输入文本"
                              }
                              className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                              spellCheck={false}
                            />
                            {inputType === "file_set" && (
                              <input
                                aria-label={`Upload file for ${inputId}`}
                                type="file"
                                multiple
                                onChange={(event) =>
                                  uploadPrepareInputFile(input, event.currentTarget.files)
                                }
                                className="mt-1 block w-full text-xs text-on-surface-variant file:mr-2 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                              />
                            )}
                          </>
                        ) : (
                          <>
                            <input
                              aria-label={`Workflow input ${inputId}`}
                              value={value}
                              onChange={(event) => updatePrepareInput(input, event.target.value)}
                              placeholder={
                                isFileLikeWorkflowInput(inputType)
                                  ? "本地文件路径"
                                  : role || "输入值"
                              }
                              className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                            />
                            {isFileLikeWorkflowInput(inputType) && (
                              <input
                                aria-label={`Upload file for ${inputId}`}
                                type="file"
                                onChange={(event) =>
                                  uploadPrepareInputFile(input, event.currentTarget.files)
                                }
                                className="mt-1 block w-full text-xs text-on-surface-variant file:mr-2 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                              />
                            )}
                          </>
                        )}
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">输入 JSON</span>
              <textarea
                value={inputsJson}
                onChange={(event) => setInputsJson(event.target.value)}
                className="h-40 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Inputs JSON"
                spellCheck={false}
              />
            </label>
            <button
              onClick={createAndRunTaskRun}
              disabled={busyAction === "create-and-run-task-run" || !repoPath.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busyAction === "create-and-run-task-run" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <PlayCircle size={14} />
              )}
              创建并运行
            </button>
            <button
              onClick={prepareTaskRun}
              disabled={busyAction === "prepare-task-run" || !repoPath.trim()}
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "prepare-task-run" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <PlayCircle size={14} />
              )}
              准备运行
            </button>
            <button
              onClick={executePreparedWorkflow}
              disabled={busyAction === "execute-workflow" || !preparedRun}
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "execute-workflow" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <PlayCircle size={14} />
              )}
              执行工作流
            </button>
            <button
              onClick={loadPreparedArtifacts}
              disabled={busyAction === "load-artifacts" || !preparedRun}
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "load-artifacts" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <ClipboardList size={14} />
              )}
              审计产物
            </button>
            <button
              onClick={loadTaskRerunPlan}
              disabled={busyAction === "load-rerun-plan" || !preparedRun}
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "load-rerun-plan" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              复跑计划
            </button>
            <button
              onClick={generateTaskAcceptanceAudit}
              disabled={busyAction === "acceptance-audit" || !preparedRun}
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "acceptance-audit" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Search size={14} />
              )}
              验收审计
            </button>
            <button
              onClick={executeTaskRerunPlan}
              disabled={
                busyAction === "execute-rerun-plan" ||
                !preparedRun ||
                !taskRerunPlanValidation?.can_rerun
              }
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "execute-rerun-plan" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <PlayCircle size={14} />
              )}
              执行复跑
            </button>
            <button
              onClick={materializePreparedWorkflowOutputs}
              disabled={
                busyAction === "materialize-workflow-outputs" ||
                !preparedRun ||
                !workflowExecution
              }
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "materialize-workflow-outputs" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Database size={14} />
              )}
              固化输出
            </button>
            <button
              onClick={importPreparedSemanticOutputs}
              disabled={
                busyAction === "import-semantic-outputs" ||
                !preparedRun ||
                semanticImportOutputIds.length === 0
              }
              className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              {busyAction === "import-semantic-outputs" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Library size={14} />
              )}
              导入语义
            </button>
            {preparedRun && (
              <div className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs">
                <p className="font-medium text-on-surface">{preparedRun.task_run_id}</p>
                <p className="mt-1 break-words font-data text-on-surface-variant">
                  {preparedRun.artifact_dir}
                </p>
                <p className="mt-1 text-on-surface-variant">
                  Agent runs: {preparedRun.agent_runs.length}
                </p>
                <button
                  type="button"
                  onClick={openPreparedConversation}
                  disabled={openingConversation}
                  className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-on-primary shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md disabled:translate-y-0 disabled:opacity-50"
                >
                  {openingConversation ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <MessageSquareText size={13} />
                  )}
                  围绕本次运行继续追问
                </button>
                {taskAcceptanceAudit &&
                  taskAcceptanceAudit.task_run_id === preparedRun.task_run_id && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>
                        Acceptance:{" "}
                        <span
                          className={
                            taskAcceptanceAudit.status === "ready" ||
                            taskAcceptanceAudit.status === "passed"
                              ? "text-on-surface"
                              : "text-warning"
                          }
                        >
                          {taskAcceptanceAudit.status}
                        </span>
                        <span className="ml-2">
                          artifacts:{taskAcceptanceAudit.summary.artifact_count}
                        </span>
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          required:{taskAcceptanceAudit.summary.required_checks}
                        </span>
                        <span
                          className={`rounded bg-surface px-1.5 py-0.5 ${
                            taskAcceptanceAudit.summary.missing_required > 0
                              ? "text-warning"
                              : ""
                          }`}
                        >
                          missing-required:
                          {taskAcceptanceAudit.summary.missing_required}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          recommended:{taskAcceptanceAudit.summary.recommended_checks}
                        </span>
                        <span
                          className={`rounded bg-surface px-1.5 py-0.5 ${
                            taskAcceptanceAudit.summary.missing_recommended > 0
                              ? "text-warning"
                              : ""
                          }`}
                        >
                          missing-recommended:
                          {taskAcceptanceAudit.summary.missing_recommended}
                        </span>
                      </div>
                      {(() => {
                        const providerIssues = acceptanceProviderIssues(taskAcceptanceAudit);
                        if (providerIssues.length === 0) return null;
                        return (
                          <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                            <p className="text-[11px] font-medium text-warning">
                              Agent provider readiness
                            </p>
                            <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                              {providerIssues.slice(0, 4).map((issue) => (
                                <div key={issue.provider} className="break-words">
                                  {issue.provider}:{issue.status}
                                  {issue.usedFallback ? " fallback" : ""}
                                  {issue.deploymentTaskProbeStatus
                                    ? ` deployment:${issue.deploymentTaskProbeStatus}`
                                    : ""}
                                  {issue.deploymentEvidenceConflict ? " conflict" : ""}
                                  {issue.deploymentProbeId
                                    ? ` probe-id:${issue.deploymentProbeId}`
                                    : ""}
                                  {issue.reason ? ` reason:${issue.reason}` : ""}
                                  {issue.startupProbeEndpoint
                                    ? ` probe:${issue.startupProbeEndpoint}`
                                    : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      {(() => {
                        const providerIssues =
                          acceptanceCodetalkProviderIssues(taskAcceptanceAudit);
                        if (providerIssues.length === 0) return null;
                        return (
                          <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                            <p className="text-[11px] font-medium text-warning">
                              CodeTalk provider readiness
                            </p>
                            <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                              {providerIssues.slice(0, 4).map((issue) => (
                                <div key={issue.provider} className="break-words">
                                  {issue.provider}:{issue.status}
                                  {issue.reason ? ` reason:${issue.reason}` : ""}
                                  {issue.startupProbeEndpoint
                                    ? ` check:${issue.startupProbeEndpoint}`
                                    : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      {(() => {
                        const outputIssues = acceptanceWorkflowOutputIssues(taskAcceptanceAudit);
                        if (outputIssues.length === 0) return null;
                        return (
                          <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                            <p className="text-[11px] font-medium text-warning">
                              工作流输出就绪度
                            </p>
                            <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                              {outputIssues.slice(0, 4).map((issue) => (
                                <div key={issue.outputId} className="break-words">
                                  {issue.outputId}:{issue.status}
                                  {issue.reason ? ` reason:${issue.reason}` : ""}
                                  {issue.artifact ? ` artifact:${issue.artifact}` : ""}
                                  {issue.schemaErrorCount > 0
                                    ? ` schema-errors:${issue.schemaErrorCount}`
                                    : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      {(() => {
                        const redactionIssues =
                          acceptanceInputRedactionIssues(taskAcceptanceAudit);
                        if (redactionIssues.length === 0) return null;
                        return (
                          <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                            <p className="text-[11px] font-medium text-warning">
                              Agent input redaction
                            </p>
                            <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                              {redactionIssues.slice(0, 4).map((issue) => (
                                <div key={issue.id} className="break-words">
                                  {issue.label}
                                  {issue.reason ? ` reason:${issue.reason}` : ""}
                                  {issue.stdinSha ? ` stdin-sha:${issue.stdinSha.slice(0, 12)}` : ""}
                                  {issue.relativePath ? ` artifact:${issue.relativePath}` : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      {(() => {
                        const policyIssues =
                          acceptanceInstructionPolicyIssues(taskAcceptanceAudit);
                        if (policyIssues.length === 0) return null;
                        return (
                          <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                            <p className="text-[11px] font-medium text-warning">
                              Agent instruction policy
                            </p>
                            <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                              {policyIssues.slice(0, 4).map((issue) => (
                                <div key={issue.id} className="break-words">
                                  {issue.label}
                                  {issue.reason ? ` reason:${issue.reason}` : ""}
                                  {issue.expectedFiles.length > 0
                                    ? ` expected:${issue.expectedFiles.slice(0, 3).join(",")}`
                                    : ""}
                                  {issue.relativePath ? ` artifact:${issue.relativePath}` : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      {taskAcceptanceAudit.missing_required.length > 0 && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                          {taskAcceptanceAudit.missing_required.slice(0, 3).map((item, index) => (
                            <div key={`${String(item.id ?? index)}:${index}`}>
                              {String(item.id ?? "check")}:
                              {String(item.reason ?? item.relative_path ?? "missing")}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                {taskRerunPlan && taskRerunPlan.task_run_id === preparedRun.task_run_id && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    <p>
                      Rerun: {taskRerunPlan.status} / steps{" "}
                      {taskRerunPlan.steps?.length ?? 0}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        preserve-inputs:{String(taskRerunPlan.preserve_inputs ?? false)}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        reuse-bundle:{String(taskRerunPlan.reuse_task_bundle ?? false)}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        history:{taskRerunHistory?.count ?? 0}
                      </span>
                      {(taskRerunPlan.blocked_outputs?.length ?? 0) > 0 ? (
                        <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                          blocked:{taskRerunPlan.blocked_outputs?.length ?? 0}
                        </span>
                      ) : null}
                    </div>
                    {taskRerunPlanValidation &&
                      taskRerunPlanValidation.task_run_id === preparedRun.task_run_id && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              taskRerunPlanValidation.can_rerun ? "" : "text-warning"
                            }`}
                          >
                            validation:{taskRerunPlanValidation.status}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            can-rerun:{String(taskRerunPlanValidation.can_rerun)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            checks:{taskRerunPlanValidation.checks?.length ?? 0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            steps:{taskRerunPlanValidation.steps?.length ?? 0}
                          </span>
                        </div>
                      )}
                    {taskRerunExecution && (
                      <div className="mt-1 space-y-0.5 font-data text-[10px] text-on-surface-variant">
                        <p>
                          rerun-execution:{taskRerunExecution.status} workflow:
                          {taskRerunExecution.execution?.status ?? "unknown"}
                        </p>
                        {(() => {
                          const latest = taskRerunHistory?.records?.at(-1);
                          if (!latest) return null;
                          const execution =
                            latest.execution && typeof latest.execution === "object"
                              ? (latest.execution as Record<string, unknown>)
                              : {};
                          const executionArtifactRecord =
                            execution.artifact && typeof execution.artifact === "object"
                              ? (execution.artifact as Record<string, unknown>)
                              : {};
                          const latestArtifactRecord =
                            latest.artifact && typeof latest.artifact === "object"
                              ? (latest.artifact as Record<string, unknown>)
                              : {};
                          const rerunId = String(latest.rerun_id ?? "");
                          const sequence = String(latest.sequence ?? "");
                          const executionArtifact = String(
                            latestArtifactRecord.path ??
                              latestArtifactRecord.manifest_path ??
                              executionArtifactRecord.path ??
                              executionArtifactRecord.manifest_path ??
                              "task_rerun_execution.json",
                          );
                          return (
                            <div className="rounded bg-surface px-1.5 py-1">
                              <p>rerun-id:{rerunId || "unknown"}</p>
                              <p>sequence:{sequence || "unknown"}</p>
                              <p className="break-words">
                                history-latest:{executionArtifact}
                              </p>
                            </div>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                )}
                {(() => {
                  const readiness = providerReadinessSummary(preparedRun.task_bundle);
                  if (!readiness) return null;
                  const visibleCodetalk = readiness.codetalkProviders.filter((provider) =>
                    ["gitnexus", "cgc", "local-search"].includes(provider.provider),
                  );
                  return (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>
                        执行器就绪度:{" "}
                        <span
                          className={
                            readiness.status === "ready"
                              ? "text-on-surface"
                              : "text-warning"
                          }
                        >
                          {readiness.status}
                        </span>
                        <span className="ml-2">repo:{readiness.repoStatus}</span>
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        {visibleCodetalk.map((provider) => (
                          <span
                            key={provider.provider}
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              provider.status === "available" ||
                              provider.status === "configured"
                                ? ""
                                : "text-warning"
                            }`}
                            title={provider.nextCheck}
                          >
                            {provider.provider}:{provider.status}
                          </span>
                        ))}
                        {readiness.agentProviders.map((provider) => (
                          <span
                            key={provider.provider}
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              provider.status === "available" &&
                              !provider.deploymentEvidenceConflict
                                ? ""
                                : "text-warning"
                            }`}
                            title={[
                              provider.reason,
                              provider.deploymentProbeId
                                ? `deployment probe:${provider.deploymentProbeId}`
                                : "",
                            ].filter(Boolean).join(" / ")}
                          >
                            {provider.provider}:{provider.status}
                            {provider.deploymentTaskProbeStatus && (
                              <span className="ml-1">
                                probe:{provider.deploymentTaskProbeStatus}
                              </span>
                            )}
                            {provider.deploymentEvidenceConflict && (
                              <span className="ml-1">conflict</span>
                            )}
                          </span>
                        ))}
                        {readiness.blockingReasons.length > 0 && (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            blocked:{readiness.blockingReasons.join(",")}
                          </span>
                        )}
                        {readiness.warnings.length > 0 && (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            warnings:{readiness.warnings.length}
                          </span>
                        )}
                      </div>
                      {readiness.agentProviders.some(
                        (provider) =>
                          provider.reason ||
                          provider.startupProbeEndpoint ||
                          provider.manualProbeCommand ||
                          provider.configuredCommand,
                      ) && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px]">
                          {readiness.agentProviders
                            .filter(
                              (provider) =>
                                provider.status !== "available" ||
                                provider.reason ||
                                provider.deploymentEvidenceConflict,
                            )
                            .slice(0, 4)
                            .map((provider) => (
                              <div
                                key={`${provider.provider}:readiness-detail`}
                                className="break-words"
                              >
                                {provider.provider}
                                {provider.configuredCommand
                                  ? ` command:${provider.configuredCommand}`
                                  : ""}
                                {provider.usedFallback ? " fallback" : ""}
                                {provider.reason ? ` reason:${provider.reason}` : ""}
                                {provider.startupProbeEndpoint
                                  ? ` probe:${provider.startupProbeEndpoint}`
                                  : ""}
                                {provider.manualProbeCommand
                                  ? ` manual:${provider.manualProbeCommand}`
                                  : ""}
                              </div>
                            ))}
                        </div>
                      )}
                    </div>
                  );
                })()}
                {(() => {
                  const contextBundle = preparedRun.task_bundle.context_bundle as
                    | {
                        evidence?: unknown[];
                        deployment_evidence?: unknown[];
                        semantic_cases?: unknown[];
                      }
                    | undefined;
                  if (!contextBundle) return null;
                  return (
                    <p className="mt-1 text-on-surface-variant">
                      Context: evidence {contextBundle.evidence?.length ?? 0} /
                      deployment {contextBundle.deployment_evidence?.length ?? 0} /
                      semantics {contextBundle.semantic_cases?.length ?? 0}
                    </p>
                  );
                })()}
                {(() => {
                  const instructions = preparedRun.task_bundle.agent_instructions as
                    | {
                        files?: unknown[];
                      }
                    | undefined;
                  if (!instructions) return null;
                  return (
                    <p className="mt-1 text-on-surface-variant">
                      Agent instructions: {instructions.files?.length ?? 0}
                    </p>
                  );
                })()}
                {(() => {
                  const summary = inputContextSummary(preparedRun.task_bundle);
                  if (!summary) return null;
                  return (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>Input context: {summary.fileCount} files</p>
                      {summary.inputs.length > 0 && (
                        <div className="mt-1 space-y-1">
                          {summary.inputs.slice(0, 4).map((input, index) => (
                            <div
                              key={`${input.inputId}-${input.filename}-${index}`}
                              className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                            >
                              <span className="text-on-surface">
                                {input.filename || input.inputId}
                              </span>
                              <span className="ml-1">
                                {input.suffix || input.kind || "file"}
                              </span>
                              <span className="ml-1">chunks:{input.chunkCount}</span>
                              {input.textTruncated && (
                                <span className="ml-1 text-warning">truncated</span>
                              )}
                              {input.parseWarnings.length > 0 && (
                                <span className="ml-1 break-words text-warning">
                                  warnings:{input.parseWarnings.slice(0, 2).join(",")}
                                  {input.parseWarnings.length > 2 ? ",..." : ""}
                                </span>
                              )}
                            </div>
                          ))}
                          {summary.inputs.length > 4 && (
                            <p className="font-data text-[10px]">
                              +{summary.inputs.length - 4} more
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}
                {(() => {
                  const requests = agentMcpRequestSummary(preparedRun.task_bundle);
                  if (requests.length === 0) return null;
                  return (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>Agent MCP requests: {requests.length}</p>
                      <div className="mt-1 space-y-1">
                        {requests.slice(0, 4).map((request, index) => (
                          <div
                            key={`${request.inputId}-${index}`}
                            className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                          >
                            <span className="text-on-surface">
                              {request.inputId || "mcp_input"}
                            </span>
                            <span className="ml-1">{request.inputType || "input"}</span>
                            <span className="ml-1">
                              owner:{request.credentialOwner || "agent_cli"}
                            </span>
                            <span
                              className={`ml-1 ${
                                request.codetalkFetchAllowed ? "text-warning" : ""
                              }`}
                            >
                              codetalk-fetch:{String(request.codetalkFetchAllowed)}
                            </span>
                            {request.mcpProfiles.length > 0 && (
                              <span className="ml-1">
                                profiles:{request.mcpProfiles.join(",")}
                              </span>
                            )}
                            {request.requiredArtifacts.length > 0 && (
                              <span className="ml-1 break-words">
                                artifacts:{request.requiredArtifacts.slice(0, 4).join(",")}
                              </span>
                            )}
                          </div>
                        ))}
                        {requests.length > 4 && (
                          <p className="font-data text-[10px]">+{requests.length - 4} more</p>
                        )}
                      </div>
                    </div>
                  );
                })()}
                {(() => {
                  const summary = fastContextDecisionSummary(preparedRun.task_bundle);
                  if (!summary) return null;
                  return (
                    <p className="mt-1 font-data text-[11px] text-on-surface-variant">
                      {summary}
                    </p>
                  );
                })()}
                {artifactManifest && artifactManifest.task_run_id === preparedRun.task_run_id && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    审计产物: {artifactManifest.artifacts.length}
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {prioritizedAuditArtifacts(artifactManifest.artifacts).slice(0, 12).map((artifact) => (
                        <button
                          key={artifact.relative_path}
                          onClick={() => previewArtifact(artifact.relative_path)}
                          disabled={busyAction === `preview-artifact-${artifact.relative_path}`}
                          className="rounded bg-surface px-1.5 py-0.5 text-left font-data text-[10px] transition-colors hover:bg-surface-container-high disabled:opacity-50"
                        >
                          {artifact.kind}:{artifact.relative_path}
                          {artifact.preview_redacted && (
                            <span className="ml-1 text-warning">redacted</span>
                          )}
                        </button>
                      ))}
                    </div>
                    {artifactContent && (
                      <div className="mt-2 rounded border border-outline-variant/30 bg-surface p-2">
                        <div className="flex flex-wrap items-center gap-2 text-[11px]">
                          <span className="font-medium text-on-surface">
                            {artifactContent.relative_path}
                          </span>
                          <span className="font-data">{artifactContent.kind}</span>
                          <span className="font-data">
                            sha:{artifactContent.sha256.slice(0, 12)}
                          </span>
                          {artifactContent.truncated && (
                            <span className="text-warning">truncated</span>
                          )}
                          {artifactContent.content_redacted && (
                            <span className="text-warning">redacted</span>
                          )}
                        </div>
                        {(() => {
                          const summary = evidenceValidationSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Accepted artifacts: {summary.acceptedCount}</span>
                                <span>Rejected artifacts: {summary.rejectedCount}</span>
                              </div>
                              {summary.acceptedDetails.length > 0 && (
                                <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                  {summary.acceptedDetails.slice(0, 4).map((item) => (
                                    <div key={`${item.sourceStepId}:${item.artifact}`}>
                                      {item.artifact} sha:{item.sha256.slice(0, 12)}
                                    </div>
                                  ))}
                                </div>
                              )}
                              {summary.rejectedDetails.length > 0 && (
                                <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                  {summary.rejectedDetails.slice(0, 3).map((item) => (
                                    <div key={`${item.sourceStepId}:${item.artifact}:${item.reason}`}>
                                      {item.artifact || "artifact"} rejected:{item.reason || "unknown"}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = workflowOutputMaterializationSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Materialized evidence: {summary.evidenceCount}</span>
                                <span>Rejected outputs: {summary.rejectedCount}</span>
                                <span>Declared outputs: {summary.outputCount}</span>
                                {summary.auditSummary.evidenceMemoryDeclaredCount > 0 && (
                                  <span>
                                    evidence memory:{summary.auditSummary.evidenceMemoryDeclaredCount}
                                  </span>
                                )}
                              </div>
                              {summary.auditOutputs.length > 0 && (
                                <div className="mt-1 space-y-1 font-data text-[10px]">
                                  {summary.auditOutputs.slice(0, 4).map((item) => (
                                    <div
                                      key={item.outputId}
                                      className={
                                        item.materializationStatus === "accepted"
                                          ? "text-on-surface"
                                          : item.materializationStatus === "partial"
                                            ? "text-warning"
                                            : "text-on-surface-variant"
                                      }
                                    >
                                      {item.outputId}:{item.materializationStatus || "unknown"}
                                      {item.artifact ? ` artifact:${item.artifact}` : ""}
                                      {item.mappingKind ? ` mapping:${item.mappingKind}` : ""}
                                      {item.materializedCount
                                        ? ` evidence:${item.materializedCount}`
                                        : ""}
                                      {item.rejectedCount ? ` rejected:${item.rejectedCount}` : ""}
                                      {item.rejectionReasons.length > 0
                                        ? ` reason:${item.rejectionReasons[0]}`
                                        : ""}
                                    </div>
                                  ))}
                                </div>
                              )}
                              {summary.firstRejected && (
                                <div className="mt-1 flex flex-wrap gap-2">
                                  <span>First rejected: {summary.firstRejected.output}</span>
                                  <span>reason:{summary.firstRejected.reason}</span>
                                  {summary.firstRejected.status && (
                                    <span>status:{summary.firstRejected.status}</span>
                                  )}
                                  {summary.firstRejected.schemaErrorCount > 0 && (
                                    <span>
                                      schema errors:{summary.firstRejected.schemaErrorCount}
                                    </span>
                                  )}
                                </div>
                              )}
                              {summary.workflowOutputsSha && (
                                <div className="mt-1 font-data text-[10px]">
                                  workflow_outputs sha:{summary.workflowOutputsSha.slice(0, 12)}
                                </div>
                              )}
                              {summary.materializedEvidence.length > 0 && (
                                <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                  {summary.materializedEvidence.slice(0, 4).map((item) => (
                                    <div key={`${item.evidenceId}:${item.kind}`}>
                                      {item.kind}:{item.subjectKey || item.evidenceId}
                                      {item.outputId ? ` output:${item.outputId}` : ""}
                                      {item.mappingKind ? ` mapping:${item.mappingKind}` : ""}
                                      {item.sourceStepId ? ` step:${item.sourceStepId}` : ""}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = blackBoxGenerationPolicySummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Black-box terms: {summary.termCount}</span>
                                <span>cases:{summary.caseCount}</span>
                                {summary.firstCaseId && <span>{summary.firstCaseId}</span>}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.firstTerms.slice(0, 4).map((term) => (
                                  <span key={term}>term:{term}</span>
                                ))}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.allowedUses.slice(0, 3).map((use) => (
                                  <span key={use}>allowed:{use}</span>
                                ))}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                {summary.mustNotUse.slice(0, 3).map((use) => (
                                  <span key={use}>must-not:{use}</span>
                                ))}
                              </div>
                              {summary.authorityRule && (
                                <div className="mt-1 break-words text-[10px]">
                                  {summary.authorityRule}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = memoryArtifactSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>
                                  {summary.kind === "memory_retrieval"
                                    ? "Memory retrieval"
                                    : "Context bundle"}
                                </span>
                                <span>evidence:{summary.evidenceCount}</span>
                                <span>deployment:{summary.deploymentCount}</span>
                                <span>semantics:{summary.semanticCount}</span>
                                <span>slices:{summary.sourceSliceCount}</span>
                              </div>
                              {summary.query && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  query:{summary.query}
                                </div>
                              )}
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.firstSubject && (
                                  <span>first:{summary.firstSubject}</span>
                                )}
                                {summary.firstDeploymentSubject && (
                                  <span>deployment:{summary.firstDeploymentSubject}</span>
                                )}
                              </div>
                              {summary.firstReuseReason && (
                                <div className="mt-1 break-words text-[10px]">
                                  reuse:{summary.firstReuseReason}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = inputMaterialsSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Input materials</span>
                                <span>materials:{summary.materialCount}</span>
                                <span>must-read:{String(summary.mustRead)}</span>
                                <span>source-truth:{String(summary.materialsAreSourceTruth)}</span>
                              </div>
                              {summary.readOrder.length > 0 && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  read-order:{summary.readOrder.slice(0, 6).join(",")}
                                </div>
                              )}
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.firstInputId && (
                                  <span>first:{summary.firstInputId}</span>
                                )}
                                {summary.firstRole && <span>role:{summary.firstRole}</span>}
                                {summary.firstFilename && (
                                  <span>file:{summary.firstFilename}</span>
                                )}
                                {summary.firstSha && (
                                  <span>sha:{summary.firstSha.slice(0, 12)}</span>
                                )}
                              </div>
                              {summary.firstChunksPath && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  chunks:{summary.firstChunksPath}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = failureRetryContextSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Failure retry</span>
                                {summary.stepId && <span>step:{summary.stepId}</span>}
                                {summary.failureKind && (
                                  <span>kind:{summary.failureKind}</span>
                                )}
                                <span>retryable:{String(summary.retryable)}</span>
                                {summary.exitCode && <span>exit:{summary.exitCode}</span>}
                              </div>
                              {summary.missingArtifacts.length > 0 && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  missing:{summary.missingArtifacts.slice(0, 6).join(",")}
                                </div>
                              )}
                              {summary.mustProduceArtifacts.length > 0 && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  must-produce:{summary.mustProduceArtifacts.slice(0, 6).join(",")}
                                </div>
                              )}
                              {summary.doNotRepeat.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                  {summary.doNotRepeat.slice(0, 3).map((item) => (
                                    <span key={item}>do-not:{item}</span>
                                  ))}
                                </div>
                              )}
                              {summary.stderrExcerpt && (
                                <div className="mt-1 break-words text-[10px]">
                                  stderr:{summary.stderrExcerpt.slice(0, 180)}
                                </div>
                              )}
                              {summary.stdoutExcerpt && (
                                <div className="mt-1 break-words text-[10px]">
                                  stdout:{summary.stdoutExcerpt.slice(0, 180)}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = replayPlanSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Replay status: {summary.replayStatus}</span>
                                {summary.provider && <span>provider:{summary.provider}</span>}
                                {summary.turnId && <span>turn:{summary.turnId}</span>}
                                {summary.promptSource && (
                                  <span>prompt:{summary.promptSource}</span>
                                )}
                                {summary.promptTransport && (
                                  <span>transport:{summary.promptTransport}</span>
                                )}
                                {summary.timeoutSec > 0 && (
                                  <span>timeout:{summary.timeoutSec}s</span>
                                )}
                                <span>readonly:{String(summary.readonlyRequired)}</span>
                                <span>validates:{String(summary.validatesOutputs)}</span>
                                <span>hashes:{summary.hashCount}</span>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.taskBundleSha && (
                                  <span>task_bundle sha:{summary.taskBundleSha.slice(0, 12)}</span>
                                )}
                                {summary.executionInputSha && (
                                  <span>
                                    execution_input sha:{summary.executionInputSha.slice(0, 12)}
                                  </span>
                                )}
                                {summary.contractSha && (
                                  <span>contract sha:{summary.contractSha.slice(0, 12)}</span>
                                )}
                              </div>
                              {summary.cwd && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  cwd:{summary.cwd}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {(() => {
                          const summary = executionInputSummary(artifactContent);
                          if (!summary) return null;
                          return (
                            <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span>Execution input</span>
                                {summary.provider && <span>provider:{summary.provider}</span>}
                                {summary.turnId && <span>turn:{summary.turnId}</span>}
                                {summary.promptTransport && (
                                  <span>transport:{summary.promptTransport}</span>
                                )}
                                {summary.promptTransportReason && (
                                  <span>reason:{summary.promptTransportReason}</span>
                                )}
                                {summary.timeoutSec > 0 && (
                                  <span>timeout:{summary.timeoutSec}s</span>
                                )}
                                <span>stdin redacted:{String(summary.stdinRedacted)}</span>
                                {summary.readonlyEnv && (
                                  <span>readonly env:{summary.readonlyEnv}</span>
                                )}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                {summary.stdinSha && (
                                  <span>stdin sha:{summary.stdinSha.slice(0, 12)}</span>
                                )}
                                {summary.outputContractSha && (
                                  <span>
                                    contract sha:{summary.outputContractSha.slice(0, 12)}
                                  </span>
                                )}
                              </div>
                              {summary.cwd && (
                                <div className="mt-1 break-words font-data text-[10px]">
                                  cwd:{summary.cwd}
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {artifactContent.content_redacted ? (
                          <p className="mt-2 rounded bg-surface-container p-2 text-[11px] text-warning">
                            Artifact content is redacted and hidden from inline preview.
                          </p>
                        ) : artifactContent.is_text ? (
                          <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-container p-2 font-data text-[10px] text-on-surface">
                            {artifactContent.content}
                          </pre>
                        ) : (
                          <p className="mt-2 text-[11px] text-on-surface-variant">
                            Binary artifact content is not rendered inline.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {workflowExecution && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    工作流: {workflowExecution.status} / steps{" "}
                    {workflowExecution.step_results.length} / outputs{" "}
                    {workflowExecution.outputs?.length ?? 0}
                    {workflowExecution.audit_summary && (
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          agent:{workflowExecution.audit_summary.agent_step_count ?? 0}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          invalid:{workflowExecution.audit_summary.invalid_steps ?? 0}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          errors:{workflowExecution.audit_summary.error_steps ?? 0}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          lifecycle:
                          {workflowExecution.audit_summary.agent_lifecycle_artifacts?.length ?? 0}
                        </span>
                        {workflowExecution.audit_summary.failure_kinds?.length ? (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            failure:{workflowExecution.audit_summary.failure_kinds.join(",")}
                          </span>
                        ) : null}
                        {workflowExecution.audit_summary.missing_artifacts?.length ? (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            missing:{workflowExecution.audit_summary.missing_artifacts.join(",")}
                          </span>
                        ) : null}
                      </div>
                    )}
                    {workflowExecution.rerun_plan && (
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          rerun:{workflowExecution.rerun_plan.status ?? "unknown"}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          rerun-steps:{workflowExecution.rerun_plan.steps?.length ?? 0}
                        </span>
                        {(workflowExecution.rerun_plan.blocked_outputs?.length ?? 0) > 0 ? (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            blocked-outputs:
                            {workflowExecution.rerun_plan.blocked_outputs?.length ?? 0}
                          </span>
                        ) : null}
                      </div>
                    )}
                    {workflowExecution.evidence_materialization && (
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        <span
                          className={`rounded bg-surface px-1.5 py-0.5 ${
                            workflowExecution.evidence_materialization.status === "ok"
                              ? ""
                              : "text-warning"
                          }`}
                        >
                          evidence:{workflowExecution.evidence_materialization.status}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          evidence-items:
                          {workflowExecution.evidence_materialization.evidence_count}
                        </span>
                        {workflowExecution.evidence_materialization.rejected_outputs
                          .length > 0 ? (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            rejected:
                            {workflowExecution.evidence_materialization.rejected_outputs.length}
                          </span>
                        ) : null}
                      </div>
                    )}
                    {workflowExecution.semantic_output_import && (
                      <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                        <span
                          className={`rounded bg-surface px-1.5 py-0.5 ${
                            workflowExecution.semantic_output_import.status === "ok" ||
                            workflowExecution.semantic_output_import.status === "skipped"
                              ? ""
                              : "text-warning"
                          }`}
                        >
                          semantics:{workflowExecution.semantic_output_import.status ?? "unknown"}
                        </span>
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          semantic-cases:
                          {workflowExecution.semantic_output_import.imported_count}
                        </span>
                        {workflowExecution.semantic_output_import.rejected_count > 0 ? (
                          <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                            rejected:{workflowExecution.semantic_output_import.rejected_count}
                          </span>
                        ) : null}
                      </div>
                    )}
                    {(workflowExecution.outputs?.length ?? 0) > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {workflowExecution.outputs?.map((output, index) => (
                          <span
                            key={`${String(output.id ?? "output")}-${index}`}
                            className="rounded bg-surface px-1.5 py-0.5 font-data text-[10px]"
                          >
                            {String(output.id ?? "output")}:
                            {String(output.status ?? "unknown")}
                          </span>
                        ))}
                      </div>
                    )}
                    {workflowExecution.step_results.length > 0 && (
                      <div className="mt-1 space-y-1">
                        {workflowExecution.step_results.map((step, index) => {
                          const diagnostics = step.provider_diagnostics;
                          const recovery = step.failure_recovery;
                          const recoveryDiagnostics = recovery?.provider_diagnostics;
                          const displayedDiagnostics = diagnostics ?? recoveryDiagnostics;
                          const firstAttempt = recoveryDiagnostics?.attempts?.[0];
                          if (!displayedDiagnostics && !recovery) return null;
                          return (
                            <div
                              key={`${String(step.step_id ?? "step")}-${index}`}
                              className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                            >
                              {displayedDiagnostics && (
                                <>
                                  <span className="text-on-surface">
                                    {String(step.step_id ?? "step")} provider:
                                    {displayedDiagnostics.provider || String(step.provider ?? "")}
                                  </span>
                                  <span className="ml-1">
                                    health:{displayedDiagnostics.health_status || "unknown"}
                                  </span>
                                </>
                              )}
                              {!displayedDiagnostics && (
                                <span className="text-on-surface">
                                  {String(step.step_id ?? "step")}
                                </span>
                              )}
                              {displayedDiagnostics?.prompt_transport && (
                                <span className="ml-1">
                                  transport:{displayedDiagnostics.prompt_transport}
                                </span>
                              )}
                              {displayedDiagnostics?.command_resolution_source && (
                                <span className="ml-1">
                                  command:{displayedDiagnostics.command_resolution_source}
                                </span>
                              )}
                              {displayedDiagnostics?.command_resolution_used_fallback && (
                                <span className="ml-1 text-warning">fallback</span>
                              )}
                              {displayedDiagnostics?.command_resolution_reason && (
                                <span className="ml-1">
                                  reason:{displayedDiagnostics.command_resolution_reason}
                                </span>
                              )}
                              {displayedDiagnostics?.startup_probe_endpoint && (
                                <span className="ml-1 break-all">
                                  probe:{displayedDiagnostics.startup_probe_endpoint}
                                </span>
                              )}
                              {recovery && (
                                <div className="mt-1 text-warning">
                                  <span>recovery:{recovery.failure_kind || "unknown"}</span>
                                  {recovery.validation_status && (
                                    <span className="ml-1">
                                      validation:{recovery.validation_status}
                                    </span>
                                  )}
                                  {recovery.missing_artifacts?.length ? (
                                    <span className="ml-1">
                                      missing:{recovery.missing_artifacts.join(",")}
                                    </span>
                                  ) : null}
                                  {recovery.suggested_actions?.[0] && (
                                    <span className="ml-1">
                                      next:{recovery.suggested_actions[0]}
                                    </span>
                                  )}
                                  {recoveryDiagnostics?.configured_command_text && (
                                    <span className="ml-1 break-all">
                                      configured:{recoveryDiagnostics.configured_command_text}
                                    </span>
                                  )}
                                  {firstAttempt && (
                                    <span className="ml-1 break-all">
                                      attempt:{firstAttempt.command || firstAttempt.executable || "agent"}=
                                      {firstAttempt.status || "unknown"}
                                      {firstAttempt.reason ? `:${firstAttempt.reason}` : ""}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
                {workflowOutputMaterialize && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    <p>
                      Output evidence: {workflowOutputMaterialize.status} /{" "}
                      {workflowOutputMaterialize.evidence_count} items
                      {workflowOutputMaterialize.rejected_outputs.length > 0 && (
                        <span className="ml-2 text-warning">
                          rejected {workflowOutputMaterialize.rejected_outputs.length}
                        </span>
                      )}
                    </p>
                    {workflowOutputMaterialize.rejected_outputs.length > 0 && (
                      <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                        {workflowOutputMaterialize.rejected_outputs
                          .slice(0, 4)
                          .map((item, index) => (
                            <div
                              key={`${rejectedOutputLabel(item)}:${index}`}
                              className="break-words"
                            >
                              {rejectedOutputLabel(item)} rejected:
                              {rejectedOutputReason(item)}
                            </div>
                          ))}
                        {workflowOutputMaterialize.rejected_outputs.length > 4 && (
                          <div>
                            +{workflowOutputMaterialize.rejected_outputs.length - 4} more
                          </div>
                        )}
                      </div>
                    )}
                    {(() => {
                      const outputs = materializationAuditOutputs(workflowOutputMaterialize);
                      if (outputs.length === 0) return null;
                      return (
                        <div className="mt-1 space-y-0.5 font-data text-[10px]">
                          {outputs.slice(0, 4).map((item) => (
                            <div
                              key={item.outputId}
                              className={
                                item.materializationStatus === "accepted"
                                  ? "text-on-surface"
                                  : item.materializationStatus === "partial"
                                    ? "text-warning"
                                    : "text-on-surface-variant"
                              }
                            >
                              {item.outputId}:{item.materializationStatus || "unknown"}
                              {item.artifact ? ` artifact:${item.artifact}` : ""}
                              {item.materializedCount
                                ? ` evidence:${item.materializedCount}`
                                : ""}
                              {item.rejectedCount ? ` rejected:${item.rejectedCount}` : ""}
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                  </div>
                )}
                {semanticOutputImport && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    <p>
                      Semantic import: {semanticOutputImport.status ?? "unknown"} /{" "}
                      {semanticOutputImport.imported_count} imported
                      {semanticOutputImport.rejected_count > 0 && (
                        <span className="ml-2 text-warning">
                          rejected {semanticOutputImport.rejected_count}
                        </span>
                      )}
                    </p>
                    {semanticOutputImport.source_ref && (
                      <p className="mt-1 break-words font-data text-[10px]">
                        source:{semanticOutputImport.source_ref}
                      </p>
                    )}
                    {semanticOutputImport.rejected.length > 0 && (
                      <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                        {semanticOutputImport.rejected.slice(0, 4).map((item, index) => (
                          <div
                            key={`${String(item.output ?? item.case_id ?? "case")}:${index}`}
                            className="break-words"
                          >
                            {String(item.output ?? item.case_id ?? "case")} rejected:
                            {item.reason}
                          </div>
                        ))}
                        {semanticOutputImport.rejected.length > 4 && (
                          <div>+{semanticOutputImport.rejected.length - 4} more</div>
                        )}
                      </div>
                    )}
                  </div>
                )}
                <div className="mt-3 space-y-2">
                  {preparedRun.agent_runs.map((agentRun) => {
                    const stepId = agentRun.step_id;
                    const result = executionResults[stepId];
                    const validation = validationResults[stepId];
                    const materialized = materializeResults[stepId];
                    const isExecuting = busyAction === `execute-${stepId}`;
                    const isValidating = busyAction === `validate-${stepId}`;
                    const isMaterializing = busyAction === `materialize-${stepId}`;
                    const requiredArtifacts = agentRun.required_artifacts ?? [];
                    return (
                      <div
                        key={agentRun.run_id}
                        className="rounded-md border border-outline-variant/30 bg-surface-container px-2.5 py-2"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-medium text-on-surface">{stepId}</p>
                            <p className="break-words font-data text-[11px] text-on-surface-variant">
                              {agentRun.provider} / {agentRun.run_id}
                            </p>
                          </div>
                          <button
                            onClick={() => executePreparedAgentRun(stepId)}
                            disabled={isExecuting}
                            className="inline-flex items-center gap-1.5 rounded bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                          >
                            {isExecuting ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <PlayCircle size={12} />
                            )}
                            Execute
                          </button>
                          <button
                            onClick={() =>
                              validatePreparedAgentRun(stepId, requiredArtifacts)
                            }
                            disabled={isValidating || requiredArtifacts.length === 0}
                            className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                          >
                            {isValidating ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <Search size={12} />
                            )}
                            Validate
                          </button>
                          <button
                            onClick={() =>
                              materializePreparedAgentRun(stepId, requiredArtifacts)
                            }
                            disabled={isMaterializing || requiredArtifacts.length === 0}
                            className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                          >
                            {isMaterializing ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <Database size={12} />
                            )}
                            Materialize
                          </button>
                        </div>
                        {requiredArtifacts.length > 0 && (
                            <p className="mt-1 text-on-surface-variant">
                              必需产物: {requiredArtifacts.join(", ")}
                            </p>
                        )}
                        {result && (
                          <div className="mt-2 space-y-1 text-on-surface-variant">
                            <div className="flex flex-wrap gap-2">
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                {result.status}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                exit {result.exit_code ?? "-"}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                {result.duration_ms}ms
                              </span>
                            </div>
                            {result.provider_diagnostics && (
                              <div className="rounded bg-surface px-1.5 py-1 font-data text-[10px]">
                                <span className="text-on-surface">
                                  provider:
                                  {result.provider_diagnostics.provider || agentRun.provider}
                                </span>
                                <span className="ml-1">
                                  health:
                                  {result.provider_diagnostics.health_status || "unknown"}
                                </span>
                                {result.provider_diagnostics.prompt_transport && (
                                  <span className="ml-1">
                                    transport:{result.provider_diagnostics.prompt_transport}
                                  </span>
                                )}
                                {result.provider_diagnostics.command_resolution_source && (
                                  <span className="ml-1">
                                    command:{result.provider_diagnostics.command_resolution_source}
                                  </span>
                                )}
                                {result.provider_diagnostics.command_resolution_used_fallback && (
                                  <span className="ml-1 text-warning">fallback</span>
                                )}
                                {result.provider_diagnostics.command_resolution_reason && (
                                  <span className="ml-1">
                                    reason:{result.provider_diagnostics.command_resolution_reason}
                                  </span>
                                )}
                                {result.provider_diagnostics.startup_probe_endpoint && (
                                  <span className="ml-1 break-all">
                                    probe:{result.provider_diagnostics.startup_probe_endpoint}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                        {validation && (
                          <div className="mt-2 rounded bg-surface px-2 py-1.5 text-on-surface-variant">
                            <p>
                              Validation: {validation.status} /{" "}
                              {validation.provenance_status}
                            </p>
                            {validation.accepted_artifact_details?.length ? (
                              <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                {validation.accepted_artifact_details.slice(0, 3).map((item) => (
                                  <div key={String(item.artifact ?? item.path ?? item.sha256)}>
                                    {String(item.artifact ?? "artifact")} sha:
                                    {String(item.sha256 ?? "").slice(0, 12)}
                                  </div>
                                ))}
                              </div>
                            ) : null}
                            {validation.rejected_artifacts.length > 0 && (
                              <p className="mt-1 text-amber-400">
                                Rejected: {validation.rejected_artifacts.length}
                              </p>
                            )}
                            {validation.rejected_artifact_details?.length ? (
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {validation.rejected_artifact_details.slice(0, 3).map((item) => (
                                  <div key={`${String(item.artifact ?? "artifact")}:${String(item.reason ?? "rejected")}`}>
                                    {String(item.artifact ?? "artifact")} rejected:
                                    {String(item.reason ?? "unknown")}
                                  </div>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        )}
                        {materialized && (
                          <div className="mt-2 rounded bg-surface px-2 py-1.5 text-on-surface-variant">
                            <p>
                              Evidence: {materialized.status} /{" "}
                              {materialized.evidence_count} items
                            </p>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {taskRuns.length > 0 && (
              <div className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs">
                <p className="mb-2 font-medium text-on-surface">最近任务运行</p>
                <div className="space-y-2">
                  {taskRuns.map((run) => (
                    <button
                      key={run.task_run_id}
                      onClick={() => restoreExistingTaskRun(run.task_run_id)}
                      disabled={busyAction === `restore-task-run-${run.task_run_id}`}
                      className={`block w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-container-high disabled:opacity-50 ${
                        preparedRun?.task_run_id === run.task_run_id
                          ? "bg-surface-container-high"
                          : "bg-surface-container"
                      }`}
                    >
                      <span className="block font-medium text-on-surface">
                        {run.workflow_id}
                      </span>
                      <span className="block break-words font-data text-[11px] text-on-surface-variant">
                        {busyAction === `restore-task-run-${run.task_run_id}`
                          ? "restoring..."
                          : run.task_run_id}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Panel>
      )}

      {activeWorkbenchView === "knowledge" && (
        <>
        <Panel title="测试语义库" icon={<Library size={16} />}>
          <div className="space-y-3">
            <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">特性</span>
                  <input
                    aria-label="Semantic feature"
                    value={semanticFeature}
                    onChange={(event) => setSemanticFeature(event.target.value)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">模块</span>
                  <input
                    aria-label="Semantic module"
                    value={semanticModule}
                    onChange={(event) => setSemanticModule(event.target.value)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  />
                </label>
              </div>
              <label className="mt-2 block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  已有用例，每行一个
                </span>
                <textarea
                  aria-label="Semantic case lines"
                  value={semanticLines}
                  onChange={(event) => setSemanticLines(event.target.value)}
                  className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                />
              </label>
              <button
                onClick={buildSemanticCasesFromText}
                disabled={busyAction === "build-semantic-cases" || !semanticLines.trim()}
                className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
              >
                {busyAction === "build-semantic-cases" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Library size={14} />
                )}
                生成语义 JSON
              </button>
            </div>
            <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  type="file"
                  accept=".json,.jsonl,.ndjson,.csv,.txt,.md"
                  aria-label="Semantic case file"
                  onChange={(event) => setSemanticFile(event.target.files?.[0] ?? null)}
                  className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface file:mr-3 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                />
                <button
                  onClick={importSemanticCaseFile}
                  disabled={busyAction === "import-semantic-file" || !semanticFile}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                >
                  {busyAction === "import-semantic-file" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Save size={14} />
                  )}
                  导入文件
                </button>
              </div>
              {semanticFile && (
                <p className="mt-2 break-all font-data text-[11px] text-on-surface-variant">
                  {semanticFile.name}
                </p>
              )}
            </div>
            <textarea
              value={semanticJson}
              onChange={(event) => setSemanticJson(event.target.value)}
              className="h-44 max-h-[46vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
              aria-label="Semantic JSON"
              spellCheck={false}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                onClick={importSemanticCase}
                disabled={busyAction === "import-semantic-case"}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                <Save size={14} />
                导入用例
              </button>
              <input
                value={semanticQuery}
                onChange={(event) => setSemanticQuery(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                aria-label="Semantic search query"
              />
              <button
                onClick={searchSemanticCases}
                disabled={busyAction === "search-semantic-cases" || !semanticQuery.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
              >
                <Search size={14} />
                搜索
              </button>
            </div>
            <div className="space-y-2">
              {semanticResults.map((item) => (
                <div
                  key={item.semantic_id}
                  className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                >
                  <p className="font-medium text-on-surface">{item.case_id}</p>
                  <p className="mt-1 text-on-surface-variant">{item.scenario}</p>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel title="证据库" icon={<Database size={16} />}>
          <div className="space-y-3">
            <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">证据主题</span>
                  <input
                    aria-label="Evidence subject"
                    value={manualEvidenceSubject}
                    onChange={(event) => setManualEvidenceSubject(event.target.value)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">源码路径</span>
                  <input
                    aria-label="Evidence path"
                    value={manualEvidencePath}
                    onChange={(event) => setManualEvidencePath(event.target.value)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-sm text-on-surface outline-none focus:border-primary"
                  />
                </label>
              </div>
              <label className="mt-2 block">
                <span className="mb-1 block text-xs text-on-surface-variant">证据说明</span>
                <textarea
                  aria-label="Evidence text"
                  value={manualEvidenceText}
                  onChange={(event) => setManualEvidenceText(event.target.value)}
                  className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                />
              </label>
              <button
                onClick={saveManualEvidence}
                disabled={
                  busyAction === "save-manual-evidence" ||
                  !manualEvidenceSubject.trim() ||
                  !workspaceId.trim() ||
                  !repoPath.trim()
                }
                className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
              >
                {busyAction === "save-manual-evidence" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                保存证据
              </button>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={memoryQuery}
                onChange={(event) => setMemoryQuery(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                aria-label="Evidence search query"
              />
              <button
                onClick={searchMemory}
                disabled={busyAction === "search-memory" || !memoryQuery.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                <Search size={14} />
                搜索证据
              </button>
            </div>
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-400">
              <div className="flex items-start gap-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>
                  证据库只保存结构化事实；Agent 原始输出会作为产物上下文保存，不会直接当作事实复用。
                </span>
              </div>
            </div>
            <div className="space-y-2">
              {memoryResults.map((item) => (
                <div
                  key={item.evidence_id}
                  className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                      {item.kind}
                    </span>
                    <span className="font-medium text-on-surface">{item.subject_key}</span>
                    <span className="text-on-surface-variant">{item.status}</span>
                    {item.source_read_status && (
                      <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                        source:{item.source_read_status}
                      </span>
                    )}
                    {item.usable_as_source_evidence !== undefined && (
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          item.usable_as_source_evidence
                            ? "bg-green-400/10 text-green-500"
                            : "bg-amber-400/10 text-amber-500"
                        }`}
                      >
                        usable:{String(item.usable_as_source_evidence)}
                      </span>
                    )}
                  </div>
                  {item.path && (
                    <p className="mt-1 break-words font-data text-on-surface-variant">
                      {item.path}
                    </p>
                  )}
                  {item.reason && (
                    <p className="mt-1 text-on-surface-variant">{item.reason}</p>
                  )}
                  {(() => {
                    const refs = evidenceAuditRefs(item.provenance ?? {});
                    if (refs.length === 0) return null;
                    return (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                        <div className="flex flex-wrap gap-1.5 font-data text-[10px] text-on-surface-variant">
                          {refs.map((ref) => (
                            <span
                              key={`${ref.label}:${ref.artifact}`}
                              className="rounded bg-surface px-1.5 py-0.5"
                              title={ref.sha256 ? `${ref.artifact} sha:${ref.sha256}` : ref.artifact}
                            >
                              {ref.label}: {ref.artifact}
                              {ref.sha256 ? ` sha:${ref.sha256.slice(0, 12)}` : ""}
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })()}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => loadMemorySlices(item.evidence_id)}
                      disabled={busyAction === `memory-slices-${item.evidence_id}`}
                      className="inline-flex items-center gap-1 rounded bg-surface-container px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {busyAction === `memory-slices-${item.evidence_id}` ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <ClipboardList size={12} />
                      )}
                      源码切片
                    </button>
                    {memorySlices[item.evidence_id] && (
                      <span className="font-data text-[11px] text-on-surface-variant">
                        {memorySlices[item.evidence_id].length} slice(s)
                      </span>
                    )}
                  </div>
                  {memorySlices[item.evidence_id] && memorySlices[item.evidence_id].length > 0 && (
                    <div className="mt-2 space-y-2 text-on-surface-variant">
                      {memorySlices[item.evidence_id].slice(0, 3).map((slice) => (
                        <div
                          key={slice.slice_id}
                          className="rounded bg-surface-container px-2 py-1.5"
                        >
                          <p className="break-words font-data text-[11px]">
                            {slice.file_path}:{slice.start_line}-{slice.end_line} sha:
                            {slice.sha256.slice(0, 12)}
                            {slice.integrity_status && (
                              <span
                                className={`ml-1 ${
                                  slice.integrity_status === "verified_current"
                                    ? "text-green-500"
                                    : "text-warning"
                                }`}
                              >
                                {slice.integrity_status}
                              </span>
                            )}
                          </p>
                          {(slice.current_sha256 || slice.validation_error) && (
                            <p className="mt-1 break-words font-data text-[10px] text-warning">
                              {slice.current_sha256
                                ? `current:${slice.current_sha256.slice(0, 12)} `
                                : ""}
                              {slice.validation_error || ""}
                            </p>
                          )}
                          <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-data text-[10px] text-on-surface">
                            {slice.excerpt}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                  {item.source_slices && item.source_slices.length > 0 && !memorySlices[item.evidence_id] && (
                    <div className="mt-2 space-y-1 text-on-surface-variant">
                      {item.source_slices.slice(0, 3).map((slice) => (
                        <p key={slice.slice_id} className="break-words font-data text-[11px]">
                          slice {slice.file_path}:{slice.start_line}-{slice.end_line} sha:
                          {slice.sha256.slice(0, 12)}
                          {slice.integrity_status && (
                            <span
                              className={`ml-1 ${
                                slice.integrity_status === "verified_current"
                                  ? "text-green-500"
                                  : "text-warning"
                              }`}
                            >
                              {slice.integrity_status}
                            </span>
                          )}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </Panel>
        </>
      )}
        </motion.div>
      </AnimatePresence>
      </div>
  );
}

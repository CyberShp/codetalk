"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  EvidenceMemoryItem,
  EvidenceSourceSlice,
  ExternalAgentStartupProbeResult,
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
  WorkflowDefinition,
  WorkflowExecutionResult,
  WorkflowPreset,
  WorkbenchProviderCapabilitiesMatrix,
  WorkbenchTaskArtifact,
  WorkbenchTaskArtifactContent,
  WorkbenchTaskArtifactManifest,
} from "@/lib/types";

const DEFAULT_WORKFLOW = {
  id: "mr-blackbox-workflow",
  name: "MR Black-box Test Workflow",
  version: 1,
  inputs: [
    {
      id: "mr_link",
      type: "mr_link",
      required: true,
      resolver: "agent_mcp",
      role: "MR source resolved by the Agent CLI MCP credentials",
    },
    { id: "design_doc", type: "file", required: false, role: "design context" },
    { id: "coverage_report", type: "coverage_report", required: false },
  ],
  steps: [
    {
      id: "agent_collect_mr",
      type: "agent_task",
      provider: "claude-code",
      mcp_profile: "codehub-mcp",
      goal: "Collect MR diff and produce verifiable artifacts. Do not edit files.",
      required_artifacts: ["mr_snapshot.json", "diff.patch", "changed_files.json"],
    },
    { id: "validate_evidence", type: "evidence_validate" },
    { id: "render_black_box_cases", type: "report_render" },
  ],
  outputs: [
    { id: "mr_scope", type: "scope_report", from: "validate_evidence" },
    { id: "black_box_cases", type: "test_cases", from: "render_black_box_cases" },
  ],
};

const DEFAULT_INPUTS = {
  mr_link: "https://codehub.example.local/group/project/-/merge_requests/1",
  design_doc: "",
  coverage_report: "",
};

const WORKFLOW_BUILDER_SCENARIOS = {
  module_analysis: {
    name: "Module Analysis",
    inputs: "analysis_object:free_text, design_doc:file, coverage_report:coverage_report",
    outputs: "source_scope:scope_report, risk_findings:json, test_cases:test_cases",
    goal: "Analyze the requested module, validate source scope, identify risk paths, and produce black-box oriented test cases.",
    artifacts: "source_scope.json, risk_findings.json, black_box_cases.json",
  },
  issue_hunt: {
    name: "Resource / Exception Hunt",
    inputs: "analysis_object:free_text, issue_type:free_text, design_doc:file",
    outputs: "issue_candidates:json, repro_paths:json, test_cases:test_cases",
    goal: "Find resource leaks or exception-branch defects matching the requested issue type, with verifiable source evidence and observable tests.",
    artifacts: "issue_candidates.json, repro_paths.json, black_box_cases.json",
  },
  mr_blackbox: {
    name: "MR Black-box Tests",
    inputs: "mr_link:mr_link, design_doc:file, coverage_report:coverage_report",
    outputs: "mr_scope:scope_report, changed_behavior:json, black_box_cases:test_cases",
    goal: "Use Agent-owned MCP credentials to read the MR, identify changed behavior and affected scope, then produce black-box test cases.",
    artifacts: "mr_snapshot.json, diff.patch, changed_files.json, black_box_cases.json",
  },
  patch_impact: {
    name: "Patch Impact Plan",
    inputs: "patch_file:patch, design_doc:file, analysis_object:free_text",
    outputs: "before_after_flow:markdown, impact_scope:scope_report, test_cases:test_cases",
    goal: "Read the patch proposal, compare before/after flow, validate impact scope, and produce implementation and test recommendations.",
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
  const safeFeature = feature.trim() || "Imported Feature";
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
  firstRejected?: {
    output: string;
    reason: string;
    status: string;
    schemaErrorCount: number;
  };
};

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
  return {
    evidenceCount: Number(payload.evidence_count ?? 0) || 0,
    rejectedCount: rejectedOutputs.length,
    workflowOutputsSha: String(workflowOutputsArtifact.sha256 ?? ""),
    outputCount: Number(workflowOutputsArtifact.output_count ?? 0) || 0,
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

const AUDIT_ARTIFACT_KIND_ORDER = [
  "task_bundle",
  "input_snapshot",
  "input_context",
  "input_file_metadata",
  "input_file_set_manifest",
  "input_parsed_text",
  "input_chunks",
  "input_original_file",
  "input_artifact",
  "agent_task_bundle",
  "agent_provider_diagnostics",
  "agent_run_lifecycle",
  "agent_failure_recovery",
  "agent_turn_task_bundle",
  "agent_turn_provider_diagnostics",
  "agent_turn_execution_input",
  "agent_turn_execution_result",
  "agent_turn_source_slice_requests",
  "agent_turn_source_slices",
  "agent_turn_raw_output",
  "agent_turn_run",
  "agent_instructions",
  "provider_snapshot",
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
  "workflow_output_materialization",
  "workflow_execution",
  "task_rerun_plan",
  "task_rerun_execution",
  "task_rerun_history",
];

function prioritizedAuditArtifacts(artifacts: WorkbenchTaskArtifact[]): WorkbenchTaskArtifact[] {
  return [...artifacts].sort((left, right) => {
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
    <section className="rounded-xl border border-outline-variant/20 bg-surface-container p-5">
      <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-on-surface">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function AgentWorkbenchPage() {
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [workflowPresets, setWorkflowPresets] = useState<WorkflowPreset[]>([]);
  const [workflowJson, setWorkflowJson] = useState(pretty(DEFAULT_WORKFLOW));
  const [builderScenario, setBuilderScenario] =
    useState<keyof typeof WORKFLOW_BUILDER_SCENARIOS>("mr_blackbox");
  const [builderWorkflowId, setBuilderWorkflowId] = useState("custom_mr_blackbox");
  const [builderWorkflowName, setBuilderWorkflowName] = useState("Custom MR black-box workflow");
  const [builderInputSpec, setBuilderInputSpec] = useState(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.inputs,
  );
  const [builderOutputSpec, setBuilderOutputSpec] = useState(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.outputs,
  );
  const [builderProvider, setBuilderProvider] = useState("claude-code");
  const [builderMcpProfile, setBuilderMcpProfile] = useState("codehub-mcp");
  const [builderGoal, setBuilderGoal] = useState(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.goal,
  );
  const [builderArtifacts, setBuilderArtifacts] = useState(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox.artifacts,
  );
  const [builderOutputSchemas, setBuilderOutputSchemas] = useState(
    pretty(DEFAULT_BUILDER_OUTPUT_SCHEMAS),
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
  const [memoryResults, setMemoryResults] = useState<EvidenceMemoryItem[]>([]);
  const [memorySlices, setMemorySlices] = useState<Record<string, EvidenceSourceSlice[]>>({});
  const [providerMatrix, setProviderMatrix] =
    useState<WorkbenchProviderCapabilitiesMatrix | null>(null);
  const [providerProbeResults, setProviderProbeResults] = useState<
    Record<string, ExternalAgentStartupProbeResult>
  >({});
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
  const [workflowOutputMaterialize, setWorkflowOutputMaterialize] =
    useState<MaterializeWorkflowOutputsResult | null>(null);
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
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const workflowOptions = useMemo(
    () => workflows.map((workflow) => workflow.id),
    [workflows],
  );
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
  const parsedPrepareInputs = useMemo(() => {
    try {
      return parseJsonObject(inputsJson || "{}");
    } catch {
      return {};
    }
  }, [inputsJson]);

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [workflowData, taskRunData, providerData] = await Promise.all([
        api.workbench.workflows.list(),
        api.workbench.taskRuns.list({ limit: 10 }),
        api.workbench.providerCapabilities(),
      ]);
      const presetData = await api.workbench.workflows.presets();
      setWorkflows(workflowData);
      setWorkflowPresets(presetData.items);
      setProviderMatrix(providerData);
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

  function applyBuilderScenario(scenarioId: keyof typeof WORKFLOW_BUILDER_SCENARIOS) {
    const scenario = WORKFLOW_BUILDER_SCENARIOS[scenarioId];
    setBuilderScenario(scenarioId);
    setBuilderWorkflowName(`Custom ${scenario.name}`);
    setBuilderInputSpec(scenario.inputs);
    setBuilderOutputSpec(scenario.outputs);
    setBuilderGoal(scenario.goal);
    setBuilderArtifacts(scenario.artifacts);
  }

  function generateWorkflowFromBuilder() {
    const workflowId = builderWorkflowId.trim();
    const workflowName = builderWorkflowName.trim();
    if (!workflowId || !workflowName) {
      throw new Error("Workflow builder requires workflow id and name");
    }
    const inputs = parseWorkflowSpecList(builderInputSpec, "free_text").map((input) => ({
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
          ? "remote change source resolved by Agent CLI MCP credentials"
          : "user-provided workflow input",
    }));
    const requiredArtifacts = parseCommaSeparated(builderArtifacts);
    const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
    const outputs = parseWorkflowSpecList(builderOutputSpec, "json").map((output) => {
      const artifact =
        output.artifact || outputArtifactForSpec(output.id, output.type, requiredArtifacts);
      const from = artifact ? "agent_collect" : "render_report";
      const schema = output.type === "json" ? outputSchemaForSpec(output.id, outputSchemas) : null;
      return {
        id: output.id,
        type: output.type,
        from,
        ...(artifact ? { artifact } : {}),
        ...(schema ? { schema } : {}),
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
    setMessage(`Workflow draft generated: ${workflow.id}`);
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
          ? `Workflow saved: ${saved.id} (${warningCount} audit warning(s))`
          : `Workflow saved: ${saved.id}`,
      );
      await loadWorkflows();
    });

  const applyPreset = () => {
    const preset = workflowPresets.find((item) => item.id === selectedPresetId);
    if (!preset) return;
    setWorkflowJson(pretty(preset.definition));
    setSelectedWorkflowId(preset.definition.id);
    setMessage(`Preset applied: ${preset.name}`);
  };

  const installPreset = () =>
    runAction("install-preset", async () => {
      if (!selectedPresetId) return;
      const workflow = await api.workbench.workflows.installPreset(selectedPresetId);
      setWorkflowJson(pretty(workflow));
      setSelectedWorkflowId(workflow.id);
      setMessage(`Preset installed: ${workflow.id}`);
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
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`Task run prepared: ${result.task_run_id}`);
    });

  const runProviderStartupProbe = (provider: string) =>
    runAction(`provider-probe-${provider}`, async () => {
      const result = await api.tools.startupProbe(provider, repoPath.trim() || undefined);
      setProviderProbeResults((current) => ({ ...current, [provider]: result }));
      setMessage(`Startup probe ${result.status}: ${provider}`);
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

  const loadPreparedArtifacts = () =>
    runAction("load-artifacts", async () => {
      if (!preparedRun) return;
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`Artifacts loaded: ${preparedRun.task_run_id}`);
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
        setWorkflowExecution(result.execution);
        setTaskRerunPlan((result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null);
      }
      setTaskRerunPlanValidation(result.validation_after ?? null);
      setTaskRerunHistory(await api.workbench.taskRuns.rerunHistory(preparedRun.task_run_id));
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`Rerun execution ${result.execution?.status ?? result.status}: ${preparedRun.task_run_id}`);
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
      setSemanticOutputImport(null);
      setTaskRerunPlan((result.rerun_plan as TaskRerunPlan | undefined) ?? null);
      setTaskRerunPlanValidation(
        await api.workbench.taskRuns.rerunPlanValidation(preparedRun.task_run_id),
      );
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`Workflow execution ${result.status}: ${result.task_run_id}`);
      await loadWorkflows();
    });

  const materializePreparedWorkflowOutputs = () =>
    runAction("materialize-workflow-outputs", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.materializeOutputs(
        preparedRun.task_run_id,
      );
      setWorkflowOutputMaterialize(result);
      setMessage(`Workflow outputs materialized: ${result.evidence_count}`);
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
      setMessage(`Evidence materialized: ${result.evidence_count}`);
    });

  const importSemanticCase = () =>
    runAction("import-semantic-case", async () => {
      const payload = parseJsonValue(semanticJson);
      if (isBulkSemanticImportPayload(payload)) {
        const result = await api.workbench.semanticCases.importMany(payload);
        setMessage(
          `Semantic cases imported: ${result.imported_count}, rejected: ${result.rejected_count}`,
        );
        return;
      }
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error("Semantic import JSON must be an object or array");
      }
      const result = await api.workbench.semanticCases.create(
        payload as Record<string, unknown>,
      );
      setMessage(`Semantic case stored: ${result.case_id}`);
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
      setMessage(`Semantic import draft generated: ${count} cases`);
    });

  const searchSemanticCases = () =>
    runAction("search-semantic-cases", async () => {
      const result = await api.workbench.semanticCases.search({
        q: semanticQuery,
        limit: 10,
      });
      setSemanticResults(result.items);
      setMessage(`Semantic results: ${result.items.length}`);
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
        `Semantic file imported: ${result.imported_count}, rejected: ${result.rejected_count}`,
      );
      setSemanticFile(null);
    });

  const searchMemory = () =>
    runAction("search-memory", async () => {
      const result = await api.workbench.memory.search({
        q: memoryQuery,
        limit: 10,
      });
      setMemoryResults(result.items);
      setMemorySlices({});
      setMessage(`Memory results: ${result.items.length}`);
    });

  const loadMemorySlices = (evidenceId: string) =>
    runAction(`memory-slices-${evidenceId}`, async () => {
      const result = await api.workbench.memory.sourceSlices(evidenceId);
      setMemorySlices((current) => ({ ...current, [evidenceId]: result.items }));
      setMessage(`Source slices loaded: ${result.items.length}`);
    });

  return (
    <div className="w-full px-4 xl:px-6">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            Agent Workbench
          </h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Configure workflows, prepare Agent CLI runs, and audit evidence memory.
          </p>
        </div>
        <button
          onClick={() => void loadWorkflows()}
          disabled={loading}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
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

      <Panel title="Provider Matrix" icon={<AlertTriangle size={16} />}>
        <div className="grid gap-3 lg:grid-cols-3">
          {(providerMatrix?.providers ?? []).map((provider) => (
            <div
              key={provider.provider}
              className="rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-on-surface">
                    {provider.display_name || provider.provider}
                  </p>
                  <p className="font-data text-[11px] text-on-surface-variant">
                    {provider.provider}
                  </p>
                </div>
                <span className="shrink-0 rounded bg-surface-container px-2 py-0.5 font-data text-[10px] text-on-surface-variant">
                  {provider.status}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {provider.codetalk_callable && (
                  <span className="rounded bg-green-400/10 px-2 py-0.5 text-[11px] font-medium text-green-500">
                    CodeTalk callable
                  </span>
                )}
                {provider.agent_owned && (
                  <span className="rounded bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                    Agent-owned
                  </span>
                )}
                {!provider.codetalk_callable && !provider.agent_owned && (
                  <span className="rounded bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-500">
                    delegated or unavailable
                  </span>
                )}
              </div>
              <div className="mt-3 space-y-1 text-on-surface-variant">
                <p>
                  Owner:{" "}
                  <span className="font-data text-on-surface">{provider.owner}</span>
                </p>
                <p className="break-words">
                  Command:{" "}
                  <span className="font-data text-on-surface">
                    {provider.command.length > 0 ? provider.command.join(" ") : "n/a"}
                  </span>
                </p>
                <p>
                  MCP:{" "}
                  <span className="font-data text-on-surface">
                    {provider.capabilities.supports_mcp
                      ? provider.capabilities.mcp_profiles.length > 0
                        ? provider.capabilities.mcp_profiles.join(", ")
                        : "yes"
                      : "no"}
                  </span>
                </p>
                <p>
                  Artifacts/json:{" "}
                  <span className="font-data text-on-surface">
                    {provider.capabilities.supports_artifact_export ? "artifact" : "no-artifact"}
                    {" / "}
                    {provider.capabilities.supports_json_output ? "json" : "no-json"}
                  </span>
                </p>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {provider.capabilities.supports_source_discovery && (
                  <span className="rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    source discovery
                  </span>
                )}
                {provider.capabilities.supports_call_graph && (
                  <span className="rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    call graph
                  </span>
                )}
                {provider.capabilities.supports_source_slices && (
                  <span className="rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    source slices
                  </span>
                )}
                {provider.capabilities.supports_black_box_terms && (
                  <span className="rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                    black-box terms
                  </span>
                )}
              </div>
              {provider.credential_boundary && (
                <p className="mt-3 text-xs leading-5 text-on-surface-variant">
                  {provider.credential_boundary}
                </p>
              )}
              {provider.diagnostics && (
                <div className="mt-3 space-y-1 border-t border-outline-variant/30 pt-3 text-on-surface-variant">
                  {provider.diagnostics.startup_probe_endpoint && (
                    <p className="break-words">
                      Probe:{" "}
                      <span className="font-data text-on-surface">
                        {provider.diagnostics.startup_probe_endpoint}
                      </span>
                    </p>
                  )}
                  {provider.diagnostics.startup_probe_transport && (
                    <p>
                      Transport:{" "}
                      <span className="font-data text-on-surface">
                        {provider.diagnostics.startup_probe_transport}
                      </span>
                    </p>
                  )}
                  {provider.diagnostics.command_resolution && (
                    <div className="rounded bg-surface-container px-2 py-1.5">
                      <p>
                        Resolution:{" "}
                        <span className="font-data text-on-surface">
                          {provider.diagnostics.command_resolution.status || "unknown"}
                        </span>
                        {provider.diagnostics.command_resolution.used_fallback && (
                          <span className="ml-2 font-medium text-warning">fallback</span>
                        )}
                        {provider.diagnostics.command_resolution.launch_kind && (
                          <span className="ml-2 font-data text-on-surface">
                            launch:{provider.diagnostics.command_resolution.launch_kind}
                          </span>
                        )}
                      </p>
                      {provider.diagnostics.command_resolution.reason && (
                        <p className="mt-1 break-words">
                          Reason: {provider.diagnostics.command_resolution.reason}
                        </p>
                      )}
                      {typeof provider.diagnostics.command_resolution.attempt_count ===
                        "number" && (
                        <p className="mt-1">
                          Attempts:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.command_resolution.attempt_count}
                          </span>
                        </p>
                      )}
                    </div>
                  )}
                  {provider.diagnostics.probe_recipe && (
                    <div className="rounded bg-surface-container px-2 py-1.5">
                      <p className="font-medium text-on-surface">Probe recipe</p>
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
                          Backend command:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.backend_command}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.probe_recipe.command_env && (
                        <p className="mt-1 break-words">
                          Override env:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.command_env}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.probe_recipe.environment_checks?.length ? (
                        <p className="mt-1 break-words">
                          Check:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.probe_recipe.environment_checks.join(", ")}
                          </span>
                        </p>
                      ) : null}
                    </div>
                  )}
                  {provider.diagnostics.manual_probe_command && (
                    <p className="break-words">
                      Manual:{" "}
                      <span className="font-data text-on-surface">
                        {provider.diagnostics.manual_probe_command}
                      </span>
                    </p>
                  )}
                  {provider.diagnostics.troubleshooting?.[0] && (
                    <p className="leading-5">{provider.diagnostics.troubleshooting[0]}</p>
                  )}
                  {provider.diagnostics.startup_probe_endpoint && (
                    <button
                      onClick={() => runProviderStartupProbe(provider.provider)}
                      disabled={busyAction === `provider-probe-${provider.provider}`}
                      className="mt-2 inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {busyAction === `provider-probe-${provider.provider}` ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <PlayCircle size={13} />
                      )}
                      Startup probe
                    </button>
                  )}
                  {providerProbeResults[provider.provider] && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                      <p>
                        Probe result:{" "}
                        <span className="font-data text-on-surface">
                          {providerProbeResults[provider.provider].status}
                        </span>
                      </p>
                      <p className="mt-1 break-words">
                        {providerProbeResults[provider.provider].message}
                      </p>
                      {providerProbeResults[provider.provider].health?.launch_kind && (
                        <p className="mt-1">
                          Probe launch:{" "}
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
                          Probe attempts:{" "}
                          <span className="font-data text-on-surface">
                            {providerProbeResults[provider.provider].health?.attempts?.length}
                          </span>
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {!providerMatrix && (
            <p className="text-sm text-on-surface-variant">
              Provider diagnostics load with Workbench data.
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

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Panel title="Workflow Registry" icon={<ClipboardList size={16} />}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {workflowPresets.length > 0 && (
              <select
                value={selectedPresetId}
                onChange={(event) => setSelectedPresetId(event.target.value)}
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                aria-label="Workflow preset"
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
              Apply preset
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
              Install preset
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
              Save workflow
            </button>
            <span className="text-xs text-on-surface-variant">
              {workflows.length} registered
            </span>
          </div>
          <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface p-3">
            <div className="mb-3 flex flex-wrap items-end gap-2">
              <label className="min-w-48 flex-1">
                <span className="mb-1 block text-xs text-on-surface-variant">Scenario</span>
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
                Generate draft
              </button>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">Workflow ID</span>
                <input
                  value={builderWorkflowId}
                  onChange={(event) => setBuilderWorkflowId(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder id"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">Workflow name</span>
                <input
                  value={builderWorkflowName}
                  onChange={(event) => setBuilderWorkflowName(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder name"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">Provider</span>
                <input
                  value={builderProvider}
                  onChange={(event) => setBuilderProvider(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder provider"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">MCP profile</span>
                <input
                  value={builderMcpProfile}
                  onChange={(event) => setBuilderMcpProfile(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  aria-label="Workflow builder MCP profile"
                />
              </label>
            </div>
            <label className="mt-2 block">
              <span className="mb-1 block text-xs text-on-surface-variant">
                Inputs as id:type or id:type@resolver
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
                Outputs as id:type or id:type=artifact
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
                Required artifacts
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
                Output schemas JSON
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
              <span className="mb-1 block text-xs text-on-surface-variant">Agent goal</span>
              <textarea
                value={builderGoal}
                onChange={(event) => setBuilderGoal(event.target.value)}
                className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Workflow builder goal"
              />
            </label>
          </div>
          <textarea
            value={workflowJson}
            onChange={(event) => setWorkflowJson(event.target.value)}
            className="h-80 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
            aria-label="Workflow JSON"
            spellCheck={false}
          />
        </Panel>

        <Panel title="Prepare Task Run" icon={<PlayCircle size={16} />}>
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">Workflow</span>
              <select
                value={selectedWorkflowId}
                onChange={(event) => setSelectedWorkflowId(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              >
                {[selectedWorkflowId, ...workflowOptions]
                  .filter((value, index, values) => value && values.indexOf(value) === index)
                  .map((id) => (
                    <option key={id} value={id}>
                      {id}
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
                      Workflow audit warnings: {selectedWorkflowAudit.warnings.length}
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
              <span className="mb-1 block text-xs text-on-surface-variant">Workspace ID</span>
              <input
                aria-label="Workspace ID"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">Repo path</span>
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
                Provider override
              </span>
              <input
                aria-label="Provider override"
                value={providerOverride}
                onChange={(event) => setProviderOverride(event.target.value)}
                placeholder="claude-code / opencode / internal-agent"
                className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
            </label>
            {selectedWorkflowInputs.length > 0 && (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                <p className="mb-2 text-xs font-medium text-on-surface">
                  Workflow inputs
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
                                  ? "One local file path per line"
                                  : role || "Input text"
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
                                  ? "Local file path"
                                  : role || "Input value"
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
              <span className="mb-1 block text-xs text-on-surface-variant">Inputs JSON</span>
              <textarea
                value={inputsJson}
                onChange={(event) => setInputsJson(event.target.value)}
                className="h-40 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                aria-label="Inputs JSON"
                spellCheck={false}
              />
            </label>
            <button
              onClick={prepareTaskRun}
              disabled={busyAction === "prepare-task-run" || !repoPath.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busyAction === "prepare-task-run" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <PlayCircle size={14} />
              )}
              Prepare run
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
              Execute workflow
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
              Audit artifacts
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
              Rerun plan
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
              Execute rerun
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
              Materialize outputs
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
              Import semantics
            </button>
            {preparedRun && (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs">
                <p className="font-medium text-on-surface">{preparedRun.task_run_id}</p>
                <p className="mt-1 break-words font-data text-on-surface-variant">
                  {preparedRun.artifact_dir}
                </p>
                <p className="mt-1 text-on-surface-variant">
                  Agent runs: {preparedRun.agent_runs.length}
                </p>
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
                      <p className="mt-1 font-data text-[10px] text-on-surface-variant">
                        rerun-execution:{taskRerunExecution.status} workflow:
                        {taskRerunExecution.execution?.status ?? "unknown"}
                      </p>
                    )}
                  </div>
                )}
                {(() => {
                  const contextBundle = preparedRun.task_bundle.context_bundle as
                    | {
                        evidence?: unknown[];
                        semantic_cases?: unknown[];
                      }
                    | undefined;
                  if (!contextBundle) return null;
                  return (
                    <p className="mt-1 text-on-surface-variant">
                      Context: evidence {contextBundle.evidence?.length ?? 0} /
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
                    Audit artifacts: {artifactManifest.artifacts.length}
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {prioritizedAuditArtifacts(artifactManifest.artifacts).slice(0, 12).map((artifact) => (
                        <button
                          key={artifact.relative_path}
                          onClick={() => previewArtifact(artifact.relative_path)}
                          disabled={busyAction === `preview-artifact-${artifact.relative_path}`}
                          className="rounded bg-surface px-1.5 py-0.5 text-left font-data text-[10px] transition-colors hover:bg-surface-container-high disabled:opacity-50"
                        >
                          {artifact.kind}:{artifact.relative_path}
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
                              </div>
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
                            </div>
                          );
                        })()}
                        {artifactContent.is_text ? (
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
                    Workflow: {workflowExecution.status} / steps{" "}
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
                  </div>
                )}
                {semanticOutputImport && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    <p>
                      Semantic import: {semanticOutputImport.imported_count} imported
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
                              Required artifacts: {requiredArtifacts.join(", ")}
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
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs">
                <p className="mb-2 font-medium text-on-surface">Recent task runs</p>
                <div className="space-y-2">
                  {taskRuns.map((run) => (
                    <button
                      key={run.task_run_id}
                      onClick={() => {
                        setPreparedRun(run);
                        setExecutionResults({});
                        setValidationResults({});
                        setMaterializeResults({});
                        setArtifactContent(null);
                      }}
                      className="block w-full rounded-md bg-surface-container px-2.5 py-2 text-left transition-colors hover:bg-surface-container-high"
                    >
                      <span className="block font-medium text-on-surface">
                        {run.workflow_id}
                      </span>
                      <span className="block break-words font-data text-[11px] text-on-surface-variant">
                        {run.task_run_id}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Panel>

        <Panel title="Test Semantic Library" icon={<Library size={16} />}>
          <div className="space-y-3">
            <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">Feature</span>
                  <input
                    aria-label="Semantic feature"
                    value={semanticFeature}
                    onChange={(event) => setSemanticFeature(event.target.value)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs text-on-surface-variant">Module</span>
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
                  Existing cases, one per line
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
                Build semantic JSON
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
                  Import file
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
              className="h-52 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
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
                Import case(s)
              </button>
              <input
                value={semanticQuery}
                onChange={(event) => setSemanticQuery(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
              <button
                onClick={searchSemanticCases}
                disabled={busyAction === "search-semantic-cases" || !semanticQuery.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
              >
                <Search size={14} />
                Search
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

        <Panel title="Evidence Memory" icon={<Database size={16} />}>
          <div className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={memoryQuery}
                onChange={(event) => setMemoryQuery(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
              />
              <button
                onClick={searchMemory}
                disabled={busyAction === "search-memory" || !memoryQuery.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                <Search size={14} />
                Search memory
              </button>
            </div>
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-400">
              <div className="flex items-start gap-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>
                  Memory facts are structured evidence only. Raw Agent output is stored as
                  artifact context, not reused as truth.
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
                      Source slices
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
      </div>
    </div>
  );
}

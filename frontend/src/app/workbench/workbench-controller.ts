"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from "react";
import { flushSync } from "react-dom";
import { useRouter } from "next/navigation";
import { AlertTriangle, ClipboardList, Copy, Database, Download, FilePlus2, Library, Loader2, PlayCircle, RefreshCw, RotateCcw, Save, Search, MessageSquareText, Trash2, X } from "lucide-react";
import { api, currentApiBase } from "@/lib/api";
import { buildWorkflowFromDesigner, mergeDesignerWorkflowWithDraft, mergeDesignerWorkflowWithSpecializedDraft } from "@/lib/workflow-builder.mjs";
import { deriveRunPanelStatus } from "@/lib/run-panel-status.mjs";
import type { EvidenceMemoryItem, EvidenceSourceSlice, ExternalAgentStartupProbeResult, AgentRunExecutionResult, ArtifactValidationResult, MaterializeEvidenceResult, MaterializeWorkflowOutputsResult, PreparedWorkbenchTaskRun, SemanticCase, SemanticCaseImportResult, TaskRerunExecutionResult, TaskRerunHistory, TaskRerunPlan, TaskRerunPlanValidation, WorkbenchDeploymentProbeResult, WorkflowDefinition, WorkflowExecutionResult, WorkflowPreset, WorkbenchWorkflowCapabilities, WorkbenchAcceptanceAudit, WorkbenchProviderCapabilitiesMatrix, WorkbenchProviderTaskProbeResult, WorkbenchSmokeE2EResult, WorkbenchSystemAudit, WorkbenchTaskArtifactContent, WorkbenchTaskArtifactManifest, WorkbenchTaskRunEvent, Workspace, WorkflowDraftServerAudit } from "@/lib/types";


import * as WorkbenchShared from "./workbench-shared";
import type { WorkbenchView, WorkflowCanvasNode, WorkflowCanvasEdge, WorkflowDraftEdge, WorkflowNodePosition, WorkflowCanvasLayout, WorkflowSkillOption } from "./workbench-shared";

const WORKBENCH_WORKSPACE_STORAGE_KEY = "codetalk.workbench.workspace_id";

const { MIN_VISIBLE_BUSY_ACTION_MS, WORKFLOW_CANVAS_WIDTH, WORKFLOW_CANVAS_HEIGHT, WORKFLOW_NODE_WIDTH, WORKFLOW_NODE_HEIGHT, DEFAULT_WORKFLOW, DEFAULT_INPUTS, workbenchInputsFromSearchParams, workbenchWorkspaceIdFromSearchParams, WorkbenchStageFrame, CORE_WORKFLOW_PRESET_IDS, workflowDisplayName, workflowPresetGroup, WORKFLOW_BUILDER_SCENARIOS, DEFAULT_BUILDER_OUTPUT_SCHEMAS, DEFAULT_BUILDER_EVIDENCE_MAPPINGS, DEFAULT_BUILDER_SEMANTIC_IMPORTS, DEFAULT_BUILDER_INPUT_SCHEMAS, DEFAULT_SEMANTIC_CASE, DEFAULT_SEMANTIC_LINES, pretty, parseJsonObject, workflowIdFromJson, parseJsonValue, parseCommaSeparated, uniqueWorkflowStrings, parseWorkflowSpecList, WORKFLOW_MODULE_PALETTE, WORKFLOW_NODE_TONE, WORKFLOW_NODE_ACCENT, FALLBACK_WORKFLOW_SKILLS, DEFAULT_BUILDER_SKILL_IDS, workflowPaletteKind, workflowPaletteSubtitle, clampWorkflowNodePosition, workflowLayoutFromPayload, safeWorkflowSpecList, workflowSpecToText, workflowItemLabel, workflowInputDisplayName, safeArtifactDownloadFilename, downloadTextFile, ArtifactPreviewCard, outputArtifactForSpec, outputSchemaForSpec, outputEvidenceMappingForSpec, outputSemanticImportForSpec, workflowInputsFromJson, workflowOutputsFromJson, workflowStepsFromJson, workflowOutputDisplayName, artifactShortName, workflowDraftAudit, inputTextValue, updateInputsJsonValue, isFileLikeWorkflowInput, isPatchLikeWorkflowInput, semanticCasesFromLines, isBulkSemanticImportPayload, fastContextDecisionSummary, inputContextSummary, agentMcpRequestSummary, providerReadinessSummary, commandResolutionLines, acceptanceProviderIssues, acceptanceCodetalkProviderIssues, acceptanceWorkflowOutputIssues, acceptanceInstructionPolicyIssues, acceptanceInputRedactionIssues, evidenceValidationSummary, workflowOutputMaterializationSummary, materializationAuditOutputs, replayPlanSummary, executionInputSummary, blackBoxGenerationPolicySummary, memoryArtifactSummary, inputMaterialsSummary, failureRetryContextSummary, rejectedOutputLabel, rejectedOutputReason, evidenceAuditRefs, prioritizedAuditArtifacts, artifactAudience, artifactAudienceLabel, runStatusDisplayLabel, providerStatusDisplayLabel, providerDisplayLabel, workflowRunSnapshotSummary, compactReasonLabel, taskRunEventTitle, taskRunEventDetail, taskRunEventTone, workflowAuditWarningLabel, acceptanceIssueLabel, workflowRunResultMessage, suggestedWorkflowIdFromError, workflowHasSpecializedStep, groupArtifactsByAudience, Panel, ProviderFactRow, ProviderSectionTitle } = WorkbenchShared;

export function useWorkbenchController({
  initialView = "run",
}: {
  initialView?: WorkbenchView;
}) {
  const router = useRouter();
  const workbenchRootRef = useRef<HTMLDivElement | null>(null);
  const workflowBoardRef = useRef<HTMLDivElement | null>(null);
  const workflowCanvasInnerRef = useRef<HTMLDivElement | null>(null);
  const workspaceAutoSelectionDoneRef = useRef(false);
  const queryPrefillAppliedRef = useRef(false);
  const autoRestoredTaskRunRef = useRef<string | null>(null);
  const startTaskRunPollingRef = useRef<(taskRunId: string) => void>(() => undefined);
  const taskRunEventSourceRef = useRef<EventSource | null>(null);
  const taskRunPollingIdRef = useRef<string | null>(null);
  const paletteDragModuleRef = useRef<string | null>(null);
  const palettePointerDragRef = useRef<{
    moduleId: string;
    startX: number;
    startY: number;
  } | null>(null);
  const workflowDragRef = useRef<{
    id: string;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const workflowConnectionDragRef = useRef<WorkflowDraftEdge | null>(null);
  const workflowBoardPanRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startScrollLeft: number;
    startScrollTop: number;
    moved: boolean;
  } | null>(null);
  const localWorkflowDraftIdRef = useRef("");
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [workflowPresets, setWorkflowPresets] = useState<WorkflowPreset[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workflowJson, setWorkflowJson] = useState(pretty(DEFAULT_WORKFLOW));
  const [builderScenario, setBuilderScenario] =
    useState<keyof typeof WORKFLOW_BUILDER_SCENARIOS>("mr_blackbox_test");
  const [builderWorkflowId, setBuilderWorkflowId] =
    useState("custom_mr_blackbox");
  const [builderWorkflowName, setBuilderWorkflowName] =
    useState("自定义 MR 黑盒测试工作流");
  const [builderInputSpec, setBuilderInputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.inputs,
  );
  const [builderOutputSpec, setBuilderOutputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.outputs,
  );
  const [builderProvider, setBuilderProvider] = useState("claude-code");
  const [builderMcpProfile, setBuilderMcpProfile] = useState("codehub-mcp");
  const [builderSkillQuery, setBuilderSkillQuery] = useState("");
  const [builderGoal, setBuilderGoal] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.goal,
  );
  const [builderArtifacts, setBuilderArtifacts] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.artifacts,
  );
  const [builderSkillIds, setBuilderSkillIds] = useState<string[]>(
    DEFAULT_BUILDER_SKILL_IDS,
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
  const [builderInputLabels, setBuilderInputLabels] = useState<
    Record<string, string>
  >({});
  const [builderOutputLabels, setBuilderOutputLabels] = useState<
    Record<string, string>
  >({});
  const [newWorkflowInputName, setNewWorkflowInputName] = useState("");
  const [newWorkflowInputId, setNewWorkflowInputId] = useState("");
  const [newWorkflowInputType, setNewWorkflowInputType] = useState("file");
  const [newWorkflowInputResolver, setNewWorkflowInputResolver] =
    useState("manual");
  const [newWorkflowOutputName, setNewWorkflowOutputName] = useState("");
  const [newWorkflowOutputId, setNewWorkflowOutputId] = useState("");
  const [newWorkflowOutputType, setNewWorkflowOutputType] = useState("json");
  const [newWorkflowOutputArtifact, setNewWorkflowOutputArtifact] =
    useState("");
  const [workflowCanvasEdges, setWorkflowCanvasEdges] = useState<
    WorkflowCanvasEdge[]
  >([]);
  const [workflowHiddenEdgeIds, setWorkflowHiddenEdgeIds] = useState<string[]>(
    [],
  );
  const [workflowDraftEdge, setWorkflowDraftEdge] =
    useState<WorkflowDraftEdge | null>(null);
  const [workflowPendingConnectionSourceId, setWorkflowPendingConnectionSourceId] =
    useState("");
  const [workflowNodePositions, setWorkflowNodePositions] = useState<
    Record<string, WorkflowNodePosition>
  >({});
  const [workflowExtraNodes, setWorkflowExtraNodes] = useState<
    WorkflowCanvasNode[]
  >([]);
  const [workflowHiddenNodeIds, setWorkflowHiddenNodeIds] = useState<string[]>(
    [],
  );
  const [workflowNodeTitles, setWorkflowNodeTitles] = useState<
    Record<string, string>
  >({});
  const [workflowNodeConfigs, setWorkflowNodeConfigs] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [activeWorkflowNodeId, setActiveWorkflowNodeId] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(
    DEFAULT_WORKFLOW.id,
  );
  const selectedWorkflowIdRef = useRef(DEFAULT_WORKFLOW.id);
  const [workspaceId, setWorkspaceId] = useState("manual-workspace");
  const [repoPath, setRepoPath] = useState("");
  const [providerOverride, setProviderOverride] = useState("");
  const [inputsJson, setInputsJson] = useState(pretty(DEFAULT_INPUTS));
  const [semanticJson, setSemanticJson] = useState(
    pretty(DEFAULT_SEMANTIC_CASE),
  );
  const [semanticFeature, setSemanticFeature] = useState("NVMe TCP TLS");
  const [semanticModule, setSemanticModule] = useState("nvmf_tcp");
  const [semanticLines, setSemanticLines] = useState(DEFAULT_SEMANTIC_LINES);
  const [semanticFile, setSemanticFile] = useState<File | null>(null);
  const [semanticQuery, setSemanticQuery] = useState("tls cleanup");
  const [semanticResults, setSemanticResults] = useState<SemanticCase[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("nvme tcp tls");
  const [manualEvidenceSubject, setManualEvidenceSubject] =
    useState("nvmf_tgt_accept");
  const [manualEvidencePath, setManualEvidencePath] =
    useState("lib/nvmf/nvmf.c");
  const [manualEvidenceText, setManualEvidenceText] = useState(
    "SPDK NVMe-oF target accept path evidence for connect-flow black-box validation.",
  );
  const [memoryResults, setMemoryResults] = useState<EvidenceMemoryItem[]>([]);
  const [memorySlices, setMemorySlices] = useState<
    Record<string, EvidenceSourceSlice[]>
  >({});
  const [providerMatrix, setProviderMatrix] =
    useState<WorkbenchProviderCapabilitiesMatrix | null>(null);
  const [workflowCapabilities, setWorkflowCapabilities] =
    useState<WorkbenchWorkflowCapabilities | null>(null);
  const [systemAudit, setSystemAudit] = useState<WorkbenchSystemAudit | null>(
    null,
  );
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
  const [preparedRun, setPreparedRun] =
    useState<PreparedWorkbenchTaskRun | null>(null);
  const [artifactManifest, setArtifactManifest] =
    useState<WorkbenchTaskArtifactManifest | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<WorkbenchTaskArtifactContent | null>(null);
  const [workflowExecution, setWorkflowExecution] =
    useState<WorkflowExecutionResult | null>(null);
  const [taskRunEvents, setTaskRunEvents] = useState<WorkbenchTaskRunEvent[]>(
    [],
  );
  const [taskRerunPlan, setTaskRerunPlan] = useState<TaskRerunPlan | null>(
    null,
  );
  const [taskRerunPlanValidation, setTaskRerunPlanValidation] =
    useState<TaskRerunPlanValidation | null>(null);
  const [taskRerunExecution, setTaskRerunExecution] =
    useState<TaskRerunExecutionResult | null>(null);
  const [taskRerunHistory, setTaskRerunHistory] =
    useState<TaskRerunHistory | null>(null);
  const [taskAcceptanceAudit, setTaskAcceptanceAudit] =
    useState<WorkbenchAcceptanceAudit | null>(null);
  const [workflowOutputMaterialize, setWorkflowOutputMaterialize] =
    useState<MaterializeWorkflowOutputsResult | null>(null);
  const [workflowDraftServerAudit, setWorkflowDraftServerAudit] =
    useState<WorkflowDraftServerAudit | null>(null);
  const [workflowInputsUpdated, setWorkflowInputsUpdated] = useState(false);
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
  const [activeWorkbenchView, setActiveWorkbenchView] =
    useState<WorkbenchView>(initialView);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [motionPreferenceReady, setMotionPreferenceReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busyActionRef = useRef<string | null>(null);
  const activeActionsRef = useRef<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [openingConversation, setOpeningConversation] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setActiveWorkbenchView(initialView);
  }, [initialView]);

  useEffect(
    () => () => {
      taskRunPollingIdRef.current = null;
      taskRunEventSourceRef.current?.close();
      taskRunEventSourceRef.current = null;
    },
    [],
  );

  useEffect(() => {
    if (queryPrefillAppliedRef.current || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const workflowId = params.get("workflow")?.trim() || "";
    const queryInputs = workbenchInputsFromSearchParams(params);
    if (!workflowId && Object.keys(queryInputs).length === 0) return;
    if (workflowId && workflows.length === 0 && workflowPresets.length === 0) {
      return;
    }
    queryPrefillAppliedRef.current = true;
    if (workflowId) {
      selectedWorkflowIdRef.current = workflowId;
      setSelectedWorkflowId(workflowId);
    }
    if (queryInputs.repo_path) setRepoPath(queryInputs.repo_path);
    const workflowDefinition = workflowId
      ? workflows.find((workflow) => workflow.id === workflowId) ??
        workflowPresets.find(
          (preset) => preset.definition.id === workflowId || preset.id === workflowId,
        )?.definition ??
        null
      : null;
    const workflowDefaults: Record<string, string> = {};
    for (const input of workflowDefinition?.inputs ?? []) {
      if (!input || typeof input !== "object") continue;
      const inputId = String((input as Record<string, unknown>).id ?? "");
      if (!inputId) continue;
      workflowDefaults[inputId] = inputId === "repo_path" ? repoPath : "";
    }
    setInputsJson((current) =>
      pretty({
        ...workflowDefaults,
        ...parseJsonObject(current || "{}"),
        ...queryInputs,
      }),
    );
    setWorkflowInputsUpdated(true);
    window.setTimeout(() => setWorkflowInputsUpdated(false), 2200);
  }, [repoPath, workflowPresets, workflows]);

  const workflowOptions = useMemo(() => {
    const seen = new Set<string>();
    return [
      ...workflows,
      ...workflowPresets
        .map((preset) => preset.definition)
        .filter(
          (definition) =>
            !workflows.some((workflow) => workflow.id === definition.id),
        ),
    ].flatMap((workflow) => {
      if (seen.has(workflow.id)) return [];
      seen.add(workflow.id);
      return [
        {
          id: workflow.id,
          label: workflowDisplayName(workflow),
        },
      ];
    });
  }, [workflowPresets, workflows]);

  const groupedWorkflowPresets = useMemo(
    () =>
      (["核心工作流", "常用测试场景"] as const)
        .map((group) => ({
          group,
          items: workflowPresets.filter(
            (preset) => workflowPresetGroup(preset) === group,
          ),
        }))
        .filter((group) => group.items.length > 0),
    [workflowPresets],
  );
  const savedCustomWorkflows = useMemo(() => {
    const presetIds = new Set(
      workflowPresets.flatMap((preset) => [preset.id, preset.definition.id]),
    );
    return workflows.filter((workflow) => !presetIds.has(workflow.id));
  }, [workflowPresets, workflows]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setPrefersReducedMotion(query.matches);
    updatePreference();
    setMotionPreferenceReady(true);
    query.addEventListener("change", updatePreference);
    return () => query.removeEventListener("change", updatePreference);
  }, []);
  useEffect(() => {
    if (
      selectedWorkflowId !== DEFAULT_WORKFLOW.id ||
      workflowPresets.length === 0
    )
      return;
    const preferredPreset =
      workflowPresets.find((preset) => preset.id === "module_analysis") ??
      workflowPresets[0];
    selectedWorkflowIdRef.current = preferredPreset.definition.id;
    setSelectedWorkflowId(preferredPreset.definition.id);
    setWorkflowJson((currentJson) => {
      const currentId = workflowIdFromJson(currentJson);
      if (currentId && currentId !== DEFAULT_WORKFLOW.id) return currentJson;
      return pretty(preferredPreset.definition);
    });
  }, [selectedWorkflowId, workflowPresets]);
  const builderProviderOptions = useMemo(() => {
    const providers = (providerMatrix?.providers ?? [])
      .filter(
        (provider) =>
          provider.agent_owned ||
          provider.codetalk_callable ||
          provider.command.length > 0 ||
          provider.owner === "agent_runtime" ||
          provider.provider === "builtin-llm",
      )
      .map((provider) => ({
        id: provider.provider,
        label: provider.display_name || provider.provider,
        status: provider.status,
        owner: provider.owner,
      }));
    if (!providers.some((provider) => provider.id === "claude-code")) {
      providers.unshift({
        id: "claude-code",
        label: "Claude Code",
        status: "configured",
        owner: "agent_cli",
      });
    }
    return providers;
  }, [providerMatrix]);
  const runExecutorProviderOptions = useMemo(
    () =>
      builderProviderOptions.filter((provider) =>
        ["agent_cli", "agent_runtime", "codetalk_builtin_llm"].includes(
          provider.owner,
        ),
      ),
    [builderProviderOptions],
  );
  const workflowSkillOptions = useMemo<WorkflowSkillOption[]>(() => {
    const catalog = workflowCapabilities?.skill_catalog ?? [];
    const merged = [...catalog, ...FALLBACK_WORKFLOW_SKILLS];
    const seen = new Set<string>();
    return merged.filter((skill) => {
      if (!skill.id || seen.has(skill.id)) return false;
      seen.add(skill.id);
      return true;
    });
  }, [workflowCapabilities]);
  const selectedBuilderSkillOptions = useMemo(
    () =>
      workflowSkillOptions.filter((skill) =>
        builderSkillIds.includes(skill.id),
      ),
    [builderSkillIds, workflowSkillOptions],
  );
  const visibleBuilderSkillOptions = useMemo(() => {
    const query = builderSkillQuery.trim().toLowerCase();
    const matches = query
      ? workflowSkillOptions.filter((skill) =>
          [
            skill.id,
            skill.label,
            skill.description ?? "",
            skill.source ?? "",
          ]
            .join(" ")
            .toLowerCase()
            .includes(query),
        )
      : workflowSkillOptions.filter(
          (skill, index) => builderSkillIds.includes(skill.id) || index < 8,
        );
    return matches.slice(0, 24);
  }, [builderSkillIds, builderSkillQuery, workflowSkillOptions]);
  const builderProviderItem = useMemo(
    () =>
      (providerMatrix?.providers ?? []).find(
        (provider) => provider.provider === builderProvider,
      ) ?? null,
    [builderProvider, providerMatrix],
  );
  const builderMcpOptions = useMemo(() => {
    const profiles = uniqueWorkflowStrings([
      ...((builderProviderItem?.capabilities?.mcp_profiles ?? []) as string[]),
      ...(providerMatrix?.providers ?? []).flatMap((provider) =>
        (provider.capabilities?.mcp_profiles ?? []).map(String),
      ),
      "gitnexus",
      "cgc",
      "codehub-mcp",
      "codehub-readonly",
      builderMcpProfile,
    ]);
    return profiles;
  }, [builderMcpProfile, builderProviderItem, providerMatrix]);
  const builderMcpCompatibility = useMemo(() => {
    const profile = builderMcpProfile.trim();
    if (!profile) {
      return {
        level: "muted",
        label: "未启用 MCP",
        detail: "Agent 会使用工作区输入和 CodeTalk 预取上下文。",
      };
    }
    if (!builderProvider.trim()) {
      return {
        level: "pending",
        label: "等待选择执行器",
        detail: "MCP 需求会保留，选择执行器后再校验兼容性。",
      };
    }
    if (!builderProviderItem) {
      return {
        level: "pending",
        label: "自定义执行器待探测",
        detail: "保存和运行前会通过执行器探测确认 MCP 能力。",
      };
    }
    const capabilities = builderProviderItem.capabilities;
    const mcpProfiles = (capabilities?.mcp_profiles ?? []).map(String);
    if (mcpProfiles.includes(profile)) {
      return {
        level: "ok",
        label: "Agent 可直接使用 MCP",
        detail: `${builderProviderItem.display_name || builderProvider} 声明了 ${profile}。`,
      };
    }
    if (capabilities?.supports_mcp && mcpProfiles.length === 0) {
      return {
        level: "pending",
        label: "Agent 支持 MCP，profile 待确认",
        detail: "该执行器支持 MCP 但未声明 profile，建议先运行探测。",
      };
    }
    if (
      profile === "gitnexus" ||
      profile === "cgc" ||
      profile === "codehub-mcp"
    ) {
      return {
        level: "fallback",
        label: "CodeTalk 预取后注入",
        detail: "当前执行器未声明该 MCP；CodeTalk 会优先查源码/GitNexus/CGC 后把证据交给 Agent。",
      };
    }
    return {
      level: "warn",
      label: "MCP 与执行器不匹配",
      detail: "当前执行器未声明该 MCP profile，运行前需要更换执行器或改成 CodeTalk 预取。",
    };
  }, [builderMcpProfile, builderProvider, builderProviderItem]);
  const semanticImportOutputIds = useMemo(
    () =>
      (workflowExecution?.outputs ?? [])
        .filter((output) => {
          const outputId = String(output.id ?? "").toLowerCase();
          const outputType = String(output.type ?? "").toLowerCase();
          const artifact = String(
            output.artifact ?? output.path ?? "",
          ).toLowerCase();
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
  const parsedPrepareInputs = useMemo(() => {
    try {
      return parseJsonObject(inputsJson || "{}");
    } catch {
      return {};
    }
  }, [inputsJson]);
  const selectedWorkflowInputs = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.inputs?.length) return registered.inputs;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.inputs?.length) return preset.definition.inputs;
    return workflowInputsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const visibleWorkflowInputs = useMemo(
    () =>
      selectedWorkflowInputs.filter((input) => {
        const inputId = String(input.id ?? "");
        const inputType = String(input.type ?? "");
        return !(inputId === "repo_path" && inputType === "directory");
      }),
    [selectedWorkflowInputs],
  );
  const selectedWorkflowOutputs = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.outputs?.length) return registered.outputs;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.outputs?.length) return preset.definition.outputs;
    return workflowOutputsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const selectedWorkflowSteps = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.steps?.length) return registered.steps;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.steps?.length) return preset.definition.steps;
    return workflowStepsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const selectedAgentStep = useMemo(
    () =>
      selectedWorkflowSteps.find(
        (step) => String(step.type ?? "") === "agent_task",
      ) ?? null,
    [selectedWorkflowSteps],
  );
  useEffect(() => {
    if (!selectedAgentStep && providerOverride) {
      setProviderOverride("");
    }
  }, [providerOverride, selectedAgentStep]);
  const selectedAgentSkillInstructions = useMemo(() => {
    const raw = selectedAgentStep?.skill_instructions;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item) =>
        item && typeof item === "object"
          ? (item as Record<string, unknown>)
          : null,
      )
      .filter((item): item is Record<string, unknown> => Boolean(item));
  }, [selectedAgentStep]);
  const selectedAgentSkillIds = useMemo(() => {
    const raw = selectedAgentStep?.skills;
    return Array.isArray(raw)
      ? raw.map((item) => String(item)).filter(Boolean)
      : [];
  }, [selectedAgentStep]);
  const selectedRunProvider = useMemo(
    () =>
      selectedAgentStep
        ? providerOverride.trim() ||
          String(selectedAgentStep.provider ?? (builderProvider || "claude-code"))
        : "本地内置步骤",
    [builderProvider, providerOverride, selectedAgentStep],
  );
  const selectedRunMcpProfile = useMemo(
    () =>
      String(
        selectedAgentStep?.mcp_profile ??
          (builderMcpProfile.trim() ? builderMcpProfile : "未启用"),
      ),
    [builderMcpProfile, selectedAgentStep],
  );
  const selectedProviderCapability = useMemo(
    () =>
      (providerMatrix?.providers ?? []).find(
        (provider) => provider.provider === selectedRunProvider,
      ) ?? null,
    [providerMatrix, selectedRunProvider],
  );
  const requiredInputCount = useMemo(
    () =>
      selectedWorkflowInputs.filter((input) => input.required === true).length,
    [selectedWorkflowInputs],
  );
  const filledInputCount = useMemo(
    () =>
      selectedWorkflowInputs.filter((input) => {
        const id = String(input.id ?? "");
        if (!id) return false;
        const value = parsedPrepareInputs[id];
        if (value === null || value === undefined) return false;
        if (typeof value === "string") return value.trim().length > 0;
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === "object") {
          return Object.keys(value as Record<string, unknown>).length > 0;
        }
        return true;
      }).length,
    [parsedPrepareInputs, selectedWorkflowInputs],
  );
  const preparedProviderReadiness = useMemo(
    () =>
      preparedRun?.task_bundle &&
      typeof preparedRun.task_bundle === "object" &&
      !Array.isArray(preparedRun.task_bundle)
        ? providerReadinessSummary(preparedRun.task_bundle)
        : null,
    [preparedRun],
  );
  const preparedAgentMcpRequests = useMemo(
    () =>
      preparedRun?.task_bundle &&
      typeof preparedRun.task_bundle === "object" &&
      !Array.isArray(preparedRun.task_bundle)
        ? agentMcpRequestSummary(preparedRun.task_bundle)
        : [],
    [preparedRun],
  );
  const preparedRunSnapshotSummary = useMemo(
    () => (preparedRun ? workflowRunSnapshotSummary(preparedRun) : null),
    [preparedRun],
  );
  const activeRunUiSummary = useMemo(
    () => workflowExecution?.run_ui_summary ?? preparedRun?.run_ui_summary ?? null,
    [preparedRun, workflowExecution],
  );
  const runPanelExecutionNotice = useMemo(() => {
    const label = String(
      activeRunUiSummary?.execution_label ??
        activeRunUiSummary?.workflow?.execution_label ??
        "",
    ).trim();
    const message = String(
      activeRunUiSummary?.user_message ??
        activeRunUiSummary?.workflow?.user_message ??
        "",
    ).trim();
    const subject = String(
      activeRunUiSummary?.execution_subject ??
        activeRunUiSummary?.workflow?.execution_subject ??
        "",
    ).trim();
    if (!label && !message) return null;
    return { label, message, subject };
  }, [activeRunUiSummary]);
  const runPhaseCards = useMemo(
    () => {
      if (activeRunUiSummary?.nodes?.length) {
        return activeRunUiSummary.nodes.map((node) => {
          const inputCount = node.inputs?.length ?? 0;
          const mcpCount = node.mcp_inputs?.length ?? 0;
          const skillCount = node.skills?.length ?? 0;
          const outputCount = node.outputs?.length ?? 0;
          const executorLabel =
            node.executor_label && node.executor_label !== activeRunUiSummary.execution_label
              ? node.executor_label
              : "";
          const details = [
            executorLabel,
            inputCount ? `输入 ${inputCount}` : "",
            mcpCount ? `MCP ${mcpCount}` : "",
            skillCount ? `技能 ${skillCount}` : "",
            outputCount ? `输出 ${outputCount}` : "",
          ].filter(Boolean);
          return {
            label: node.label || node.id,
            status: node.status_label,
            detail:
              details.length > 0
                ? details.join(" · ")
                : node.executor_label || node.provider || node.type || "等待节点执行",
          };
        });
      }
      return [
        {
          label: "准备上下文",
          status: preparedRun ? "完成" : "等待",
          detail: repoPath.trim() ? `源码路径: ${repoPath}` : "等待选择源码路径",
        },
        {
          label: "执行 Agent",
          status: workflowExecution
            ? runStatusDisplayLabel(workflowExecution.status)
            : preparedRun
              ? "等待"
              : "等待",
          detail: `${selectedRunProvider} · ${selectedRunMcpProfile}`,
        },
        {
          label: "校验证据",
          status: taskAcceptanceAudit
            ? runStatusDisplayLabel(taskAcceptanceAudit.status)
            : workflowExecution?.evidence_materialization?.status ??
              (preparedRun ? "待审计" : "等待"),
          detail: taskAcceptanceAudit
            ? `缺少 ${taskAcceptanceAudit.summary.missing_required} 个必需验收项`
            : "等待校验 schema、证据和脱敏",
        },
        {
          label: "固化交付物",
          status: runStatusDisplayLabel(
            workflowOutputMaterialize?.status ??
              workflowExecution?.evidence_materialization?.status ??
              (artifactManifest ? "ready" : "waiting"),
          ),
          detail: `${artifactManifest?.artifacts.length ?? 0} 个产物`,
        },
      ];
    },
    [
      activeRunUiSummary,
      artifactManifest,
      preparedRun,
      repoPath,
      selectedRunMcpProfile,
      selectedRunProvider,
      taskAcceptanceAudit,
      workflowExecution,
      workflowOutputMaterialize,
    ],
  );
  const artifactAudienceGroups = useMemo(
    () => groupArtifactsByAudience(artifactManifest?.artifacts ?? []),
    [artifactManifest],
  );
  const testActivityQuality = workflowExecution?.test_activity_quality;
  const runPanelStatus = useMemo(
    () =>
      deriveRunPanelStatus({
        hasPreparedRun: Boolean(preparedRun),
        activeStatusLabel: activeRunUiSummary?.status_label,
        testActivityStatus: testActivityQuality?.status,
        acceptanceStatus: taskAcceptanceAudit?.status,
        missingRequired: taskAcceptanceAudit?.summary.missing_required,
        workflowStatus: workflowExecution?.status,
        hasMaterializedOutput: Boolean(workflowOutputMaterialize?.status),
      }),
    [
      preparedRun,
      taskAcceptanceAudit,
      workflowExecution,
      workflowOutputMaterialize,
      activeRunUiSummary,
      testActivityQuality,
    ],
  );
  const runPanelFailureReasons = useMemo(() => {
    const summaryReasons = activeRunUiSummary?.failure?.reasons ?? [];
    if (summaryReasons.length > 0) {
      return Array.from(new Set(summaryReasons.map(compactReasonLabel))).slice(0, 5);
    }
    const reasons: string[] = [];
    activeRunUiSummary?.nodes
      ?.flatMap((node) => node.review_reasons ?? [])
      .filter(Boolean)
      .forEach((reason) => reasons.push(compactReasonLabel(reason)));
    workflowExecution?.step_results
      .map((step) => step.failure_recovery)
      .filter(Boolean)
      .forEach((recovery) => {
        if (recovery?.user_message) reasons.push(recovery.user_message);
        recovery?.recommended_actions?.slice(0, 2).forEach((action) => {
          reasons.push(action);
        });
      });
    if (
      testActivityQuality?.status &&
      ["needs_rework", "invalid"].includes(String(testActivityQuality.status).toLowerCase())
    ) {
      reasons.push(
        `质量审计需要补证据：${Number(testActivityQuality.score ?? 0)} 分，${Number(testActivityQuality.issue_count ?? 0)} 个问题`,
      );
      testActivityQuality.recommendations?.slice(0, 2).forEach((item) => {
        reasons.push(item);
      });
    }
    const missingRequired = taskAcceptanceAudit?.summary.missing_required ?? 0;
    if (missingRequired > 0) {
      reasons.push(`缺少 ${missingRequired} 个必需验收项`);
    }
    taskAcceptanceAudit?.missing_required
      .map((issue) => acceptanceIssueLabel(issue))
      .filter(Boolean)
      .forEach((reason) => reasons.push(reason));
    preparedProviderReadiness?.blockingReasons
      .map(compactReasonLabel)
      .forEach((reason) => reasons.push(reason));
    if (!taskAcceptanceAudit && preparedProviderReadiness?.warnings.length) {
      preparedProviderReadiness.warnings
        .slice(0, 3)
        .map(compactReasonLabel)
        .forEach((reason) => reasons.push(reason));
    }
    return Array.from(new Set(reasons)).slice(0, 5);
  }, [
    activeRunUiSummary,
    preparedProviderReadiness,
    taskAcceptanceAudit,
    testActivityQuality,
    workflowExecution,
  ]);
  const runPanelCapabilitySummary = useMemo(() => {
    if (!preparedRun) return null;
    const rows: Array<{
      id: string;
      label: string;
      value: string;
      detail?: string;
      tone: "ok" | "warning" | "muted";
    }> = [];
    const mcpProfiles = new Set<string>();
    const skills = new Set<string>();
    const requiredArtifacts = new Set<string>();

    if (preparedProviderReadiness) {
      rows.push({
        id: "repo",
        label: "源码工作区",
        value: providerStatusDisplayLabel(preparedProviderReadiness.repoStatus),
        tone:
          ["ready", "ok", "available", "configured"].includes(
            preparedProviderReadiness.repoStatus.toLowerCase(),
          )
            ? "ok"
            : "warning",
      });
      preparedProviderReadiness.agentProviders.forEach((provider) => {
        rows.push({
          id: `agent:${provider.provider}`,
          label: `Agent · ${providerDisplayLabel(provider.provider)}`,
          value: providerStatusDisplayLabel(provider.status),
          detail: provider.reason
            ? compactReasonLabel(provider.reason)
            : provider.deploymentEvidenceConflict
              ? "部署探测证据与当前执行器状态冲突"
              : "",
          tone:
            provider.status === "available" &&
            !provider.deploymentEvidenceConflict
              ? "ok"
              : "warning",
        });
      });
      preparedProviderReadiness.codetalkProviders
        .filter((provider) => provider.provider !== "local-search")
        .forEach((provider) => {
          rows.push({
            id: `codetalk:${provider.provider}`,
            label: providerDisplayLabel(provider.provider),
            value: providerStatusDisplayLabel(provider.status),
            detail: provider.nextCheck,
            tone:
              ["available", "configured", "ready", "ok"].includes(
                provider.status.toLowerCase(),
              )
                ? "ok"
                : "warning",
          });
        });
    }

    preparedAgentMcpRequests.forEach((request) => {
      request.mcpProfiles.forEach((profile) => mcpProfiles.add(profile));
      request.requiredArtifacts.forEach((artifact) =>
        requiredArtifacts.add(artifact),
      );
      if (request.inputId && request.mcpProfiles.length === 0) {
        mcpProfiles.add(request.inputId);
      }
    });
    activeRunUiSummary?.nodes.forEach((node) => {
      node.mcp_profiles?.forEach((profile) => {
        if (profile) mcpProfiles.add(profile);
      });
      node.mcp_inputs?.forEach((input) => {
        if (input.id) mcpProfiles.add(input.id);
      });
      node.skills?.forEach((skill) => {
        const label = skill.label || skill.id;
        if (label) skills.add(label);
      });
      node.outputs?.forEach((output) => {
        if (output.artifact) requiredArtifacts.add(output.artifact);
      });
    });

    const warnings = [
      ...(preparedProviderReadiness?.blockingReasons ?? []),
      ...(preparedProviderReadiness?.warnings ?? []),
      ...(activeRunUiSummary?.nodes ?? [])
        .map((node) => node.mcp_availability)
        .filter(
          (availability) =>
            availability?.user_message &&
            !["direct", "not_requested"].includes(
              String(availability.status ?? "").toLowerCase(),
            ),
        )
        .flatMap((availability) =>
          [
            availability?.user_message ?? "",
            availability?.action ?? "",
          ].filter(Boolean),
        ),
    ].map(compactReasonLabel);

    return {
      rows,
      mcpProfiles: Array.from(mcpProfiles),
      skills: Array.from(skills),
      requiredArtifacts: Array.from(requiredArtifacts),
      warnings: Array.from(new Set(warnings)),
    };
  }, [
    activeRunUiSummary,
    preparedAgentMcpRequests,
    preparedProviderReadiness,
    preparedRun,
  ]);
  const runPanelProgress = useMemo(() => {
    const completed = runPhaseCards.filter(
      (phase) => runStatusDisplayLabel(phase.status) === "已完成",
    ).length;
    const failedIndex = runPhaseCards.findIndex(
      (phase) => runStatusDisplayLabel(phase.status) === "失败",
    );
    const runningIndex = runPhaseCards.findIndex(
      (phase) => runStatusDisplayLabel(phase.status) === "进行中",
    );
    const currentIndex =
      failedIndex >= 0
        ? failedIndex
        : runningIndex >= 0
          ? runningIndex
          : Math.min(completed, runPhaseCards.length - 1);
    const total = Math.max(runPhaseCards.length, 1);
    return {
      completed,
      currentIndex,
      percent: Math.round((completed / total) * 100),
      total,
    };
  }, [runPhaseCards]);
  const visibleDeliveryArtifacts = useMemo(() => {
    const manifestArtifacts = artifactManifest?.artifacts ?? [];
    const outputArtifacts = new Set(
      selectedWorkflowOutputs
        .map((output) => String(output.artifact ?? "").trim())
        .filter(Boolean),
    );
    return manifestArtifacts
      .filter((artifact) => {
        if (artifact.kind === "task_bundle") return true;
        if (artifact.kind === "workflow_output_materialization") return true;
        if (artifact.kind === "evidence_validation") return true;
        if (artifact.kind === "semantic_import_outputs") return true;
        if (artifact.relative_path.startsWith("agent_runs/")) return false;
        return outputArtifacts.has(artifact.relative_path);
      })
      .slice(0, 8);
  }, [artifactManifest, selectedWorkflowOutputs]);
  const runPanelDeliverables = useMemo(
    () =>
      (activeRunUiSummary?.deliverables ?? []).filter(
        (item) => item.path || item.artifact,
      ),
    [activeRunUiSummary],
  );
  const visibleTaskRunEvents = useMemo(
    () => taskRunEvents.slice(-8),
    [taskRunEvents],
  );
  const selectedWorkflowAudit = useMemo(
    () =>
      workflows.find((workflow) => workflow.id === selectedWorkflowId)?.audit,
    [selectedWorkflowId, workflows],
  );
  const workflowDraftAuditSummary = useMemo(
    () => workflowDraftAudit(workflowJson),
    [workflowJson],
  );
  const builderInputItems = useMemo(
    () => safeWorkflowSpecList(builderInputSpec, "free_text"),
    [builderInputSpec],
  );
  const builderOutputItems = useMemo(
    () => safeWorkflowSpecList(builderOutputSpec, "json"),
    [builderOutputSpec],
  );
  const builderOutputPreview = useMemo(() => {
    try {
      const requiredArtifacts = parseCommaSeparated(builderArtifacts);
      const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
      const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
      const semanticImports = parseJsonObject(builderSemanticImports || "{}");
      return parseWorkflowSpecList(builderOutputSpec, "json").map((output) => {
        const artifact =
          output.artifact ||
          outputArtifactForSpec(output.id, output.type, requiredArtifacts);
        const schema =
          output.type === "json"
            ? outputSchemaForSpec(output.id, outputSchemas)
            : null;
        const evidenceMemory =
          output.type === "json" || output.type === "scope_report"
            ? outputEvidenceMappingForSpec(output.id, evidenceMappings)
            : null;
        const semanticImport =
          output.type === "test_cases"
            ? outputSemanticImportForSpec(
                output.id,
                output.type,
                semanticImports,
              )
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
  const workflowContractNodes = useMemo<WorkflowCanvasNode[]>(() => {
    const requiredArtifacts = parseCommaSeparated(builderArtifacts);
    return [
      {
        id: "inputs",
        kind: "input",
        title: "输入",
        subtitle: builderInputItems.length
          ? `${builderInputItems.length} 个入口`
          : "等待输入定义",
        body: builderInputItems
          .slice(0, 4)
          .map(
            (item) =>
              `${workflowItemLabel(builderInputLabels, item.id)}:${item.type}`,
          ),
        x: 36,
        y: 72,
        source: "contract",
      },
      {
        id: "source-context",
        kind: "context",
        title: "源码上下文",
        subtitle: "GitNexus / CGC",
        body: ["优先读取工作区源码", "复用索引与调用图产物"],
        x: 300,
        y: 220,
        source: "contract",
      },
      {
        id: "skills-mcp",
        kind: "context",
        title: "Skills / MCP",
        subtitle: `${selectedBuilderSkillOptions.length} skills · ${builderMcpCompatibility.label}`,
        body: [
          ...selectedBuilderSkillOptions
            .slice(0, 3)
            .map((skill) => skill.label),
          builderMcpProfile ? `MCP: ${builderMcpProfile}` : "MCP: 未启用",
        ],
        x: 565,
        y: 88,
        source: "contract",
      },
      {
        id: "agent-task",
        kind: "agent",
        title: builderProvider || "智能体",
        subtitle: builderMcpProfile
          ? `MCP: ${builderMcpProfile}`
          : "无 MCP 配置",
        body: [builderScenario, builderGoal.trim().slice(0, 72) || "等待目标"],
        x: 840,
        y: 295,
        source: "contract",
      },
      {
        id: "outputs",
        kind: "output",
        title: "输出",
        subtitle: builderOutputItems.length
          ? `${builderOutputItems.length} 个契约`
          : "等待输出定义",
        body: [
          ...builderOutputItems
            .slice(0, 3)
            .map((item) =>
              item.artifact
                ? `${workflowItemLabel(builderOutputLabels, item.id)} -> ${item.artifact}`
                : `${workflowItemLabel(builderOutputLabels, item.id)}:${item.type}`,
          ),
          "sfmea / black_box_cases",
        ],
        x: 1120,
        y: 155,
        source: "contract",
      },
      {
        id: "validation",
        kind: "verify",
        title: "验收",
        subtitle: `${requiredArtifacts.length} 个必需产物`,
        body: [
          `schema:${workflowDraftAuditSummary.warnings.length === 0 ? "ready" : "check"}`,
          `evidence:${workflowDraftAuditSummary.evidenceMemoryOutputCount}`,
          `semantic:${workflowDraftAuditSummary.semanticImportOutputCount}`,
        ],
        x: 1260,
        y: 500,
        source: "contract",
      },
    ];
  }, [
    builderArtifacts,
    builderGoal,
    builderInputItems,
    builderInputLabels,
    builderMcpProfile,
    builderOutputItems,
    builderOutputLabels,
    builderProvider,
    builderScenario,
    builderMcpCompatibility.label,
    selectedBuilderSkillOptions,
    workflowDraftAuditSummary.evidenceMemoryOutputCount,
    workflowDraftAuditSummary.semanticImportOutputCount,
    workflowDraftAuditSummary.warnings.length,
  ]);
  const workflowCanvasNodes = useMemo<WorkflowCanvasNode[]>(
    () =>
      [...workflowContractNodes, ...workflowExtraNodes]
        .filter((node) => !workflowHiddenNodeIds.includes(node.id))
        .map((node) => {
          const override = workflowNodePositions[node.id];
          const title = workflowNodeTitles[node.id];
          const config = workflowNodeConfigs[node.id];
          return {
            ...node,
            ...(override ? override : {}),
            ...(title ? { title } : {}),
            ...(config ? { config: { ...(node.config ?? {}), ...config } } : {}),
          };
        }),
    [
      workflowContractNodes,
      workflowExtraNodes,
      workflowHiddenNodeIds,
      workflowNodeConfigs,
      workflowNodePositions,
      workflowNodeTitles,
    ],
  );
  const defaultWorkflowCanvasEdges = useMemo<WorkflowCanvasEdge[]>(
    () => [
      {
        id: "edge-inputs-source-context",
        source: "inputs",
        target: "source-context",
        label: "源码/文件",
      },
      {
        id: "edge-source-context-agent",
        source: "source-context",
        target: "agent-task",
        label: "证据",
      },
      {
        id: "edge-skills-agent",
        source: "skills-mcp",
        target: "agent-task",
        label: "MCP/Skills",
      },
      {
        id: "edge-agent-outputs",
        source: "agent-task",
        target: "outputs",
        label: "产物",
      },
      {
        id: "edge-outputs-validation",
        source: "outputs",
        target: "validation",
        label: "验收",
      },
    ],
    [],
  );
  const visibleWorkflowCanvasEdges = useMemo(() => {
    const visibleNodeIds = new Set(workflowCanvasNodes.map((node) => node.id));
    const hiddenEdgeIds = new Set(workflowHiddenEdgeIds);
    return [...defaultWorkflowCanvasEdges, ...workflowCanvasEdges].filter(
      (edge) =>
        visibleNodeIds.has(edge.source) &&
        visibleNodeIds.has(edge.target) &&
        !hiddenEdgeIds.has(edge.id),
    );
  }, [
    defaultWorkflowCanvasEdges,
    workflowCanvasEdges,
    workflowCanvasNodes,
    workflowHiddenEdgeIds,
  ]);
  const activeWorkflowNode = useMemo(
    () =>
      workflowCanvasNodes.find((node) => node.id === activeWorkflowNodeId) ??
      null,
    [activeWorkflowNodeId, workflowCanvasNodes],
  );

  function workflowLayoutSnapshot(
    nodes: WorkflowCanvasNode[] = workflowCanvasNodes,
    hiddenNodeIds: string[] = workflowHiddenNodeIds,
    edges: WorkflowCanvasEdge[] = workflowCanvasEdges,
    hiddenEdgeIds: string[] = workflowHiddenEdgeIds,
  ): WorkflowCanvasLayout {
    return {
      nodes: nodes.map((node) => ({
        id: node.id,
        kind: node.kind,
        title: node.title,
        subtitle: node.subtitle,
        x: node.x,
        y: node.y,
        source: node.source,
        ...(node.config ? { config: node.config } : {}),
      })),
      edges,
      hidden_edge_ids: hiddenEdgeIds,
      hidden_node_ids: hiddenNodeIds,
    };
  }

  function mergeWorkflowLayoutIntoJson(
    layout: WorkflowCanvasLayout = workflowLayoutSnapshot(),
  ) {
    setWorkflowJson((current) => {
      try {
        const payload = parseJsonObject(current || "{}");
        const ui =
          payload.ui && typeof payload.ui === "object"
            ? (payload.ui as Record<string, unknown>)
            : {};
        return pretty({
          ...payload,
          ui: {
            ...ui,
            layout,
          },
        });
      } catch {
        return current;
      }
    });
  }

  function applyWorkflowLayout(payload: unknown) {
    const layout = workflowLayoutFromPayload(payload);
    if (!layout) {
      setWorkflowNodePositions({});
      setWorkflowExtraNodes([]);
      setWorkflowHiddenNodeIds([]);
      setWorkflowNodeTitles({});
      setWorkflowNodeConfigs({});
      setWorkflowCanvasEdges([]);
      setWorkflowHiddenEdgeIds([]);
      return;
    }
    const positions: Record<string, WorkflowNodePosition> = {};
    const titles: Record<string, string> = {};
    const configs: Record<string, Record<string, unknown>> = {};
    const extras: WorkflowCanvasNode[] = [];
    for (const node of layout.nodes) {
      positions[node.id] = clampWorkflowNodePosition({ x: node.x, y: node.y });
      titles[node.id] = node.title;
      if (node.config) configs[node.id] = node.config;
      if (node.source === "canvas") {
        extras.push({
          id: node.id,
          kind: node.kind,
          title: node.title,
          subtitle: node.subtitle,
          body: ["画布恢复节点", "来自 workflow ui.layout"],
          x: node.x,
          y: node.y,
          source: "canvas",
          ...(node.config ? { config: node.config } : {}),
        });
      }
    }
    setWorkflowNodePositions(positions);
    setWorkflowNodeTitles(titles);
    setWorkflowNodeConfigs(configs);
    const defaultWorkflowCanvasEdgeIds = new Set(
      defaultWorkflowCanvasEdges.map((edge) => edge.id),
    );
    setWorkflowCanvasEdges(
      (layout.edges ?? []).filter(
        (edge) => !defaultWorkflowCanvasEdgeIds.has(edge.id),
      ),
    );
    setWorkflowHiddenEdgeIds(layout.hidden_edge_ids ?? []);
    setWorkflowExtraNodes(extras);
    setWorkflowHiddenNodeIds(layout.hidden_node_ids);
    const visibleNodeIds = new Set(
      layout.nodes
        .filter((node) => !layout.hidden_node_ids.includes(node.id))
        .map((node) => node.id),
    );
    const activeNodeIsPersistedCanvasNode = workflowExtraNodes.some(
      (node) => node.id === activeWorkflowNodeId,
    );
    if (
      activeWorkflowNodeId &&
      activeNodeIsPersistedCanvasNode &&
      !visibleNodeIds.has(activeWorkflowNodeId)
    ) {
      setActiveWorkflowNodeId("");
    }
  }

  function startWorkflowNodeDrag(
    event: ReactPointerEvent<HTMLElement>,
    node: WorkflowCanvasNode,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setActiveWorkflowNodeId(node.id);
    workflowDragRef.current = {
      id: node.id,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: node.x,
      startY: node.y,
      moved: false,
    };
  }

  function moveWorkflowNode(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    if (
      Math.abs(event.clientX - drag.startClientX) > 2 ||
      Math.abs(event.clientY - drag.startClientY) > 2
    ) {
      drag.moved = true;
    }
    const nextPosition = clampWorkflowNodePosition({
      x: drag.startX + event.clientX - drag.startClientX,
      y: drag.startY + event.clientY - drag.startClientY,
    });
    setWorkflowNodePositions((current) => ({
      ...current,
      [drag.id]: nextPosition,
    }));
  }

  function endWorkflowNodeDrag(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    workflowDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setActiveWorkflowNodeId(drag.id);
    if (!drag.moved) return;
    const node = workflowCanvasNodes.find((item) => item.id === drag.id);
    if (node) {
      setMessage(`节点位置已更新: ${node.title}`);
      window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
    }
  }

  function appendCommaSpec(current: string, item: string): string {
    const items = parseCommaSeparated(current);
    if (items.includes(item)) return current;
    return [...items, item].join(", ");
  }

  function addBuilderInputContract() {
    const id = newWorkflowInputId.trim();
    const label = newWorkflowInputName.trim() || id;
    const type = newWorkflowInputType.trim() || "free_text";
    const resolver =
      newWorkflowInputResolver && newWorkflowInputResolver !== "manual"
        ? newWorkflowInputResolver
        : "";
    if (!id) {
      setMessage("请输入输入契约 ID");
      return;
    }
    const spec = workflowSpecToText({ id, type, resolver });
    const nextInputSpec = appendCommaSpec(builderInputSpec, spec);
    const nextInputLabels = { ...builderInputLabels, [id]: label };
    flushSync(() => {
      setBuilderInputSpec(nextInputSpec);
      setBuilderInputLabels(nextInputLabels);
      setNewWorkflowInputName("");
      setNewWorkflowInputId("");
    });
    setMessage(`输入契约已添加: ${label}`);
    window.setTimeout(() => {
      try {
        generateWorkflowFromBuilder({
          inputSpec: nextInputSpec,
          inputLabels: nextInputLabels,
        });
      } catch (error) {
        mergeWorkflowLayoutIntoJson();
        setMessage(
          error instanceof Error
            ? `输入契约已添加，但草稿同步失败: ${error.message}`
            : `输入契约已添加，但草稿同步失败`,
        );
      }
    }, 0);
  }

  function addBuilderOutputContract() {
    const id = newWorkflowOutputId.trim();
    const label = newWorkflowOutputName.trim() || id;
    const type = newWorkflowOutputType.trim() || "json";
    const artifact =
      newWorkflowOutputArtifact.trim() ||
      outputArtifactForSpec(id, type, parseCommaSeparated(builderArtifacts));
    if (!id) {
      setMessage("请输入输出契约 ID");
      return;
    }
    const spec = workflowSpecToText({ id, type, artifact });
    const nextOutputSpec = appendCommaSpec(builderOutputSpec, spec);
    const nextArtifacts = appendCommaSpec(builderArtifacts, artifact);
    const nextOutputLabels = { ...builderOutputLabels, [id]: label };
    flushSync(() => {
      setBuilderOutputSpec(nextOutputSpec);
      setBuilderArtifacts(nextArtifacts);
      setBuilderOutputLabels(nextOutputLabels);
      setNewWorkflowOutputName("");
      setNewWorkflowOutputId("");
      setNewWorkflowOutputArtifact("");
    });
    setMessage(`输出契约已添加: ${label}`);
    window.setTimeout(() => {
      try {
        generateWorkflowFromBuilder({
          outputSpec: nextOutputSpec,
          artifacts: nextArtifacts,
          outputLabels: nextOutputLabels,
        });
      } catch (error) {
        mergeWorkflowLayoutIntoJson();
        setMessage(
          error instanceof Error
            ? `输出契约已添加，但草稿同步失败: ${error.message}`
            : `输出契约已添加，但草稿同步失败`,
        );
      }
    }, 0);
  }

  function workflowCanvasPointFromClient(
    clientX: number,
    clientY: number,
  ): WorkflowNodePosition {
    const rect = workflowCanvasInnerRef.current?.getBoundingClientRect();
    return {
      x: rect ? clientX - rect.left : 0,
      y: rect ? clientY - rect.top : 0,
    };
  }

  function workflowEdgePoints(source: WorkflowCanvasNode, target: WorkflowCanvasNode) {
    return {
      x1: source.x + WORKFLOW_NODE_WIDTH - 2,
      y1: source.y + 42,
      x2: target.x + 2,
      y2: target.y + 42,
    };
  }

  function workflowEdgePath(x1: number, y1: number, x2: number, y2: number) {
    const control = Math.max(88, Math.min(220, Math.abs(x2 - x1) * 0.52));
    const direction = x2 >= x1 ? 1 : -1;
    return `M ${x1} ${y1} C ${x1 + direction * control} ${y1}, ${x2 - direction * control} ${y2}, ${x2} ${y2}`;
  }

  function connectWorkflowNodes(sourceId: string, targetId: string) {
    if (sourceId === targetId) {
      setMessage("不能连接到当前节点自身");
      setWorkflowPendingConnectionSourceId("");
      return;
    }
    const source = workflowCanvasNodes.find((node) => node.id === sourceId);
    const target = workflowCanvasNodes.find(
      (node) => node.id === targetId,
    );
    if (!source || !target) return;
    const allEdges = [...defaultWorkflowCanvasEdges, ...workflowCanvasEdges];
    const exists = allEdges.some(
      (edge) =>
        edge.source === sourceId &&
        edge.target === targetId &&
        !workflowHiddenEdgeIds.includes(edge.id),
    );
    if (exists) {
      setMessage("这两个节点已经连线");
      return;
    }
    const nextEdges = [
      ...workflowCanvasEdges,
      {
        id: `edge-${sourceId}-${targetId}-${Date.now().toString(36)}`,
        source: sourceId,
        target: targetId,
        label: `${source.title} -> ${target.title}`,
      },
    ];
    setWorkflowCanvasEdges(nextEdges);
    setActiveWorkflowNodeId(sourceId);
    setWorkflowPendingConnectionSourceId("");
    setMessage(`连线已添加: ${source.title} -> ${target.title}`);
    mergeWorkflowLayoutIntoJson(
      workflowLayoutSnapshot(
        workflowCanvasNodes,
        workflowHiddenNodeIds,
        nextEdges,
        workflowHiddenEdgeIds,
      ),
    );
  }

  function deleteWorkflowEdge(edge: WorkflowCanvasEdge) {
    const isCustomEdge = workflowCanvasEdges.some((item) => item.id === edge.id);
    const nextEdges = workflowCanvasEdges.filter((item) => item.id !== edge.id);
    const nextHiddenEdgeIds = isCustomEdge
      ? workflowHiddenEdgeIds
      : Array.from(new Set([...workflowHiddenEdgeIds, edge.id]));
    setWorkflowCanvasEdges(nextEdges);
    setWorkflowHiddenEdgeIds(nextHiddenEdgeIds);
    setMessage(`连线已删除: ${edge.label || edge.id}`);
    mergeWorkflowLayoutIntoJson(
      workflowLayoutSnapshot(
        workflowCanvasNodes,
        workflowHiddenNodeIds,
        nextEdges,
        nextHiddenEdgeIds,
      ),
    );
  }

  function selectWorkflowConnectionSource(
    event: ReactMouseEvent<HTMLElement>,
    source: WorkflowCanvasNode,
  ) {
    event.preventDefault();
    event.stopPropagation();
    setActiveWorkflowNodeId(source.id);
    setWorkflowPendingConnectionSourceId(source.id);
    setMessage(`已选择连线起点: ${source.title}`);
  }

  function connectWorkflowTargetFromPending(
    event: ReactMouseEvent<HTMLElement>,
    target: WorkflowCanvasNode,
  ) {
    event.preventDefault();
    event.stopPropagation();
    if (!workflowPendingConnectionSourceId) {
      setActiveWorkflowNodeId(target.id);
      return;
    }
    connectWorkflowNodes(workflowPendingConnectionSourceId, target.id);
  }

  function startWorkflowConnectionDrag(
    event: ReactPointerEvent<HTMLElement>,
    source: WorkflowCanvasNode,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const start = {
      sourceId: source.id,
      x1: source.x + WORKFLOW_NODE_WIDTH - 2,
      y1: source.y + 42,
      ...workflowCanvasPointFromClient(event.clientX, event.clientY),
    };
    const draft = {
      sourceId: source.id,
      x1: start.x1,
      y1: start.y1,
      x2: start.x,
      y2: start.y,
    };
    workflowConnectionDragRef.current = draft;
    setWorkflowDraftEdge(draft);
    const moveDraft = (pointerEvent: PointerEvent) => {
      const drag = workflowConnectionDragRef.current;
      if (!drag) return;
      const point = workflowCanvasPointFromClient(
        pointerEvent.clientX,
        pointerEvent.clientY,
      );
      const next = { ...drag, x2: point.x, y2: point.y };
      workflowConnectionDragRef.current = next;
      setWorkflowDraftEdge(next);
    };
    const finishDraft = (pointerEvent: PointerEvent) => {
      window.removeEventListener("pointermove", moveDraft);
      window.removeEventListener("pointerup", finishDraft);
      const drag = workflowConnectionDragRef.current;
      workflowConnectionDragRef.current = null;
      setWorkflowDraftEdge(null);
      if (!drag) return;
      const targetElement = document
        .elementFromPoint(pointerEvent.clientX, pointerEvent.clientY)
        ?.closest("[data-workflow-target-node-id]");
      const targetId =
        targetElement instanceof HTMLElement
          ? targetElement.dataset.workflowTargetNodeId
          : "";
      if (targetId) {
        connectWorkflowNodes(drag.sourceId, targetId);
      }
    };
    window.addEventListener("pointermove", moveDraft);
    window.addEventListener("pointerup", finishDraft, { once: true });
  }

  function startWorkflowBoardPan(event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    const board = workflowBoardRef.current;
    if (!board) return;
    event.preventDefault();
    setActiveWorkflowNodeId("");
    const element = event.currentTarget;
    element.setPointerCapture(event.pointerId);
    workflowBoardPanRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScrollLeft: board.scrollLeft,
      startScrollTop: board.scrollTop,
      moved: false,
    };
  }

  function moveWorkflowBoardPan(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowBoardPanRef.current;
    const board = workflowBoardRef.current;
    if (!drag || !board || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    const dx = event.clientX - drag.startClientX;
    const dy = event.clientY - drag.startClientY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) drag.moved = true;
    board.scrollLeft = drag.startScrollLeft - dx;
    board.scrollTop = drag.startScrollTop - dy;
  }

  function endWorkflowBoardPan(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowBoardPanRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    workflowBoardPanRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function addPaletteNodeToCanvas(
    moduleId: string,
    clientX: number,
    clientY: number,
  ) {
    const paletteModule = WORKFLOW_MODULE_PALETTE.find(
      (item) => item.id === moduleId,
    );
    const canvasRect = workflowCanvasInnerRef.current?.getBoundingClientRect();
    if (!paletteModule || !canvasRect) return;
    const position = clampWorkflowNodePosition({
      x: clientX - canvasRect.left - WORKFLOW_NODE_WIDTH / 2,
      y: clientY - canvasRect.top - WORKFLOW_NODE_HEIGHT / 2,
    });
    const nodeId = `canvas-${moduleId}-${Date.now().toString(36)}`;
    const contractId = nodeId.replace(/-/g, "_");
    const nodeConfig: Record<string, unknown> =
      paletteModule.id === "input"
        ? { id: contractId, type: "free_text", label: paletteModule.label }
        : paletteModule.id === "output"
          ? {
              id: contractId,
              type: "json",
              label: paletteModule.label,
              artifact: `${contractId}.json`,
            }
          : paletteModule.id === "agent"
            ? {
                id: contractId,
                provider: builderProvider.trim() || "claude-code",
                mcp_profile: builderMcpProfile.trim(),
                skill_ids: builderSkillIds,
                goal: builderGoal.trim(),
              }
            : paletteModule.id === "mcp"
              ? { mcp_profile: builderMcpProfile.trim() || "codehub-mcp" }
              : paletteModule.id === "gitnexus"
                ? { mcp_profile: "gitnexus" }
                : paletteModule.id === "cgc"
                  ? { mcp_profile: "cgc" }
                  : paletteModule.id === "skills"
                    ? {
                        skill_ids: builderSkillIds,
                        skill_instructions: selectedBuilderSkillOptions.map(
                          (skill) => ({
                            id: skill.id,
                            label: skill.label,
                            source: skill.source,
                            prompt_hint:
                              skill.prompt_hint ||
                              skill.description ||
                              skill.label,
                          }),
                        ),
                      }
                    : {};
    const node: WorkflowCanvasNode = {
      id: nodeId,
      kind: workflowPaletteKind(paletteModule.id),
      title: paletteModule.label,
      subtitle: workflowPaletteSubtitle(paletteModule.id),
      body:
        paletteModule.id === "input" ||
        paletteModule.id === "agent" ||
        paletteModule.id === "output"
          ? ["画布新增节点", "已同步草稿契约"]
          : ["画布新增节点", "连接 Agent 后进入执行契约"],
      x: position.x,
      y: position.y,
      source: "canvas",
      config: nodeConfig,
    };
    const nextExtraNodes = [...workflowExtraNodes, node];
    setWorkflowExtraNodes(nextExtraNodes);
    setWorkflowNodeTitles((current) => ({ ...current, [node.id]: node.title }));
    if (paletteModule.id === "agent") {
      setBuilderGoal((current) =>
        current.includes(`新增智能体节点 ${contractId}`)
          ? current
          : `${current.trim()}\n\n新增智能体节点 ${contractId}: ${paletteModule.label}`.trim(),
      );
    }
    setActiveWorkflowNodeId(nodeId);
    setMessage(
      paletteModule.id === "input" ||
        paletteModule.id === "agent" ||
        paletteModule.id === "output"
        ? `画布节点已添加: ${paletteModule.label}；已同步草稿契约。`
        : `画布节点已添加: ${paletteModule.label}；连接到 Agent 并保存后会进入执行契约。`,
    );
    window.setTimeout(
      () => mergeWorkflowLayoutIntoJson(workflowLayoutSnapshot([...workflowCanvasNodes, node])),
      0,
    );
  }

  function addPaletteNodeToCanvasViewportCenter(moduleId: string) {
    const boardRect = workflowBoardRef.current?.getBoundingClientRect();
    if (!boardRect) return;
    addPaletteNodeToCanvas(
      moduleId,
      boardRect.left + Math.min(boardRect.width * 0.58, 560),
      boardRect.top + Math.min(boardRect.height * 0.45, 360),
    );
  }

  function startPalettePointerDrag(moduleId: string, event: ReactPointerEvent) {
    palettePointerDragRef.current = {
      moduleId,
      startX: event.clientX,
      startY: event.clientY,
    };
    const finishDrag = (pointerEvent: PointerEvent) => {
      const drag = palettePointerDragRef.current;
      palettePointerDragRef.current = null;
      window.removeEventListener("pointercancel", cancelDrag);
      if (!drag) return;
      const moved =
        Math.abs(pointerEvent.clientX - drag.startX) > 8 ||
        Math.abs(pointerEvent.clientY - drag.startY) > 8;
      const boardRect = workflowBoardRef.current?.getBoundingClientRect();
      const droppedOnBoard =
        boardRect &&
        pointerEvent.clientX >= boardRect.left &&
        pointerEvent.clientX <= boardRect.right &&
        pointerEvent.clientY >= boardRect.top &&
        pointerEvent.clientY <= boardRect.bottom;
      if (moved && droppedOnBoard) {
        addPaletteNodeToCanvas(
          drag.moduleId,
          pointerEvent.clientX,
          pointerEvent.clientY,
        );
      }
    };
    const cancelDrag = () => {
      palettePointerDragRef.current = null;
      window.removeEventListener("pointerup", finishDrag);
    };
    window.addEventListener("pointerup", finishDrag, { once: true });
    window.addEventListener("pointercancel", cancelDrag, { once: true });
  }

  function renameActiveWorkflowNode(title: string) {
    if (!activeWorkflowNode) return;
    setWorkflowNodeTitles((current) => ({
      ...current,
      [activeWorkflowNode.id]: title,
    }));
    setWorkflowExtraNodes((current) =>
      current.map((node) =>
        node.id === activeWorkflowNode.id ? { ...node, title } : node,
      ),
    );
    window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
  }

  function updateActiveWorkflowNodeConfig(patch: Record<string, unknown>) {
    if (!activeWorkflowNode) return;
    const nodeId = activeWorkflowNode.id;
    const compactPatch = Object.fromEntries(
      Object.entries(patch).filter(([, value]) => value !== undefined),
    );
    setWorkflowNodeConfigs((current) => {
      const currentConfig = current[nodeId] ?? {};
      const nextConfig = {
        ...(activeWorkflowNode.config ?? {}),
        ...currentConfig,
        ...compactPatch,
      };
      return { ...current, [nodeId]: nextConfig };
    });
    setWorkflowExtraNodes((current) =>
      current.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              config: {
                ...(node.config ?? {}),
                ...compactPatch,
              },
            }
          : node,
      ),
    );
    window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
  }

  function workflowNodeConfigString(key: string, fallback = "") {
    if (!activeWorkflowNode?.config) return fallback;
    const value = activeWorkflowNode.config[key];
    if (Array.isArray(value)) return value.map(String).join(", ");
    if (value === undefined || value === null) return fallback;
    if (typeof value === "string" && !value.trim()) return fallback;
    return String(value);
  }

  function copyActiveWorkflowNode() {
    if (!activeWorkflowNode) return;
    const nodeId = `canvas-copy-${Date.now().toString(36)}`;
    const position = clampWorkflowNodePosition({
      x: activeWorkflowNode.x + 36,
      y: activeWorkflowNode.y + 36,
    });
    const copyNode: WorkflowCanvasNode = {
      ...activeWorkflowNode,
      id: nodeId,
      title: `${activeWorkflowNode.title} 副本`,
      subtitle: activeWorkflowNode.subtitle || "复制节点",
      body: ["复制的画布节点", "未自动改写字段契约"],
      x: position.x,
      y: position.y,
      source: "canvas",
    };
    const nextExtraNodes = [...workflowExtraNodes, copyNode];
    setWorkflowExtraNodes(nextExtraNodes);
    setWorkflowNodeTitles((current) => ({
      ...current,
      [nodeId]: copyNode.title,
    }));
    setActiveWorkflowNodeId(nodeId);
    setMessage(`节点已复制: ${copyNode.title}`);
    window.setTimeout(
      () => mergeWorkflowLayoutIntoJson(workflowLayoutSnapshot([...workflowCanvasNodes, copyNode])),
      0,
    );
  }

  function deleteActiveWorkflowNode() {
    if (!activeWorkflowNode) return;
    const nextNodes = workflowCanvasNodes.filter(
      (node) => node.id !== activeWorkflowNode.id,
    );
    let nextHidden = workflowHiddenNodeIds;
    if (activeWorkflowNode.source === "canvas") {
      setWorkflowExtraNodes((current) =>
        current.filter((node) => node.id !== activeWorkflowNode.id),
      );
    } else {
      nextHidden = Array.from(
        new Set([...workflowHiddenNodeIds, activeWorkflowNode.id]),
      );
      setWorkflowHiddenNodeIds(nextHidden);
    }
    setWorkflowNodePositions((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    setWorkflowNodeTitles((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    setWorkflowNodeConfigs((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    const nextEdges = workflowCanvasEdges.filter(
      (edge) =>
        edge.source !== activeWorkflowNode.id &&
        edge.target !== activeWorkflowNode.id,
    );
    setWorkflowCanvasEdges(nextEdges);
    setActiveWorkflowNodeId("");
    setMessage(
      activeWorkflowNode.source === "canvas"
        ? `节点已删除: ${activeWorkflowNode.title}`
        : `契约节点已从画布隐藏: ${activeWorkflowNode.title}`,
    );
    mergeWorkflowLayoutIntoJson(
      workflowLayoutSnapshot(nextNodes, nextHidden, nextEdges),
    );
  }

  function resetActiveWorkflowNodePosition() {
    if (!activeWorkflowNode) return;
    setWorkflowNodePositions((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    setMessage(`节点位置已重置: ${activeWorkflowNode.title}`);
    window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
  }

  function applyWorkspaceSelection(workspace: Workspace) {
    workspaceAutoSelectionDoneRef.current = true;
    try {
      window.localStorage.setItem(WORKBENCH_WORKSPACE_STORAGE_KEY, workspace.id);
    } catch {
      // Browser storage can be disabled; the active page still keeps its selection.
    }
    setWorkspaceId(workspace.id);
    setRepoPath(workspace.repo_path);
    setInputsJson((current) =>
      updateInputsJsonValue(
        current || "{}",
        { id: "repo_path", type: "directory" },
        workspace.repo_path,
      ),
    );
  }

  function workflowDefinitionForRun(workflowId: string): WorkflowDefinition | null {
    const registered = workflows.find((workflow) => workflow.id === workflowId);
    if (registered) return registered;
    const preset = workflowPresets.find(
      (item) => item.definition.id === workflowId || item.id === workflowId,
    );
    return preset?.definition ?? null;
  }

  function workflowInputDefaults(workflowId: string): Record<string, string> {
    const definition = workflowDefinitionForRun(workflowId);
    const nextInputs: Record<string, string> = {};
    for (const input of definition?.inputs ?? []) {
      if (!input || typeof input !== "object") continue;
      const inputId = String((input as Record<string, unknown>).id ?? "");
      if (!inputId) continue;
      nextInputs[inputId] = inputId === "repo_path" ? repoPath : "";
    }
    return nextInputs;
  }

  function selectRunWorkflow(workflowId: string) {
    selectedWorkflowIdRef.current = workflowId;
    setSelectedWorkflowId(workflowId);
    setInputsJson(pretty(workflowInputDefaults(workflowId)));
    setWorkflowInputsUpdated(true);
    window.setTimeout(() => setWorkflowInputsUpdated(false), 2200);
  }

  const loadWorkflows = useCallback(async (preferredWorkflowId = selectedWorkflowId) => {
    setLoading(true);
    setError(null);
    try {
      const [workflowResult, presetResult] = await Promise.allSettled([
        api.workbench.workflows.list(),
        api.workbench.workflows.presets(),
      ]);

      const coreErrors: string[] = [];
      if (workflowResult.status === "fulfilled") {
        const nextWorkflowData = workflowResult.value;
        setWorkflows(nextWorkflowData);
        if (nextWorkflowData.length > 0) {
          const selectionRequestIsCurrent =
            preferredWorkflowId === selectedWorkflowIdRef.current;
          const selectedWorkflow = nextWorkflowData.find(
            (item) => item.id === preferredWorkflowId,
          );
          const fallbackWorkflow = selectedWorkflow ?? nextWorkflowData[0];
          const selectedLooksLikeKnownPreset =
            CORE_WORKFLOW_PRESET_IDS.has(preferredWorkflowId) ||
            preferredWorkflowId in WORKFLOW_BUILDER_SCENARIOS;
          const preservingUnsavedDraft =
            activeWorkbenchView === "workflow" &&
            Boolean(localWorkflowDraftIdRef.current) &&
            preferredWorkflowId === localWorkflowDraftIdRef.current &&
            !selectedWorkflow;
          if (
            selectionRequestIsCurrent &&
            !selectedWorkflow &&
            !selectedLooksLikeKnownPreset &&
            !preservingUnsavedDraft
          ) {
            selectedWorkflowIdRef.current = fallbackWorkflow.id;
            setSelectedWorkflowId(fallbackWorkflow.id);
          }
          if (selectionRequestIsCurrent) {
            setWorkflowJson((currentJson) => {
              const currentId = workflowIdFromJson(currentJson);
              const currentIsEmpty = !currentJson.trim() || !currentId;
              const currentIsDefault = currentId === DEFAULT_WORKFLOW.id;
              if (currentIsDefault || currentIsEmpty) {
                return pretty(fallbackWorkflow);
              }
              return currentJson;
            });
          }
          if (
            selectionRequestIsCurrent &&
            activeWorkbenchView === "workflow" &&
            !preservingUnsavedDraft
          ) {
            hydrateBuilderFromWorkflow(fallbackWorkflow);
          }
        }
      } else {
        coreErrors.push(
          workflowResult.reason instanceof Error
            ? workflowResult.reason.message
            : "Failed to load workflows",
        );
      }

      if (presetResult.status === "fulfilled") {
        const presetData = presetResult.value;
        setWorkflowPresets(presetData.items);
        if (!selectedPresetId && presetData.items.length > 0) {
          setSelectedPresetId(presetData.items[0].id);
        }
      } else {
        coreErrors.push(
          presetResult.reason instanceof Error
            ? presetResult.reason.message
            : "Failed to load workflow presets",
        );
      }

      const [
        taskRunResult,
        providerResult,
        workflowCapabilityResult,
        systemAuditResult,
        workspaceResult,
      ] = await Promise.allSettled([
        api.workbench.taskRuns.list({ limit: 10 }),
        api.workbench.providerCapabilities(),
        api.workbench.workflowCapabilities(),
        api.workbench.systemAudit(),
        api.workspaces.list(),
      ]);

      const diagnosticErrors: string[] = [];
      let recoverableRun: PreparedWorkbenchTaskRun | undefined;
      if (taskRunResult.status === "fulfilled") {
        const recentTaskRuns = taskRunResult.value.items;
        setTaskRuns(recentTaskRuns);
        recoverableRun = recentTaskRuns.find((run) =>
          ["queued", "running"].includes(taskRunRuntimeStatus(run)),
        );
      } else {
        diagnosticErrors.push("最近任务");
      }
      if (providerResult.status === "fulfilled") {
        setProviderMatrix(providerResult.value);
      } else {
        diagnosticErrors.push("执行器能力");
      }
      if (workflowCapabilityResult.status === "fulfilled") {
        setWorkflowCapabilities(workflowCapabilityResult.value);
        const defaults = (
          workflowCapabilityResult.value.skill_catalog ?? []
        )
          .filter((skill) => skill.default_enabled)
          .map((skill) => skill.id);
        if (defaults.length > 0) {
          setBuilderSkillIds((current) =>
            current.length > 0 ? current : defaults,
          );
        }
      } else {
        diagnosticErrors.push("工作流能力");
      }
      if (systemAuditResult.status === "fulfilled") {
        setSystemAudit(systemAuditResult.value);
      } else {
        diagnosticErrors.push("系统审计");
      }
      if (workspaceResult.status === "fulfilled") {
        const visibleWorkspaces = workspaceResult.value;
        setWorkspaces(visibleWorkspaces);
        const queryWorkspaceId =
          typeof window === "undefined"
            ? ""
            : workbenchWorkspaceIdFromSearchParams(
                new URLSearchParams(window.location.search),
              );
        const queryWorkspace = queryWorkspaceId
          ? visibleWorkspaces.find((workspace) => workspace.id === queryWorkspaceId)
          : null;
        let persistedWorkspace: Workspace | null = null;
        try {
          const persistedWorkspaceId = window.localStorage.getItem(
            WORKBENCH_WORKSPACE_STORAGE_KEY,
          );
          persistedWorkspace = persistedWorkspaceId
            ? visibleWorkspaces.find(
                (workspace) => workspace.id === persistedWorkspaceId,
              ) ?? null
            : null;
        } catch {
          persistedWorkspace = null;
        }
        if (
          (queryWorkspace || persistedWorkspace) &&
          !workspaceAutoSelectionDoneRef.current
        ) {
          applyWorkspaceSelection(queryWorkspace ?? persistedWorkspace!);
        }
        if (
          !workspaceAutoSelectionDoneRef.current &&
          !repoPath.trim() &&
          visibleWorkspaces.length > 0
        ) {
          const preferred =
            visibleWorkspaces.find((workspace) => workspace.indexed === 1) ??
            visibleWorkspaces[0];
          applyWorkspaceSelection(preferred);
        }
      } else {
        diagnosticErrors.push("工作空间列表");
      }

      if (
        activeWorkbenchView === "run" &&
        recoverableRun &&
        workspaceResult.status === "fulfilled" &&
        autoRestoredTaskRunRef.current !== recoverableRun.task_run_id
      ) {
        const visibleWorkspaces = workspaceResult.value;
        const recoverableWorkspace = visibleWorkspaces.find(
          (workspace) => workspace.id === recoverableRun?.workspace_id,
        );
        if (!recoverableWorkspace) {
          diagnosticErrors.push("运行中任务对应的工作空间已不存在，已停止自动恢复");
        } else {
          autoRestoredTaskRunRef.current = recoverableRun.task_run_id;
          void (async () => {
            try {
              await restoreTaskRun(recoverableRun!.task_run_id, visibleWorkspaces);
              setMessage(`任务已恢复 · ${recoverableRun!.task_run_id}`);
              startTaskRunPollingRef.current(recoverableRun!.task_run_id);
            } catch (err: unknown) {
              setError(err instanceof Error ? err.message : "恢复运行中任务失败");
            }
          })();
        }
      }

      if (coreErrors.length > 0) {
        setError(coreErrors.join("; "));
      } else if (diagnosticErrors.length > 0) {
        setError(
          `工作流已加载，部分诊断数据加载失败: ${diagnosticErrors.join("、")}`,
        );
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load workbench data",
      );
    } finally {
      setLoading(false);
    }
  // Hydration is a render-local helper; these scalar keys intentionally own reloads.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkbenchView, repoPath, selectedPresetId, selectedWorkflowId]);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  async function runAction(name: string, action: () => Promise<void>) {
    const currentBusyAction = busyActionRef.current;
    const canInterruptForCancel =
      name === "cancel-task-run" &&
      Boolean(currentBusyAction?.startsWith("execute-"));
    if (currentBusyAction && (!canInterruptForCancel || currentBusyAction === name)) {
      return;
    }
    const startedAt = performance.now();
    activeActionsRef.current.add(name);
    busyActionRef.current = name;
    flushSync(() => {
      setBusyAction(name);
    });
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      const remainingBusyMs =
        MIN_VISIBLE_BUSY_ACTION_MS - (performance.now() - startedAt);
      if (remainingBusyMs > 0) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, remainingBusyMs),
        );
      }
      activeActionsRef.current.delete(name);
      const activeActions = Array.from(activeActionsRef.current);
      const nextBusyAction =
        activeActions.find((item) => item === "cancel-task-run") ??
        activeActions[activeActions.length - 1] ??
        null;
      busyActionRef.current = nextBusyAction;
      setBusyAction(nextBusyAction);
    }
  }

  async function refreshArtifactManifest(taskRunId: string) {
    const manifest = await api.workbench.taskRuns.artifacts(taskRunId);
    setArtifactManifest(manifest);
  }

  function taskRunRuntimeStatus(run: PreparedWorkbenchTaskRun): string {
    return String(run.run_ui_summary?.status || run.status || "").toLowerCase();
  }

  function isTaskRunActiveStatus(status: string): boolean {
    return ["queued", "running", "prepared"].includes(status.trim().toLowerCase());
  }

  function taskRunSettledMessage(run: PreparedWorkbenchTaskRun): string {
    const status = runStatusDisplayLabel(
      run.run_ui_summary?.status_label || run.run_ui_summary?.status || run.status || "",
    );
    if (status === "已完成") {
      return `工作流执行已完成 · 任务 ${run.task_run_id}`;
    }
    if (status === "失败") {
      return `工作流执行失败 · 任务 ${run.task_run_id}`;
    }
    if (status === "已取消") {
      return `工作流执行已取消 · 任务 ${run.task_run_id}`;
    }
    return `工作流执行${status} · 任务 ${run.task_run_id}`;
  }

  function mergePreparedRunSummary(
    taskRunId: string,
    runUiSummary: PreparedWorkbenchTaskRun["run_ui_summary"] | null | undefined,
  ) {
    if (!runUiSummary) return;
    setPreparedRun((current) =>
      current?.task_run_id === taskRunId
        ? { ...current, run_ui_summary: runUiSummary }
        : current,
    );
    setTaskRuns((current) =>
      current.map((item) =>
        item.task_run_id === taskRunId
          ? { ...item, run_ui_summary: runUiSummary }
          : item,
      ),
    );
  }

  function submittedTaskRunSummary(
    run: PreparedWorkbenchTaskRun,
  ): PreparedWorkbenchTaskRun["run_ui_summary"] {
    const currentSummary = run.run_ui_summary;
    const nodes = (currentSummary?.nodes ?? []).map((node, index) => {
      if (
        index === 0 &&
        !["completed", "ok", "success"].includes(
          String(node.status ?? "").toLowerCase(),
        )
      ) {
        return { ...node, status: "running", status_label: "运行中" };
      }
      return node;
    });
    return {
      ...currentSummary,
      status: "running",
      status_label: "运行中",
      workflow: currentSummary?.workflow ?? {
        id: run.workflow_id,
        name: run.workflow_id,
      },
      current_node: currentSummary?.current_node ?? nodes[0],
      nodes,
      debug_default_collapsed: currentSummary?.debug_default_collapsed ?? true,
      debug_sections: currentSummary?.debug_sections ?? [
        "运行已提交，正在刷新后台事件。",
      ],
    };
  }

  function markTaskRunSubmitted(run: PreparedWorkbenchTaskRun) {
    const summary = submittedTaskRunSummary(run);
    setTaskRunEvents([]);
    mergePreparedRunSummary(run.task_run_id, summary);
    setMessage(`工作流已提交后台运行 · 任务 ${run.task_run_id}`);
    startTaskRunPollingRef.current(run.task_run_id);
  }

  function mergeTaskRunEvents(items: WorkbenchTaskRunEvent[]) {
    if (items.length === 0) return;
    setTaskRunEvents((current) => {
      const seen = new Set(current.map((item) => item.event_id));
      return [
        ...current,
        ...items.filter((item) => !seen.has(item.event_id)),
      ].slice(-300);
    });
  }

  function startTaskRunEventStream(taskRunId: string, afterEventId = 0): boolean {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return false;
    }
    taskRunEventSourceRef.current?.close();
    const query = new URLSearchParams({
      after_id: String(afterEventId),
      poll_ms: "250",
    });
    const source = new EventSource(
      `${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/events/stream?${query.toString()}`,
      { withCredentials: true },
    );
    taskRunEventSourceRef.current = source;
    source.addEventListener("task_run_event", (message) => {
      try {
        const event = JSON.parse((message as MessageEvent).data) as WorkbenchTaskRunEvent;
        mergeTaskRunEvents([event]);
      } catch {
        // Polling remains the fallback when a browser or proxy mangles the stream.
      }
    });
    source.addEventListener("task_run_done", (message) => {
      try {
        const payload = JSON.parse((message as MessageEvent).data) as {
          last_event_id?: number;
        };
        void refreshTaskRunRuntime(taskRunId, Number(payload.last_event_id ?? 0));
      } catch {
        void refreshTaskRunRuntime(taskRunId);
      } finally {
        source.close();
        if (taskRunEventSourceRef.current === source) {
          taskRunEventSourceRef.current = null;
        }
      }
    });
    source.onerror = () => {
      source.close();
      if (taskRunEventSourceRef.current === source) {
        taskRunEventSourceRef.current = null;
      }
    };
    return true;
  }

  async function refreshTaskRunRuntime(
    taskRunId: string,
    afterEventId = 0,
  ): Promise<{ run: PreparedWorkbenchTaskRun; lastEventId: number }> {
    const [events, run] = await Promise.all([
      api.workbench.taskRuns.events(taskRunId, {
        after_id: afterEventId,
        limit: 200,
      }),
      api.workbench.taskRuns.get(taskRunId),
    ]);
    mergeTaskRunEvents(events.items);
    setPreparedRun(run);
    setTaskRuns((current) =>
      [
        run,
        ...current.filter((item) => item.task_run_id !== run.task_run_id),
      ].slice(0, 10),
    );
    mergePreparedRunSummary(taskRunId, run.run_ui_summary);
    return { run, lastEventId: events.last_event_id };
  }

  async function pollTaskRunUntilSettled(taskRunId: string) {
    let cursor = 0;
    let lastRun: PreparedWorkbenchTaskRun | null = null;
    while (taskRunPollingIdRef.current === taskRunId) {
      const result = await refreshTaskRunRuntime(taskRunId, cursor);
      cursor = result.lastEventId;
      lastRun = result.run;
      if (!isTaskRunActiveStatus(taskRunRuntimeStatus(result.run))) {
        break;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
    if (lastRun && !isTaskRunActiveStatus(taskRunRuntimeStatus(lastRun))) {
      await restoreTaskRun(taskRunId);
    }
  }

  function startTaskRunPolling(taskRunId: string) {
    taskRunPollingIdRef.current = taskRunId;
    void (async () => {
      startTaskRunEventStream(taskRunId);
      try {
        await pollTaskRunUntilSettled(taskRunId);
        const run = await api.workbench.taskRuns.get(taskRunId);
        setPreparedRun(run);
        setMessage(taskRunSettledMessage(run));
        setTaskRerunPlanValidation(
          await api.workbench.taskRuns.rerunPlanValidation(taskRunId),
        );
        await loadWorkflows();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "刷新任务状态失败");
      } finally {
        if (taskRunPollingIdRef.current === taskRunId) {
          taskRunPollingIdRef.current = null;
        }
        taskRunEventSourceRef.current?.close();
        taskRunEventSourceRef.current = null;
      }
    })();
  }
  startTaskRunPollingRef.current = startTaskRunPolling;

  async function restoreTaskRun(
    taskRunId: string,
    availableWorkspaces: Workspace[] = workspaces,
  ) {
    const [run, manifest, events] = await Promise.all([
      api.workbench.taskRuns.get(taskRunId),
      api.workbench.taskRuns.artifacts(taskRunId),
      api.workbench.taskRuns.events(taskRunId),
    ]);
    selectedWorkflowIdRef.current = run.workflow_id;
    setSelectedWorkflowId(run.workflow_id);
    setWorkspaceId(run.workspace_id);
    const restoredWorkspace = availableWorkspaces.find(
      (workspace) => workspace.id === run.workspace_id,
    );
    const restoredRepoPath = restoredWorkspace?.repo_path ?? "";
    setRepoPath(restoredRepoPath);
    setInputsJson(
      pretty({
        ...(run.input_snapshot ?? {}),
        ...(restoredRepoPath ? { repo_path: restoredRepoPath } : {}),
      }),
    );
    setProviderOverride(run.agent_runs.find((item) => item.provider)?.provider ?? "");
    setPreparedRun(run);
    setArtifactManifest(manifest);
    setTaskRunEvents(events.items);
    setTaskRuns((current) =>
      [
        run,
        ...current.filter((item) => item.task_run_id !== run.task_run_id),
      ].slice(0, 10),
    );
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

    const artifactPaths = new Set(
      manifest.artifacts.map((item) => item.relative_path),
    );
    if (artifactPaths.has("workflow_execution.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_execution.json",
      );
      const parsed = JSON.parse(
        content.content || "{}",
      ) as WorkflowExecutionResult;
      setWorkflowExecution(parsed);
      setTaskRerunPlan(
        (parsed.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
    }
    if (artifactPaths.has("workflow_output_materialization.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_output_materialization.json",
      );
      const parsed = JSON.parse(
        content.content || "{}",
      ) as MaterializeWorkflowOutputsResult;
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
      const parsed = JSON.parse(
        content.content || "{}",
      ) as WorkbenchAcceptanceAudit;
      setTaskAcceptanceAudit(parsed);
    }
  }

  function hydrateBuilderFromWorkflow(workflow: {
    id?: unknown;
    name?: unknown;
    inputs?: unknown;
    outputs?: unknown;
    steps?: unknown;
    ui?: unknown;
  }) {
    const workflowId = String(workflow.id ?? "").trim();
    const workflowName = String(workflow.name ?? "").trim();
    if (workflowId) setBuilderWorkflowId(workflowId);
    if (workflowName) setBuilderWorkflowName(workflowName);

    const inputs = Array.isArray(workflow.inputs)
      ? workflow.inputs.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const inputLabels: Record<string, string> = {};
    setBuilderInputSpec(
      inputs
        .map((input) => {
          const id = String(input.id ?? "").trim();
          if (!id) return "";
          const type = String(input.type ?? "free_text").trim() || "free_text";
          const resolver = String(input.resolver ?? "").trim();
          const label = String(input.label ?? "").trim();
          if (label && label !== id) inputLabels[id] = label;
          return workflowSpecToText({ id, type, resolver });
        })
        .filter(Boolean)
        .join(", "),
    );
    setBuilderInputLabels(inputLabels);

    const outputs = Array.isArray(workflow.outputs)
      ? workflow.outputs.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const outputLabels: Record<string, string> = {};
    const outputArtifacts: string[] = [];
    setBuilderOutputSpec(
      outputs
        .map((output) => {
          const id = String(output.id ?? "").trim();
          if (!id) return "";
          const type = String(output.type ?? "json").trim() || "json";
          const artifact = String(output.artifact ?? "").trim();
          const label = String(output.label ?? "").trim();
          if (label && label !== id) outputLabels[id] = label;
          if (artifact) outputArtifacts.push(artifact);
          return workflowSpecToText({ id, type, artifact });
        })
        .filter(Boolean)
        .join(", "),
    );
    setBuilderOutputLabels(outputLabels);

    const steps = Array.isArray(workflow.steps)
      ? workflow.steps.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const agentStep = steps.find(
      (step) => String(step.type ?? "") === "agent_task",
    );
    if (agentStep) {
      const provider = String(agentStep.provider ?? "").trim();
      const mcpProfile = String(agentStep.mcp_profile ?? "").trim();
      const goal = String(agentStep.goal ?? "").trim();
      const skills = Array.isArray(agentStep.skills)
        ? agentStep.skills.map((item) => String(item)).filter(Boolean)
        : [];
      const requiredArtifacts = Array.isArray(agentStep.required_artifacts)
        ? agentStep.required_artifacts.map((item) => String(item)).filter(Boolean)
        : [];
      if (provider) setBuilderProvider(provider);
      if (mcpProfile) setBuilderMcpProfile(mcpProfile);
      if (goal) setBuilderGoal(goal);
      if (skills.length > 0) setBuilderSkillIds(skills);
      setBuilderArtifacts(
        uniqueWorkflowStrings([...requiredArtifacts, ...outputArtifacts]).join(", "),
      );
    } else {
      setBuilderArtifacts(uniqueWorkflowStrings(outputArtifacts).join(", "));
    }

    const layout = workflowLayoutFromPayload(workflow);
    if (layout) {
      const defaultEdgeIds = new Set(
        defaultWorkflowCanvasEdges.map((edge) => edge.id),
      );
      const defaultEdgePairs = new Set(
        defaultWorkflowCanvasEdges.map((edge) => `${edge.source}->${edge.target}`),
      );
      const positions: Record<string, WorkflowNodePosition> = {};
      const titles: Record<string, string> = {};
      const configs: Record<string, Record<string, unknown>> = {};
      const extraNodes: WorkflowCanvasNode[] = [];
      for (const node of layout.nodes) {
        positions[node.id] = clampWorkflowNodePosition({ x: node.x, y: node.y });
        titles[node.id] = node.title;
        if (node.config) configs[node.id] = node.config;
        if (node.source === "canvas") {
          extraNodes.push({
            id: node.id,
            kind: node.kind,
            title: node.title,
            subtitle: node.subtitle || "画布恢复节点",
            body:
              node.kind === "input" ||
              node.kind === "agent" ||
              node.kind === "output"
                ? ["画布恢复节点", "已同步草稿契约"]
                : ["画布恢复节点", "连接 Agent 后进入执行契约"],
            x: positions[node.id].x,
            y: positions[node.id].y,
            source: "canvas",
            ...(node.config ? { config: node.config } : {}),
          });
        }
      }
      setWorkflowNodePositions(positions);
      setWorkflowNodeTitles(titles);
      setWorkflowNodeConfigs(configs);
      setWorkflowExtraNodes(extraNodes);
      setWorkflowHiddenNodeIds(layout.hidden_node_ids);
      setWorkflowHiddenEdgeIds(layout.hidden_edge_ids ?? []);
      setWorkflowCanvasEdges(
        (layout.edges ?? []).filter(
          (edge) =>
            !defaultEdgeIds.has(edge.id) &&
            !defaultEdgePairs.has(`${edge.source}->${edge.target}`),
        ),
      );
    } else {
      setWorkflowNodePositions({});
      setWorkflowNodeTitles({});
      setWorkflowNodeConfigs({});
      setWorkflowExtraNodes([]);
      setWorkflowHiddenNodeIds([]);
      setWorkflowHiddenEdgeIds([]);
      setWorkflowCanvasEdges([]);
    }
  }

  function updateWorkflowJsonDraft(value: string) {
    setWorkflowJson(value);
    try {
      const payload = parseJsonObject(value) as {
        id?: unknown;
        name?: unknown;
        inputs?: unknown;
        outputs?: unknown;
        steps?: unknown;
        ui?: unknown;
      };
      hydrateBuilderFromWorkflow(payload);
    } catch {
      // Keep the raw draft while the user is still typing incomplete JSON.
    }
  }

  function applyBuilderScenario(
    scenarioId: keyof typeof WORKFLOW_BUILDER_SCENARIOS,
  ) {
    const scenario = WORKFLOW_BUILDER_SCENARIOS[scenarioId];
    setBuilderScenario(scenarioId);
    setBuilderWorkflowName(`自定义 ${scenario.name}`);
    setBuilderInputSpec(scenario.inputs);
    setBuilderOutputSpec(scenario.outputs);
    setBuilderGoal(scenario.goal);
    setBuilderArtifacts(scenario.artifacts);
    setBuilderSkillIds(
      "skills" in scenario
        ? [...scenario.skills]
        : DEFAULT_BUILDER_SKILL_IDS,
    );
    setBuilderInputSchemas(pretty(DEFAULT_BUILDER_INPUT_SCHEMAS));
    setBuilderOutputSchemas(pretty(DEFAULT_BUILDER_OUTPUT_SCHEMAS));
    setBuilderEvidenceMappings(pretty(DEFAULT_BUILDER_EVIDENCE_MAPPINGS));
    setBuilderSemanticImports(pretty(DEFAULT_BUILDER_SEMANTIC_IMPORTS));
    setBuilderInputLabels({});
    setBuilderOutputLabels({});
    setWorkflowNodePositions({});
    setWorkflowExtraNodes([]);
    setWorkflowHiddenNodeIds([]);
    setWorkflowNodeTitles({});
    setWorkflowNodeConfigs({});
    setWorkflowCanvasEdges([]);
    setActiveWorkflowNodeId("agent-task");
  }

  function generateWorkflowFromBuilder(
    overrides: {
      inputSpec?: string;
      outputSpec?: string;
      artifacts?: string;
      inputLabels?: Record<string, string>;
      outputLabels?: Record<string, string>;
    } = {},
  ) {
    const workflowId = builderWorkflowId.trim();
    const workflowName = builderWorkflowName.trim();
    if (!workflowId || !workflowName) {
      throw new Error("Workflow builder requires workflow id and name");
    }
    const inputSchemas = parseJsonObject(builderInputSchemas || "{}");
    const inputSpec = overrides.inputSpec ?? builderInputSpec;
    const outputSpec = overrides.outputSpec ?? builderOutputSpec;
    const artifactsSpec = overrides.artifacts ?? builderArtifacts;
    const inputLabels = overrides.inputLabels ?? builderInputLabels;
    const outputLabels = overrides.outputLabels ?? builderOutputLabels;
    const selectedSkills = selectedBuilderSkillOptions.map((skill) => ({
      id: skill.id,
      label: skill.label,
      source: skill.source,
      prompt_hint: skill.prompt_hint || skill.description || skill.label,
    }));
    const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
    const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
    const semanticImports = parseJsonObject(builderSemanticImports || "{}");
    const workflow = buildWorkflowFromDesigner({
      workflowId,
      workflowName,
      provider: builderProvider.trim() || "claude-code",
      mcpProfile: builderMcpProfile.trim(),
      goal: builderGoal.trim(),
      skillIds: builderSkillIds,
      selectedSkills,
      inputSpec,
      outputSpec,
      artifacts: artifactsSpec,
      inputLabels,
      outputLabels,
      inputSchemas,
      outputSchemas,
      evidenceMappings,
      semanticImports,
      layout: workflowLayoutSnapshot(
        workflowCanvasNodes,
        workflowHiddenNodeIds,
        visibleWorkflowCanvasEdges,
        workflowHiddenEdgeIds,
      ),
    });
    setWorkflowJson(pretty(workflow));
    setMessage(`工作流草稿已生成: ${workflow.id}`);
    return workflow;
  }

  const generateWorkflowDraft = () =>
    runAction("generate-workflow", async () => {
      generateWorkflowFromBuilder();
    });

  const saveWorkflow = () =>
    runAction("save-workflow", async () => {
      const currentDraft = parseJsonObject(workflowJson || "{}");
      const payload = workflowHasSpecializedStep(currentDraft)
        ? mergeDesignerWorkflowWithSpecializedDraft(
            generateWorkflowFromBuilder(),
            currentDraft,
          )
        : mergeDesignerWorkflowWithDraft(
            generateWorkflowFromBuilder(),
            currentDraft,
          );
      let saved: WorkflowDefinition;
      let autoClonedFromBuiltin = false;
      try {
        saved = await api.workbench.workflows.create(payload);
      } catch (err: unknown) {
        const suggestedId = suggestedWorkflowIdFromError(err);
        if (!suggestedId) throw err;
        const payloadRecord = payload as Record<string, unknown>;
        const sourceName =
          typeof payloadRecord.name === "string" && payloadRecord.name.trim()
            ? payloadRecord.name.trim()
            : String(payloadRecord.id || "工作流");
        const clonedPayload = {
          ...payload,
          id: suggestedId,
          name: `${sourceName} 自定义`,
          version: Number(payloadRecord.version ?? 1) + 1,
        };
        saved = await api.workbench.workflows.create(clonedPayload);
        setWorkflowJson(pretty(saved));
        autoClonedFromBuiltin = true;
      }
      selectedWorkflowIdRef.current = saved.id;
      setSelectedWorkflowId(saved.id);
      setSelectedPresetId(`saved:${saved.id}`);
      hydrateBuilderFromWorkflow(saved as unknown as Record<string, unknown>);
      const warningCount = saved.audit?.warnings?.length ?? 0;
      localWorkflowDraftIdRef.current = "";
      await loadWorkflows(saved.id);
      setMessage(
        warningCount
          ? `${autoClonedFromBuiltin ? "内置模板已另存为自定义工作流" : "工作流已保存"}: ${saved.id} (${warningCount} audit warning(s))`
          : `${autoClonedFromBuiltin ? "内置模板已另存为自定义工作流" : "工作流已保存"}: ${saved.id}`,
      );
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
    selectedWorkflowIdRef.current = clone.id;
    setSelectedWorkflowId(clone.id);
    localWorkflowDraftIdRef.current = clone.id;
    applyWorkflowLayout(clone);
    setMessage(`已复制为草稿: ${clone.id}`);
  };

  const createBlankWorkflowDraft = () => {
    const nextId = `custom_workflow_${Date.now().toString(36)}`;
    const nextName = "空白工作流";
    const blankWorkflow = {
      id: nextId,
      name: nextName,
      version: 1,
      inputs: [],
      steps: [
        {
          id: "agent_task",
          type: "agent_task",
          provider: "claude-code",
          mcp_profile: "",
          skills: [],
          skill_instructions: [],
          goal: "",
          required_artifacts: [],
        },
      ],
      outputs: [],
      ui: {
        layout: {
          nodes: [],
          edges: [],
          hidden_edge_ids: [],
          hidden_node_ids: [],
        },
      },
    };
    flushSync(() => {
      localWorkflowDraftIdRef.current = nextId;
      setSelectedPresetId("");
      selectedWorkflowIdRef.current = nextId;
      setSelectedWorkflowId(nextId);
      setBuilderScenario("mr_blackbox_test");
      setBuilderWorkflowId(nextId);
      setBuilderWorkflowName(nextName);
      setBuilderInputSpec("");
      setBuilderInputLabels({});
      setBuilderOutputSpec("");
      setBuilderOutputLabels({});
      setBuilderArtifacts("");
      setBuilderProvider("claude-code");
      setBuilderMcpProfile("");
      setBuilderSkillIds([]);
      setBuilderSkillQuery("");
      setBuilderGoal("");
      setBuilderInputSchemas("{}");
      setBuilderOutputSchemas("{}");
      setBuilderEvidenceMappings("{}");
      setBuilderSemanticImports("{}");
      setNewWorkflowInputName("");
      setNewWorkflowInputId("");
      setNewWorkflowInputType("free_text");
      setNewWorkflowInputResolver("manual");
      setNewWorkflowOutputName("");
      setNewWorkflowOutputId("");
      setNewWorkflowOutputType("json");
      setNewWorkflowOutputArtifact("");
      setWorkflowExtraNodes([]);
      setWorkflowCanvasEdges([]);
      setWorkflowHiddenNodeIds([]);
      setWorkflowHiddenEdgeIds([]);
      setWorkflowNodePositions({});
      setWorkflowNodeTitles({});
      setWorkflowNodeConfigs({});
      setActiveWorkflowNodeId("agent-task");
      setWorkflowJson(pretty(blankWorkflow));
    });
    setMessage("已创建空白工作流草稿");
  };

  const applyPreset = () => {
    const savedWorkflowId = selectedPresetId.startsWith("saved:")
      ? selectedPresetId.slice("saved:".length)
      : "";
    const preset = workflowPresets.find((item) => item.id === selectedPresetId);
    const selectedDefinition = savedWorkflowId
      ? workflows.find((item) => item.id === savedWorkflowId) ?? null
      : preset?.definition ?? null;
    if (!selectedDefinition) return;
    localWorkflowDraftIdRef.current = "";
    setWorkflowJson(pretty(selectedDefinition));
    selectedWorkflowIdRef.current = selectedDefinition.id;
    setSelectedWorkflowId(selectedDefinition.id);
    hydrateBuilderFromWorkflow(selectedDefinition as unknown as Record<string, unknown>);
    applyWorkflowLayout(selectedDefinition);
    setMessage(
      savedWorkflowId
        ? `已载入自定义工作流: ${workflowDisplayName(selectedDefinition)}`
        : `已从模板库导入到当前草稿: ${workflowDisplayName(selectedDefinition)}`,
    );
  };

  const restoreBuiltinPresets = () =>
    runAction("restore-builtin-presets", async () => {
      const result = await api.workbench.workflows.restoreBuiltins();
      setWorkflows(result.items);
      const preferred =
        result.items.find((item) => item.id === selectedWorkflowId) ??
        result.items.find((item) => item.id === "module_analysis") ??
        result.items[0];
      if (preferred) {
        selectedWorkflowIdRef.current = preferred.id;
        setSelectedWorkflowId(preferred.id);
        setWorkflowJson(pretty(preferred));
        applyWorkflowLayout(preferred);
      }
      setMessage(`已恢复内置工作流: ${result.restored_count} 个预设`);
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
        provider_override: selectedAgentStep
          ? providerOverride.trim() || null
          : null,
      });
      setPreparedRun(result);
      setTaskRuns((current) =>
        [
          result,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRunEvents([]);
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
      setMessage(`任务已准备 · ${result.task_run_id}`);
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
          provider_override: selectedAgentStep
            ? providerOverride.trim() || null
            : null,
        },
        undefined,
        true,
      );
      setPreparedRun(result.task_run);
      setTaskRunEvents([]);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setWorkflowExecution(result.execution ?? null);
      mergePreparedRunSummary(
        result.task_run_id,
        result.run_ui_summary ?? result.execution?.run_ui_summary,
      );
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      setTaskRerunPlan(
        (result.execution?.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
      setTaskRerunPlanValidation(null);
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setArtifactContent(null);
      await refreshTaskRunRuntime(result.task_run_id);
      setMessage(workflowRunResultMessage("任务运行", result));
      startTaskRunPolling(result.task_run_id);
    });

  const restoreExistingTaskRun = (taskRunId: string) =>
    runAction(`restore-task-run-${taskRunId}`, async () => {
      await restoreTaskRun(taskRunId);
      setMessage(`任务已恢复 · ${taskRunId}`);
    });

  const cancelPreparedTaskRun = () =>
    runAction("cancel-task-run", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.cancel(
        preparedRun.task_run_id,
      );
      mergePreparedRunSummary(preparedRun.task_run_id, result.run_ui_summary);
      await refreshTaskRunRuntime(preparedRun.task_run_id);
      setMessage(
        result.cancelled
          ? "已取消本次工作流运行"
          : `当前状态不可取消：${runStatusDisplayLabel(result.status)}`,
      );
    });

  const runProviderStartupProbe = (provider: string) =>
    runAction(`provider-probe-${provider}`, async () => {
      const result = await api.tools.startupProbe(
        provider,
        repoPath.trim() || undefined,
      );
      setProviderProbeResults((current) => ({
        ...current,
        [provider]: result,
      }));
      setMessage(`启动探测 ${result.status}: ${provider}`);
    });

  const runProviderTaskProbe = (provider: string) =>
    runAction(`provider-task-probe-${provider}`, async () => {
      const result = await api.workbench.providerTaskProbe(
        provider,
        repoPath.trim() || undefined,
        30,
      );
      setProviderTaskProbeResults((current) => ({
        ...current,
        [provider]: result,
      }));
      setPreparedRun(result.task_run);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
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
        (provider) =>
          provider.agent_owned && provider.diagnostics?.startup_probe_endpoint,
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
      setMessage(
        `任务探测 deployment ${result.status}: ${ready}/${total} ready`,
      );
    });

  const runSmokeE2E = () =>
    runAction("smoke-e2e", async () => {
      const result = await api.workbench.smokeE2E(
        repoPath.trim() || undefined,
        30,
      );
      setSmokeE2EResult(result);
      setPreparedRun(result.task_run);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
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
        Array.from(files).map((file) =>
          api.workbench.uploadInputFile(file, inputId),
        ),
      );
      const paths = uploads.map((item) => item.path).filter(Boolean);
      if (inputType === "file_set") {
        setInputsJson((current) => {
          const existing = inputTextValue(
            parseJsonObject(current || "{}"),
            input,
          )
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean);
          return updateInputsJsonValue(
            current,
            input,
            [...existing, ...paths].join("\n"),
          );
        });
      } else if (paths[0]) {
        updatePrepareInput(input, paths[0]);
      }
      setMessage(
        `Input file uploaded: ${uploads.map((item) => item.filename).join(", ")}`,
      );
    });

  const buildPreparedConversationInitialContext = () => {
    if (!preparedRun) return {};
    const stepResults = workflowExecution?.step_results ?? [];
    const auditSummary = workflowExecution?.audit_summary ?? {};
    const completedSteps =
      auditSummary.completed_steps ??
      stepResults.filter((step) =>
        ["ok", "success", "completed", "ready", "passed"].includes(
          String(step.status ?? "").toLowerCase(),
        ),
      ).length;
    const failedSteps =
      (auditSummary.error_steps ?? 0) +
        (auditSummary.invalid_steps ?? 0) ||
      stepResults.filter((step) =>
        ["error", "failed", "failure", "invalid", "timeout"].includes(
          String(step.status ?? "").toLowerCase(),
        ),
      ).length;
    const deliverables = (workflowExecution?.outputs ?? []).map((output) => ({
      id: String(output.id ?? output.name ?? output.artifact ?? ""),
      status: String(output.status ?? ""),
      artifact: String(output.artifact ?? output.path ?? ""),
    }));
    const artifacts = artifactManifest?.artifacts ?? [];
    const artifactSummary = {
      artifact_count: artifacts.length,
      user_deliverable_count: artifacts.filter(
        (artifact) => artifactAudience(artifact) === "deliverable",
      ).length,
      diagnostic_count: artifacts.filter(
        (artifact) => artifactAudience(artifact) === "diagnostic",
      ).length,
    };
    const quality = workflowExecution?.test_activity_quality;
    const failureRecovery = stepResults
      .map((step) => step.failure_recovery)
      .filter(Boolean)
      .map((recovery) => ({
        failure_kind: recovery?.failure_kind,
        retryable: recovery?.retryable,
        user_message: recovery?.user_message,
        recommended_actions: recovery?.recommended_actions ?? [],
        suggested_actions: recovery?.suggested_actions ?? [],
        missing_artifacts: recovery?.missing_artifacts ?? [],
      }));

    return {
      workflow_id: preparedRun.workflow_id,
      task_run_id: preparedRun.task_run_id,
      workspace_id: preparedRun.workspace_id,
      memory_namespace: `workspace:${preparedRun.workspace_id}`,
      repo_path: preparedRun.repo_path,
      artifact_dir: preparedRun.artifact_dir,
      agent_runs_count: preparedRun.agent_runs.length,
      agent_runs: preparedRun.agent_runs.map((agentRun) => ({
        step_id: agentRun.step_id,
        run_id: agentRun.run_id,
        artifact_dir: agentRun.artifact_dir,
      })),
      workflow_execution_summary: workflowExecution
        ? {
            status: workflowExecution.status,
            started_at: workflowExecution.started_at,
            completed_at: workflowExecution.completed_at,
            completed_steps: completedSteps,
            failed_steps: failedSteps,
            output_count: workflowExecution.outputs?.length ?? 0,
            failure_kinds: auditSummary.failure_kinds ?? [],
          }
        : undefined,
      deliverables,
      artifact_manifest_summary: artifactSummary,
      test_activity_quality: quality
        ? {
            status: quality.status,
            deliverable: quality.deliverable,
            score: quality.score,
            issue_count: quality.issue_count,
            recommendations: quality.recommendations ?? [],
          }
        : undefined,
      failure_recovery: failureRecovery,
    };
  };

  const openPreparedConversation = async () => {
    if (!preparedRun || taskRunActionBusy) return;
    setOpeningConversation(true);
    try {
      const conversation = await api.aiConversations.createForScope({
        scope_type: "workbench_task_run",
        scope_id: preparedRun.task_run_id,
        workspace_id: preparedRun.workspace_id,
        memory_namespace: `workspace:${preparedRun.workspace_id}`,
        title: `${workflowDisplayName(preparedRun.workflow_id)} · AI 复盘`,
        initial_context: buildPreparedConversationInitialContext(),
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
      const history = await api.workbench.taskRuns.rerunHistory(
        preparedRun.task_run_id,
      );
      setTaskRerunPlan(result);
      setTaskRerunPlanValidation(validation);
      setTaskRerunHistory(history);
      setMessage(`复跑计划 ${runStatusDisplayLabel(result.status)}: ${result.task_run_id}`);
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
        `验收审计${runStatusDisplayLabel(result.status)} · 缺少 ${result.summary.missing_required} 个必需项`,
      );
    });

  const executeTaskRerunPlan = () =>
    runAction("execute-rerun-plan", async () => {
      if (!preparedRun || !taskRerunPlanValidation?.can_rerun) return;
      const result = await api.workbench.taskRuns.executeRerunPlan(
        preparedRun.task_run_id,
        undefined,
        true,
      );
      setTaskRerunExecution(result);
      if (result.execution) {
        setWorkflowExecution({
          ...result.execution,
          evidence_materialization:
            result.evidence_materialization ??
            result.execution.evidence_materialization,
          semantic_output_import:
            result.semantic_output_import ??
            result.execution.semantic_output_import,
          acceptance_audit:
            result.acceptance_audit ?? result.execution.acceptance_audit,
          run_ui_summary:
            result.run_ui_summary ?? result.execution.run_ui_summary,
        });
        mergePreparedRunSummary(
          preparedRun.task_run_id,
          result.run_ui_summary ?? result.execution.run_ui_summary,
        );
        setTaskRerunPlan(
          (result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null,
        );
      }
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlanValidation(result.validation_after ?? null);
      setTaskRerunHistory(
        await api.workbench.taskRuns.rerunHistory(preparedRun.task_run_id),
      );
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        workflowRunResultMessage("复跑执行", {
          status: result.execution?.status ?? result.status,
          task_run_id: preparedRun.task_run_id,
          evidence_materialization: result.evidence_materialization,
          semantic_output_import: result.semantic_output_import,
          acceptance_audit: result.acceptance_audit,
        }),
      );
    });

  const previewArtifact = (relativePath: string) =>
    runAction(`preview-artifact-${relativePath}`, async () => {
      if (!preparedRun || taskRunActionBusy) return;
      const result = await api.workbench.taskRuns.artifactContent(
        preparedRun.task_run_id,
        relativePath,
      );
      setArtifactContent(result);
      setMessage(`产物预览已加载: ${relativePath}`);
    });

  const executePreparedWorkflow = () =>
    runAction("execute-workflow", async () => {
      if (!preparedRun) return;
      const taskRun = preparedRun;
      markTaskRunSubmitted(taskRun);
      const result = await api.workbench.taskRuns.execute(
        taskRun.task_run_id,
        undefined,
        true,
      );
      setWorkflowExecution(result.execution ?? null);
      mergePreparedRunSummary(taskRun.task_run_id, result.run_ui_summary);
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlan(
        (result.execution?.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
      setTaskRerunPlanValidation(null);
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      setMessage(workflowRunResultMessage("工作流执行", result));
      startTaskRunPolling(taskRun.task_run_id);
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
        `输出已固化 · 证据 ${result.evidence_count} 条 · 语义导入 ${runStatusDisplayLabel(result.semantic_output_import?.status ?? "skipped")}`,
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
        `语义输出已导入: ${result.imported_count}，被拒绝: ${result.rejected_count}`,
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

  const validatePreparedAgentRun = (
    stepId: string,
    requiredArtifacts: string[],
  ) =>
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

  const materializePreparedAgentRun = (
    stepId: string,
    requiredArtifacts: string[],
  ) =>
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
          `语义用例已导入: ${result.imported_count}，被拒绝: ${result.rejected_count}`,
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
      const result = await api.workbench.semanticCases.importFile(
        semanticFile,
        {
          feature: semanticFeature,
          module: semanticModule,
          test_level: "black_box",
        },
      );
      setMessage(
        `语义文件已导入: ${result.imported_count}，被拒绝: ${result.rejected_count}`,
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
      setMemorySlices((current) => ({
        ...current,
        [evidenceId]: result.items,
      }));
      setMessage(`源码切片已加载: ${result.items.length}`);
    });

  const taskRunActionBusy = Boolean(busyAction);
  const agentRunActionBusy = Boolean(
    busyAction?.startsWith("execute-") ||
    busyAction?.startsWith("validate-") ||
      busyAction?.startsWith("materialize-"),
  );
  const pageTitle =
    activeWorkbenchView === "workflow"
      ? "工作流设计"
      : activeWorkbenchView === "knowledge"
        ? "语义库"
        : "运行驾驶舱";
  const pageDescription =
    activeWorkbenchView === "workflow"
      ? "创建、编辑并保存工作流模板；保存后的模板才会出现在运行驾驶舱。"
      : activeWorkbenchView === "knowledge"
        ? "搜索、导入和复用测试知识、历史案例与证据片段。"
        : "选择已建工作区和已保存工作流，填写本次输入，启动运行并下载交付文件。";

  return {
    AlertTriangle, ArtifactPreviewCard, ClipboardList, Copy, DEFAULT_BUILDER_SKILL_IDS, Database, Download, FilePlus2,
    Library, Loader2, MessageSquareText, Panel, PlayCircle, ProviderFactRow, ProviderSectionTitle, RefreshCw,
    RotateCcw, Save, Search, Trash2, WORKFLOW_BUILDER_SCENARIOS, WORKFLOW_CANVAS_HEIGHT, WORKFLOW_CANVAS_WIDTH, WORKFLOW_MODULE_PALETTE,
    WORKFLOW_NODE_ACCENT, WORKFLOW_NODE_TONE, WORKFLOW_NODE_WIDTH, WorkbenchStageFrame, X, acceptanceCodetalkProviderIssues, acceptanceInputRedactionIssues, acceptanceInstructionPolicyIssues,
    acceptanceIssueLabel, acceptanceProviderIssues, acceptanceWorkflowOutputIssues, activeWorkbenchView, activeWorkflowNode, activeWorkflowNodeId, addBuilderInputContract, addBuilderOutputContract,
    addPaletteNodeToCanvas, addPaletteNodeToCanvasViewportCenter, agentMcpRequestSummary, agentRunActionBusy, applyBuilderScenario, applyPreset, applyWorkspaceSelection, artifactAudience,
    artifactAudienceGroups, artifactAudienceLabel, artifactContent, artifactManifest, artifactShortName, auditWorkflowDraft, blackBoxGenerationPolicySummary, buildSemanticCasesFromText,
    builderArtifacts, builderEvidenceMappings, builderGoal, builderInputItems, builderInputLabels, builderInputSchemas, builderInputSpec, builderMcpCompatibility,
    builderMcpOptions, builderMcpProfile, builderOutputItems, builderOutputLabels, builderOutputPreview, builderOutputSchemas, builderOutputSpec, builderProvider,
    builderProviderOptions, builderScenario, builderSemanticImports, builderSkillIds, builderSkillQuery, builderWorkflowId, builderWorkflowName, busyAction,
    cancelPreparedTaskRun, commandResolutionLines, compactReasonLabel, connectWorkflowTargetFromPending, copyActiveWorkflowNode, createAndRunTaskRun, createBlankWorkflowDraft, currentApiBase,
    deleteActiveWorkflowNode, deleteWorkflowEdge, deploymentProbeResult, downloadTextFile, duplicateSelectedWorkflowDraft, endWorkflowBoardPan, endWorkflowNodeDrag, error,
    evidenceAuditRefs, evidenceValidationSummary, executePreparedAgentRun, executePreparedWorkflow, executeTaskRerunPlan, executionInputSummary, executionResults, failureRetryContextSummary,
    fastContextDecisionSummary, filledInputCount, generateTaskAcceptanceAudit, generateWorkflowDraft, groupedWorkflowPresets, importPreparedSemanticOutputs, importSemanticCase, importSemanticCaseFile,
    inputContextSummary, inputMaterialsSummary, inputTextValue, inputsJson, isFileLikeWorkflowInput, isPatchLikeWorkflowInput, isTaskRunActiveStatus, loadMemorySlices,
    loadPreparedArtifacts, loadTaskRerunPlan, loadWorkflows, loading, manualEvidencePath, manualEvidenceSubject, manualEvidenceText, materializationAuditOutputs,
    materializePreparedAgentRun, materializePreparedWorkflowOutputs, materializeResults, memoryArtifactSummary, memoryQuery, memoryResults, memorySlices, message,
    motionPreferenceReady, moveWorkflowBoardPan, moveWorkflowNode, newWorkflowInputId, newWorkflowInputName, newWorkflowInputResolver, newWorkflowInputType, newWorkflowOutputArtifact,
    newWorkflowOutputId, newWorkflowOutputName, newWorkflowOutputType, openPreparedConversation, openingConversation, pageDescription, pageTitle, paletteDragModuleRef,
    parseCommaSeparated, parsedPrepareInputs, prefersReducedMotion, prepareTaskRun, preparedProviderReadiness, preparedRun, preparedRunSnapshotSummary, pretty,
    previewArtifact, prioritizedAuditArtifacts, providerDisplayLabel, providerMatrix, providerOverride, providerProbeResults, providerReadinessSummary, providerStatusDisplayLabel, runExecutorProviderOptions,
    providerTaskProbeResults, rejectedOutputLabel, rejectedOutputReason, renameActiveWorkflowNode, replayPlanSummary, repoPath, requiredInputCount, resetActiveWorkflowNodePosition,
    restoreBuiltinPresets, restoreExistingTaskRun, runAllAgentProviderStartupProbes, runAllAgentProviderTaskProbes, runPanelCapabilitySummary, runPanelDeliverables, runPanelExecutionNotice, runPanelFailureReasons,
    runPanelProgress, runPanelStatus, runPhaseCards, runProviderStartupProbe, runProviderTaskProbe, runSmokeE2E, runStatusDisplayLabel, safeArtifactDownloadFilename,
    saveManualEvidence, saveWorkflow, savedCustomWorkflows, searchMemory, searchSemanticCases, selectRunWorkflow, selectWorkflowConnectionSource, selectedAgentSkillIds,
    selectedAgentSkillInstructions, selectedAgentStep, selectedPresetId, selectedProviderCapability, selectedRunMcpProfile, selectedRunProvider, selectedWorkflowAudit, selectedWorkflowId,
    selectedWorkflowIdRef, selectedWorkflowInputs, selectedWorkflowOutputs, semanticFeature, semanticFile, semanticImportOutputIds, semanticJson, semanticLines,
    semanticModule, semanticOutputImport, semanticQuery, semanticResults, setActiveWorkbenchView, setActiveWorkflowNodeId, setBuilderArtifacts, setBuilderEvidenceMappings,
    setBuilderGoal, setBuilderInputSchemas, setBuilderInputSpec, setBuilderMcpProfile, setBuilderOutputSchemas, setBuilderOutputSpec, setBuilderProvider, setBuilderSemanticImports,
    setBuilderSkillIds, setBuilderSkillQuery, setBuilderWorkflowId, setBuilderWorkflowName, setInputsJson, setManualEvidencePath, setManualEvidenceSubject, setManualEvidenceText,
    setMemoryQuery, setNewWorkflowInputId, setNewWorkflowInputName, setNewWorkflowInputResolver, setNewWorkflowInputType, setNewWorkflowOutputArtifact, setNewWorkflowOutputId, setNewWorkflowOutputName,
    setNewWorkflowOutputType, setProviderOverride, setSelectedPresetId, setSelectedWorkflowId, setSemanticFeature, setSemanticFile, setSemanticJson, setSemanticLines,
    setSemanticModule, setSemanticQuery, smokeE2EResult, startPalettePointerDrag, startWorkflowBoardPan, startWorkflowConnectionDrag, startWorkflowNodeDrag, systemAudit,
    taskAcceptanceAudit, taskRerunExecution, taskRerunHistory, taskRerunPlan, taskRerunPlanValidation, taskRunActionBusy, taskRunEventDetail, taskRunEventTitle,
    taskRunEventTone, taskRunEvents, taskRunRuntimeStatus, taskRuns, testActivityQuality, uniqueWorkflowStrings, updateActiveWorkflowNodeConfig, updatePrepareInput,
    updateWorkflowJsonDraft, uploadPrepareInputFile, validatePreparedAgentRun, validationResults, visibleBuilderSkillOptions, visibleDeliveryArtifacts, visibleTaskRunEvents, visibleWorkflowCanvasEdges,
    visibleWorkflowInputs, workbenchRootRef, workflowAuditWarningLabel, workflowBoardRef, workflowCanvasInnerRef, workflowCanvasNodes, workflowDisplayName, workflowDraftAuditSummary,
    workflowDraftEdge, workflowDraftServerAudit, workflowEdgePath, workflowEdgePoints, workflowExecution, workflowInputDisplayName, workflowInputsUpdated, workflowItemLabel,
    workflowJson, workflowNodeConfigString, workflowOptions, workflowOutputDisplayName, workflowOutputMaterializationSummary, workflowOutputMaterialize, workflowPendingConnectionSourceId, workflowPresets,
    workflowSkillOptions, workflows, workspaceId, workspaces,
  };
}

export type WorkbenchController = ReturnType<typeof useWorkbenchController>;

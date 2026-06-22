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
  AgentRunExecutionResult,
  ArtifactValidationResult,
  MaterializeEvidenceResult,
  MaterializeWorkflowOutputsResult,
  PreparedWorkbenchTaskRun,
  SemanticCase,
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

const DEFAULT_SEMANTIC_CASE = {
  case_id: "nvme_tcp_tls_handshake_fail",
  feature: "NVMe TCP TLS",
  module: "nvmf_tcp",
  test_level: "black_box",
  scenario: "TLS handshake fails and connection is released",
  terms: ["TLS negotiation", "queue pair", "connection release"],
  tags: ["resource_cleanup", "exception_branch"],
  preconditions: "Target configured with TLS enabled",
  steps: [
    "Create an NVMe TCP connection with invalid TLS credentials",
    "Observe connection setup failure",
  ],
  expected: "The session is rejected and all allocated connection resources are released",
  assertion_style: "Prefer observable status, logs, counters, and connection lifecycle checks",
};

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

type EvidenceValidationSummary = {
  acceptedCount: number;
  rejectedCount: number;
  acceptedDetails: Array<{ artifact: string; sha256: string; sourceStepId: string }>;
  rejectedDetails: Array<{ artifact: string; reason: string; sourceStepId: string }>;
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

const AUDIT_ARTIFACT_KIND_ORDER = [
  "task_bundle",
  "agent_task_bundle",
  "agent_instructions",
  "provider_snapshot",
  "workflow_contract",
  "context_discovery_decision",
  "context_bundle",
  "output_schemas",
  "memory_retrieval",
  "source_read_chain",
  "evidence_consumption_trajectory",
  "degraded_retrieval",
  "evidence_validation",
  "workflow_outputs",
  "workflow_execution",
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
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(DEFAULT_WORKFLOW.id);
  const [workspaceId, setWorkspaceId] = useState("manual-workspace");
  const [repoPath, setRepoPath] = useState("");
  const [providerOverride, setProviderOverride] = useState("");
  const [inputsJson, setInputsJson] = useState(pretty(DEFAULT_INPUTS));
  const [semanticJson, setSemanticJson] = useState(pretty(DEFAULT_SEMANTIC_CASE));
  const [semanticQuery, setSemanticQuery] = useState("tls cleanup");
  const [semanticResults, setSemanticResults] = useState<SemanticCase[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("nvme tcp tls");
  const [memoryResults, setMemoryResults] = useState<EvidenceMemoryItem[]>([]);
  const [providerMatrix, setProviderMatrix] =
    useState<WorkbenchProviderCapabilitiesMatrix | null>(null);
  const [taskRuns, setTaskRuns] = useState<PreparedWorkbenchTaskRun[]>([]);
  const [preparedRun, setPreparedRun] = useState<PreparedWorkbenchTaskRun | null>(null);
  const [artifactManifest, setArtifactManifest] =
    useState<WorkbenchTaskArtifactManifest | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<WorkbenchTaskArtifactContent | null>(null);
  const [workflowExecution, setWorkflowExecution] = useState<WorkflowExecutionResult | null>(null);
  const [workflowOutputMaterialize, setWorkflowOutputMaterialize] =
    useState<MaterializeWorkflowOutputsResult | null>(null);
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

  const saveWorkflow = () =>
    runAction("save-workflow", async () => {
      const payload = parseJsonObject(workflowJson);
      const saved = await api.workbench.workflows.create(payload);
      setSelectedWorkflowId(saved.id);
      setMessage(`Workflow saved: ${saved.id}`);
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
      setWorkflowOutputMaterialize(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`Task run prepared: ${result.task_run_id}`);
    });

  const loadPreparedArtifacts = () =>
    runAction("load-artifacts", async () => {
      if (!preparedRun) return;
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`Artifacts loaded: ${preparedRun.task_run_id}`);
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
      const payload = parseJsonObject(semanticJson);
      const result = await api.workbench.semanticCases.create(payload);
      setMessage(`Semantic case stored: ${result.case_id}`);
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

  const searchMemory = () =>
    runAction("search-memory", async () => {
      const result = await api.workbench.memory.search({
        q: memoryQuery,
        limit: 10,
      });
      setMemoryResults(result.items);
      setMessage(`Memory results: ${result.items.length}`);
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
          <textarea
            value={workflowJson}
            onChange={(event) => setWorkflowJson(event.target.value)}
            className="h-80 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
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
            <label className="block">
              <span className="mb-1 block text-xs text-on-surface-variant">Inputs JSON</span>
              <textarea
                value={inputsJson}
                onChange={(event) => setInputsJson(event.target.value)}
                className="h-40 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
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
            {preparedRun && (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs">
                <p className="font-medium text-on-surface">{preparedRun.task_run_id}</p>
                <p className="mt-1 break-words font-data text-on-surface-variant">
                  {preparedRun.artifact_dir}
                </p>
                <p className="mt-1 text-on-surface-variant">
                  Agent runs: {preparedRun.agent_runs.length}
                </p>
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
                  </div>
                )}
                {workflowOutputMaterialize && (
                  <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                    Output evidence: {workflowOutputMaterialize.status} /{" "}
                    {workflowOutputMaterialize.evidence_count} items
                    {workflowOutputMaterialize.rejected_outputs.length > 0 && (
                      <span className="ml-2 text-warning">
                        rejected {workflowOutputMaterialize.rejected_outputs.length}
                      </span>
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
                          <div className="mt-2 flex flex-wrap gap-2 text-on-surface-variant">
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
            <textarea
              value={semanticJson}
              onChange={(event) => setSemanticJson(event.target.value)}
              className="h-52 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
              spellCheck={false}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                onClick={importSemanticCase}
                disabled={busyAction === "import-semantic-case"}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                <Save size={14} />
                Import case
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
                  </div>
                  {item.path && (
                    <p className="mt-1 break-words font-data text-on-surface-variant">
                      {item.path}
                    </p>
                  )}
                  {item.reason && (
                    <p className="mt-1 text-on-surface-variant">{item.reason}</p>
                  )}
                  {item.source_slices && item.source_slices.length > 0 && (
                    <div className="mt-2 space-y-1 text-on-surface-variant">
                      {item.source_slices.slice(0, 3).map((slice) => (
                        <p key={slice.slice_id} className="break-words font-data text-[11px]">
                          slice {slice.file_path}:{slice.start_line}-{slice.end_line} sha:
                          {slice.sha256.slice(0, 12)}
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

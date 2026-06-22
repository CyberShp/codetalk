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
  PreparedWorkbenchTaskRun,
  SemanticCase,
  WorkflowDefinition,
  WorkflowPreset,
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
  const [taskRuns, setTaskRuns] = useState<PreparedWorkbenchTaskRun[]>([]);
  const [preparedRun, setPreparedRun] = useState<PreparedWorkbenchTaskRun | null>(null);
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
      const [workflowData, taskRunData] = await Promise.all([
        api.workbench.workflows.list(),
        api.workbench.taskRuns.list({ limit: 10 }),
      ]);
      const presetData = await api.workbench.workflows.presets();
      setWorkflows(workflowData);
      setWorkflowPresets(presetData.items);
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
      setMessage(`Task run prepared: ${result.task_run_id}`);
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
            {preparedRun && (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs">
                <p className="font-medium text-on-surface">{preparedRun.task_run_id}</p>
                <p className="mt-1 break-words font-data text-on-surface-variant">
                  {preparedRun.artifact_dir}
                </p>
                <p className="mt-1 text-on-surface-variant">
                  Agent runs: {preparedRun.agent_runs.length}
                </p>
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
                            {validation.rejected_artifacts.length > 0 && (
                              <p className="mt-1 text-amber-400">
                                Rejected: {validation.rejected_artifacts.length}
                              </p>
                            )}
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
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

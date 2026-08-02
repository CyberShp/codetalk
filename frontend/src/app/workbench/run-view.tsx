"use client";

import type { WorkbenchController } from "./workbench-controller";
import { compactMachineToken } from "@/lib/display-text";
import type { WorkbenchTaskArtifact } from "@/lib/types";

export function RunCockpitView({ scope }: { scope: WorkbenchController }) {
  const { AlertTriangle, ArtifactPreviewCard, ClipboardList, Database, Download, Library, Loader2, MessageSquareText, Panel, PlayCircle, RefreshCw, Search, X, acceptanceCodetalkProviderIssues, acceptanceInputRedactionIssues, acceptanceInstructionPolicyIssues, acceptanceIssueLabel, acceptanceProviderIssues, acceptanceWorkflowOutputIssues, agentMcpRequestSummary, agentRunActionBusy, applyWorkspaceSelection, artifactAudience, artifactAudienceGroups, artifactAudienceLabel, artifactContent, artifactManifest, artifactShortName, blackBoxGenerationPolicySummary, busyAction, cancelPreparedTaskRun, compactReasonLabel, createAndRunTaskRun, currentApiBase, evidenceValidationSummary, executePreparedAgentRun, executePreparedWorkflow, executeTaskRerunPlan, executionInputSummary, executionResults, failureRetryContextSummary, fastContextDecisionSummary, filledInputCount, generateTaskAcceptanceAudit, importPreparedSemanticOutputs, inputContextSummary, inputMaterialsSummary, inputTextValue, inputsJson, isFileLikeWorkflowInput, isPatchLikeWorkflowInput, isTaskRunActiveStatus, loadPreparedArtifacts, loadTaskRerunPlan, materializationAuditOutputs, materializePreparedAgentRun, materializePreparedWorkflowOutputs, materializeResults, memoryArtifactSummary, openPreparedConversation, openingConversation, parsedPrepareInputs, prepareTaskRun, preparedProviderReadiness, preparedRun, preparedRunSnapshotSummary, previewArtifact, prioritizedAuditArtifacts, providerDisplayLabel, providerOverride, providerReadinessSummary, providerStatusDisplayLabel, rejectedOutputLabel, rejectedOutputReason, replayPlanSummary, repoPath, requiredInputCount, restoreExistingTaskRun, runExecutorProviderOptions, runPanelCapabilitySummary, runPanelDeliverables, runPanelExecutionNotice, runPanelFailureReasons, runPanelProgress, runPanelStatus, runPhaseCards, runStatusDisplayLabel, safeArtifactDownloadFilename, selectRunWorkflow, selectedAgentSkillIds, selectedAgentSkillInstructions, selectedAgentStep, selectedProviderCapability, selectedRunMcpProfile, selectedRunProvider, selectedWorkflowAudit, selectedWorkflowId, selectedWorkflowInputs, selectedWorkflowOutputs, semanticImportOutputIds, semanticOutputImport, setActiveWorkbenchView, setInputsJson, setProviderOverride, taskAcceptanceAudit, taskRerunExecution, taskRerunHistory, taskRerunPlan, taskRerunPlanValidation, taskRunActionBusy, taskRunEventDetail, taskRunEventTitle, taskRunEventTone, taskRunEvents, taskRunRuntimeStatus, taskRuns, testActivityQuality, updatePrepareInput, uploadPrepareInputFile, validatePreparedAgentRun, validationResults, visibleDeliveryArtifacts, visibleTaskRunEvents, visibleWorkflowInputs, workflowAuditWarningLabel, workflowDisplayName, workflowExecution, workflowInputDisplayName, workflowInputsUpdated, workflowOptions, workflowOutputDisplayName, workflowOutputMaterializationSummary, workflowOutputMaterialize, workspaceId, workspaces } = scope;
  const isV3PreparedRun =
    preparedRun?.workflow_snapshot.compiled_contract_version === 3;
  return (<Panel title="任务运行" icon={<PlayCircle size={16} />} className="ct-run-cockpit-panel">
            <div className="grid gap-4 xl:grid-cols-[minmax(380px,0.95fr)_minmax(440px,1.05fr)] xl:items-start">
              <div className="min-w-0 space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  工作流
                </span>
                <select
                  aria-label="工作流"
                  value={selectedWorkflowId}
                  onChange={(event) =>
                    selectRunWorkflow(event.target.value)
                  }
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                >
                  {[...workflowOptions, selectedWorkflowId]
                    .map((item) =>
                      typeof item === "string"
                        ? { id: item, label: workflowDisplayName(item) }
                        : item,
                    )
                    .filter(
                      (option, index, options) =>
                        option.id &&
                        options.findIndex((item) => item.id === option.id) ===
                          index,
                    )
                    .map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                </select>
              </label>
              {selectedWorkflowAudit &&
                selectedWorkflowAudit.warnings.length > 0 && (
                  <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-300">
                    <div className="flex items-start gap-2">
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="font-medium">
                          工作流审计警告:{" "}
                          {selectedWorkflowAudit.warnings.length}
                        </p>
                        <div className="mt-1 space-y-1">
                          {selectedWorkflowAudit.warnings
                            .slice(0, 3)
                            .map((warning) => (
                              <p
                              key={`${warning.code}-${warning.path}`}
                              className="break-words"
                            >
                                {workflowAuditWarningLabel(warning)}
                              </p>
                            ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-on-surface">
                    测试活动运行摘要
                  </p>
                  <span className="rounded bg-surface px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant">
                    {workflowDisplayName(selectedWorkflowId)}
                  </span>
                </div>
                <div className="grid gap-1.5 font-data text-[11px] text-on-surface-variant sm:grid-cols-2">
                  <span className="break-words">
                    工作空间: {workspaceId || "未选择"}
                  </span>
                  <span className="break-words">
                    源码路径: {repoPath.trim() || "未填写"}
                  </span>
                  <span>
                    执行器: {providerDisplayLabel(selectedRunProvider)}
                    {selectedProviderCapability
                      ? ` (${providerStatusDisplayLabel(selectedProviderCapability.status)})`
                      : ""}
                  </span>
                  <span>MCP: {selectedRunMcpProfile}</span>
                  <span>
                    输入: {filledInputCount}/{selectedWorkflowInputs.length}
                    {requiredInputCount ? ` · 必填 ${requiredInputCount}` : ""}
                  </span>
                  <span>Agent 步骤: {selectedAgentStep ? "1" : "0"}</span>
                </div>
                <div className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5">
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    输出预期
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedWorkflowOutputs.length > 0 ? (
                      selectedWorkflowOutputs.slice(0, 8).map((output) => (
                        <span
                          key={`${String(output.id ?? "output")}:${String(output.artifact ?? "")}`}
                          className="rounded bg-surface-container px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowOutputDisplayName(output)}
                          {output.artifact
                            ? ` -> ${artifactShortName(String(output.artifact))}`
                            : ""}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-on-surface-variant">
                        尚未声明输出
                      </span>
                    )}
                  </div>
                </div>
                <div
                  aria-label="Run constraints"
                  className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                >
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    运行约束
                  </p>
                  <div className="grid gap-1.5 text-[10px] text-on-surface-variant sm:grid-cols-2">
                    <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
                      MCP: {selectedRunMcpProfile || "未启用"}
                    </span>
                    <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
                      Agent: {providerDisplayLabel(selectedRunProvider)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {selectedAgentSkillInstructions.length > 0 ? (
                      selectedAgentSkillInstructions.slice(0, 8).map((skill) => (
                        <span
                          key={String(skill.id ?? skill.label ?? "skill")}
                          className="rounded bg-surface-container px-1.5 py-0.5 text-[10px] text-on-surface-variant"
                        >
                          {String(skill.label ?? skill.id ?? "skill")}
                        </span>
                      ))
                    ) : selectedAgentSkillIds.length > 0 ? (
                      selectedAgentSkillIds.slice(0, 8).map((skillId) => (
                        <span
                          key={skillId}
                          className="rounded bg-surface-container px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant"
                        >
                          {skillId}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-on-surface-variant">
                        未声明 skills
                      </span>
                    )}
                  </div>
                </div>
                <div className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5">
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    运行前检查
                  </p>
                  <div className="grid gap-1 font-data text-[10px] text-on-surface-variant sm:grid-cols-3">
                    <span
                      className={
                        repoPath.trim() ? "text-on-surface" : "text-warning"
                      }
                    >
                      源码路径:{repoPath.trim() ? "ready" : "missing"}
                    </span>
                    <span
                      className={
                        filledInputCount >= requiredInputCount
                          ? "text-on-surface"
                          : "text-warning"
                      }
                    >
                      输入契约:{filledInputCount}/{requiredInputCount}
                    </span>
                    <span
                      className={
                        selectedProviderCapability?.status === "available" ||
                        selectedProviderCapability?.status === "configured"
                          ? "text-on-surface"
                          : "text-warning"
                      }
                    >
                      执行器:
                      {providerStatusDisplayLabel(
                        selectedProviderCapability?.status,
                      )}
                    </span>
                  </div>
                </div>
              </div>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  工作空间
                </span>
                {workspaces.length > 0 ? (
                  <select
                    aria-label="Workspace selector"
                    value={
                      workspaces.some(
                        (workspace) => workspace.id === workspaceId,
                      )
                        ? workspaceId
                        : ""
                    }
                    onChange={(event) => {
                      const workspace = workspaces.find(
                        (item) => item.id === event.target.value,
                      );
                      if (workspace) applyWorkspaceSelection(workspace);
                    }}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  >
                    <option value="" disabled>
                      请选择已创建工作空间
                    </option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name} · {workspace.repo_path}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">
                    还没有可运行的工作空间，请先在工作空间页面创建并完成索引。
                  </div>
                )}
              </label>
              <div className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2">
                <p className="mb-1 text-xs text-on-surface-variant">
                  源码路径来自所选工作空间
                </p>
                <p className="break-all font-data text-xs text-on-surface">
                  {repoPath.trim() || "请选择工作空间"}
                </p>
              </div>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  执行器覆盖
                </span>
                <select
                  aria-label="执行器覆盖"
                  value={providerOverride}
                  onChange={(event) => setProviderOverride(event.target.value)}
                  disabled={!selectedAgentStep}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary disabled:cursor-not-allowed disabled:opacity-55"
                >
                  <option value="">使用工作流默认执行器</option>
                  {runExecutorProviderOptions.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label} ({provider.owner}:
                      {providerStatusDisplayLabel(provider.status)})
                    </option>
                  ))}
                </select>
                {!selectedAgentStep && (
                  <span className="mt-1 block text-[11px] text-on-surface-variant">
                    当前工作流没有 Agent 节点，执行器覆盖不可用。
                  </span>
                )}
              </label>
              {visibleWorkflowInputs.length > 0 && (
                <div
                  aria-label="Workflow run inputs"
                  className={`rounded-lg border p-3 transition-colors ${
                    workflowInputsUpdated
                      ? "border-primary/35 bg-primary/10"
                      : "border-outline-variant/30 bg-surface"
                  }`}
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-medium text-on-surface">
                      工作流输入
                    </p>
                    {workflowInputsUpdated && (
                      <span className="rounded-full bg-primary/12 px-2 py-0.5 text-[10px] font-medium text-primary">
                        已随所选工作流更新
                      </span>
                    )}
                  </div>
                  <div className="space-y-2">
                    {visibleWorkflowInputs.map((input) => {
                      const inputId = String(input.id ?? "");
                      const inputType = String(input.type ?? "text");
                      const required = input.required === true;
                      const role = String(input.role ?? "");
                      const inputName = workflowInputDisplayName(input);
                      const value = inputTextValue(parsedPrepareInputs, input);
                      if (!inputId) return null;
                      if (
                        inputId === "repo_path" &&
                        inputType === "directory" &&
                        workspaces.length > 0
                      ) {
                        return (
                          <label key={inputId} className="block">
                            <span className="mb-1 block text-xs text-on-surface-variant">
                              {inputName}
                              {required ? " *" : ""}
                            </span>
                            <select
                              aria-label={`Workflow input ${inputId}`}
                              value={
                                workspaces.some(
                                  (workspace) => workspace.repo_path === value,
                                )
                                  ? value
                                  : ""
                              }
                              onChange={(event) => {
                                const selected = workspaces.find(
                                  (workspace) =>
                                    workspace.repo_path === event.target.value,
                                );
                                if (selected) applyWorkspaceSelection(selected);
                              }}
                              className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                            >
                              <option value="" disabled>
                                请选择已创建工作空间
                              </option>
                              {workspaces.map((workspace) => (
                                <option
                                  key={workspace.id}
                                  value={workspace.repo_path}
                                >
                                  {workspace.name} · {workspace.repo_path}
                                </option>
                              ))}
                            </select>
                            <p className="mt-1 break-all font-data text-[10px] text-on-surface-variant">
                              {value || role || "从工作空间选择源码路径"}
                            </p>
                          </label>
                        );
                      }
                      if (inputType === "boolean") {
                        return (
                          <label key={inputId} className="block">
                            <span className="mb-1 block text-xs text-on-surface-variant">
                              {inputName}
                              {required ? " *" : ""}
                            </span>
                            <select
                              aria-label={`Workflow input ${inputId}`}
                              value={value === "true" ? "true" : "false"}
                              onChange={(event) =>
                                updatePrepareInput(input, event.target.value)
                              }
                              className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                            >
                              <option value="false">false</option>
                              <option value="true">true</option>
                            </select>
                          </label>
                        );
                      }
                      const multiline =
                        inputType === "file_set" ||
                        inputType === "long_text" ||
                        inputType === "free_text" ||
                        isPatchLikeWorkflowInput(inputType);
                      return (
                        <label key={inputId} className="block">
                          <span className="mb-1 block text-xs text-on-surface-variant">
                            {inputName}
                            {required ? " *" : ""}
                          </span>
                          {multiline ? (
                            <>
                              <textarea
                                aria-label={`Workflow input ${inputId}`}
                                value={value}
                                onChange={(event) =>
                                  updatePrepareInput(input, event.target.value)
                                }
                                placeholder={
                                  inputType === "file_set"
                                    ? "每行一个本地文件路径"
                                    : isPatchLikeWorkflowInput(inputType)
                                      ? "粘贴 unified diff，或上传 .patch/.diff 文件"
                                    : role || "输入文本"
                                }
                                className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                                spellCheck={false}
                              />
                              {inputType === "file_set" && (
                                <input
                                  aria-label={`Upload file for ${inputId}`}
                                  type="file"
                                  multiple
                                  onChange={(event) =>
                                    uploadPrepareInputFile(
                                      input,
                                      event.currentTarget.files,
                                    )
                                  }
                                  className="mt-1 block w-full text-xs text-on-surface-variant file:mr-2 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                                />
                              )}
                              {isPatchLikeWorkflowInput(inputType) && (
                                <input
                                  aria-label={`Upload file for ${inputId}`}
                                  type="file"
                                  onChange={(event) =>
                                    uploadPrepareInputFile(
                                      input,
                                      event.currentTarget.files,
                                    )
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
                                onChange={(event) =>
                                  updatePrepareInput(input, event.target.value)
                                }
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
                                    uploadPrepareInputFile(
                                      input,
                                      event.currentTarget.files,
                                    )
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
              <details className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                <summary className="cursor-pointer text-xs font-medium text-on-surface">
                  高级输入 JSON
                </summary>
                <label className="mt-2 block">
                  <span className="mb-1 block text-xs text-on-surface-variant">
                    输入 JSON
                  </span>
                  <textarea
                    value={inputsJson}
                    onChange={(event) => setInputsJson(event.target.value)}
                    className="h-40 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                    aria-label="Inputs JSON"
                    spellCheck={false}
                  />
                </label>
              </details>
              <button
                onClick={createAndRunTaskRun}
                disabled={taskRunActionBusy || !repoPath.trim()}
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
                disabled={taskRunActionBusy || !repoPath.trim()}
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
                disabled={taskRunActionBusy || !preparedRun}
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
                disabled={taskRunActionBusy || !preparedRun}
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
                disabled={taskRunActionBusy || !preparedRun}
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
                disabled={taskRunActionBusy || !preparedRun}
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
                  taskRunActionBusy ||
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
                  taskRunActionBusy || !preparedRun || !workflowExecution
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
                  taskRunActionBusy ||
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
              </div>
              <aside
                aria-label="运行结果面板"
                className="ct-run-console min-w-0 rounded-xl border border-outline-variant/30 bg-surface/90 p-3 text-xs shadow-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-on-surface">
                      运行控制台
                    </p>
                    <p className="mt-0.5 text-[11px] text-on-surface-variant">
                      当前节点、运行状态和交付件集中显示
                    </p>
                  </div>
                  <span
                    className={[
                      "rounded-full px-2 py-0.5 text-[11px] font-medium",
                      runPanelStatus === "失败"
                        ? "bg-amber-400/10 text-warning"
                        : runPanelStatus === "需复核"
                          ? "bg-amber-400/10 text-amber-700"
                        : runPanelStatus === "已完成"
                          ? "bg-green-500/10 text-green-600"
                          : runPanelStatus === "进行中"
                            ? "bg-primary/10 text-primary"
                            : "bg-surface-container text-on-surface-variant",
                    ].join(" ")}
                  >
                    {runPanelStatus}
                  </span>
                </div>
                <p className="mt-3 text-[11px] font-semibold text-on-surface-variant">
                  运行状态
                </p>
                <div className="mt-1 flex flex-wrap gap-1 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container/40 p-1">
                  {["空", "进行中", "需复核", "失败", "已取消", "已完成"].map((status) => (
                    <span
                      key={status}
                      className={[
                        "rounded-md px-2 py-1 text-[11px] font-medium",
                        runPanelStatus === status
                          ? "bg-surface text-primary shadow-sm"
                          : "text-on-surface-variant",
                      ].join(" ")}
                    >
                      {status === "进行中" ? "运行中" : status}
                    </span>
                  ))}
                </div>
                {!preparedRun ? (
                  <div className="mt-3 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container/40 px-3 py-8 text-center">
                    <PlayCircle
                      size={26}
                      className="mx-auto text-on-surface-variant/60"
                    />
                    <p className="mt-3 text-sm font-semibold text-on-surface">
                      尚无运行记录
                    </p>
                    <p className="mx-auto mt-1 max-w-sm leading-5 text-on-surface-variant">
                      完成左侧输入后启动运行。状态、问题提示和产物会显示在这里。
                    </p>
                  </div>
                ) : (
                  <div className="mt-3 space-y-3">
                    <section
                      className={[
                        "ct-run-state-card rounded-lg border p-3",
                        runPanelStatus === "失败"
                          ? "border-red-300/70 bg-red-50"
                          : runPanelStatus === "需复核"
                            ? "border-amber-300/70 bg-amber-50"
                          : runPanelStatus === "已完成"
                            ? "border-emerald-300/70 bg-emerald-50"
                            : runPanelStatus === "已取消"
                              ? "border-outline-variant/50 bg-surface-container/60"
                            : "border-sky-300/70 bg-sky-50",
                      ].join(" ")}
                    >
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="font-semibold text-on-surface">
                            {runPanelStatus === "失败"
                              ? `运行失败 · ${workflowDisplayName(preparedRun.workflow_id)}`
                              : runPanelStatus === "需复核"
                                ? `需要复核 · ${workflowDisplayName(preparedRun.workflow_id)}`
                              : runPanelStatus === "已取消"
                                ? `已取消 · ${workflowDisplayName(preparedRun.workflow_id)}`
                              : runPanelStatus === "已完成"
                                ? `运行完成 · ${workflowDisplayName(preparedRun.workflow_id)}`
                                : `运行中 · ${workflowDisplayName(preparedRun.workflow_id)}`}
                          </p>
                          <p className="mt-0.5 font-data text-[11px] text-on-surface-variant">
                            任务 {compactMachineToken(preparedRun.task_run_id, 18)} · {runPanelProgress.completed}/{runPanelProgress.total} 节点 · {runPanelProgress.percent}%
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          {runPanelStatus === "进行中" && (
                            <button
                              type="button"
                              onClick={cancelPreparedTaskRun}
                              disabled={
                                busyAction === "cancel-task-run" ||
                                !isTaskRunActiveStatus(
                                  taskRunRuntimeStatus(preparedRun),
                                )
                              }
                              className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700 shadow-sm disabled:opacity-50"
                            >
                              {busyAction === "cancel-task-run" ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <X size={12} />
                              )}
                              取消
                            </button>
                          )}
                          {runPanelStatus === "失败" ? (
                            <AlertTriangle size={22} className="text-red-600" />
                          ) : runPanelStatus === "需复核" ? (
                            <AlertTriangle size={22} className="text-amber-600" />
                          ) : runPanelStatus === "已完成" ? (
                            <span className="grid h-7 w-7 place-items-center rounded-full bg-emerald-100 text-emerald-700">
                              ✓
                            </span>
                          ) : runPanelStatus === "已取消" ? (
                            <span className="grid h-7 w-7 place-items-center rounded-full bg-surface-container-high text-on-surface-variant">
                              <X size={15} />
                            </span>
                          ) : (
                            <Loader2 size={22} className="animate-spin text-primary" />
                          )}
                        </div>
                      </div>
                      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-white/70">
                        <div
                          className={[
                            "h-full rounded-full transition-all",
                            runPanelStatus === "失败"
                              ? "bg-red-500"
                              : runPanelStatus === "需复核"
                                ? "bg-amber-500"
                              : runPanelStatus === "已完成"
                                ? "bg-emerald-500"
                                : runPanelStatus === "已取消"
                                  ? "bg-outline-variant"
                                : "bg-primary",
                          ].join(" ")}
                          style={{ width: `${runPanelProgress.percent}%` }}
                        />
                      </div>
                      {runPanelExecutionNotice && (
                        <div
                          className={[
                            "mb-3 rounded-md border px-2 py-1.5 text-[11px]",
                            runPanelExecutionNotice.subject === "local_static"
                              ? "border-amber-200 bg-amber-50 text-amber-900"
                              : "border-outline-variant/25 bg-white/75 text-on-surface",
                          ].join(" ")}
                        >
                          <span className="font-medium">
                            {runPanelExecutionNotice.label}
                          </span>
                          {runPanelExecutionNotice.message && (
                            <span className="ml-1 text-on-surface-variant">
                              {runPanelExecutionNotice.message}
                            </span>
                          )}
                        </div>
                      )}
                      <div className="space-y-1.5">
                        {runPhaseCards.map((phase, index) => {
                          const phaseStatus = runStatusDisplayLabel(phase.status);
                          const phaseStatusText = ["完成但信息不足", "需要复核"].includes(
                            phase.status,
                          )
                            ? phase.status
                            : phaseStatus;
                          const isCurrent = index === runPanelProgress.currentIndex;
                          return (
                          <div
                            key={phase.label}
                            className={[
                              "flex min-w-0 items-center gap-2 rounded-md border px-2 py-1.5",
                              phaseStatus === "失败"
                                ? "border-red-200 bg-white text-red-700"
                                : phaseStatus === "已完成"
                                  ? "border-emerald-200 bg-white text-emerald-700"
                                  : isCurrent
                                    ? "border-sky-200 bg-white text-primary"
                                    : "border-outline-variant/20 bg-white/70 text-on-surface-variant",
                            ].join(" ")}
                          >
                            <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-current/10 text-[10px]">
                              {phaseStatus === "已完成"
                                ? "✓"
                                : phaseStatus === "失败"
                                  ? "!"
                                  : isCurrent
                                    ? "•"
                                    : ""}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium text-on-surface">
                                {phase.label}
                              </span>
                              <span className="block truncate text-[10px] text-on-surface-variant">
                                {phase.detail}
                              </span>
                            </span>
                            <span className="shrink-0 rounded bg-surface-container px-1.5 py-0.5 text-[10px] font-medium">
                              {phaseStatusText}
                            </span>
                          </div>
                          );
                        })}
                      </div>
                    </section>
                    {preparedRunSnapshotSummary && (
                      <section className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <div>
                            <p className="font-semibold text-on-surface">
                              运行快照
                            </p>
                            <p className="text-[10px] text-on-surface-variant">
                              本次 task_run 冻结的工作流、输入和输出契约
                            </p>
                          </div>
                          <span className="rounded bg-surface px-2 py-0.5 font-data text-[10px] text-on-surface-variant">
                            frozen
                          </span>
                        </div>
                        <div className="space-y-1 rounded-md bg-surface px-2 py-2 text-[11px] text-on-surface">
                          <p className="break-words font-data">
                            {preparedRunSnapshotSummary.workflow}
                          </p>
                          <p className="break-words text-on-surface-variant">
                            {preparedRunSnapshotSummary.repo}
                          </p>
                          <p className="text-on-surface-variant">
                            {preparedRunSnapshotSummary.steps}
                          </p>
                        </div>
                        {preparedRunSnapshotSummary.inputs.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {preparedRunSnapshotSummary.inputs.slice(0, 5).map((input) => (
                              <p
                                key={`${input.label}-${input.value}`}
                                className="rounded-md bg-surface px-2 py-1 text-[11px] text-on-surface"
                              >
                                {input.label}: {input.value}
                              </p>
                            ))}
                          </div>
                        )}
                        {preparedRunSnapshotSummary.outputs.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {preparedRunSnapshotSummary.outputs.map((output) => (
                              <span
                                key={`${output.label}-${output.artifact}`}
                                className="rounded-md bg-surface px-2 py-1 text-[11px] text-primary"
                                title={output.label}
                              >
                                输出: {output.artifact}
                              </span>
                            ))}
                          </div>
                        )}
                      </section>
                    )}
                    {runPanelCapabilitySummary && (
                      <section
                        aria-label="能力就绪面板"
                        className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3"
                      >
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="font-semibold text-on-surface">
                              能力就绪
                            </p>
                            <p className="text-[10px] text-on-surface-variant">
                              执行器、MCP、skills 和产物契约会随本次运行一起传给 Agent
                            </p>
                          </div>
                          <span
                            className={[
                              "rounded-full px-2 py-0.5 text-[10px] font-medium",
                              runPanelCapabilitySummary.warnings.length === 0 &&
                              (preparedProviderReadiness?.status === "ready" ||
                              preparedProviderReadiness?.status === "ok")
                                ? "bg-emerald-100 text-emerald-700"
                                : "bg-amber-100 text-amber-800",
                            ].join(" ")}
                          >
                            {runPanelCapabilitySummary.warnings.length > 0
                              ? "降级可用"
                              : providerStatusDisplayLabel(
                                  preparedProviderReadiness?.status ?? "pending",
                                )}
                          </span>
                        </div>
                        {runPanelCapabilitySummary.rows.length > 0 && (
                          <div className="grid gap-1.5 sm:grid-cols-2">
                            {runPanelCapabilitySummary.rows.slice(0, 6).map((row) => (
                              <div
                                key={row.id}
                                className="min-w-0 rounded-md bg-surface px-2 py-1.5"
                              >
                                <div className="flex min-w-0 items-center justify-between gap-2">
                                  <span className="truncate text-[11px] font-medium text-on-surface">
                                    {row.label}
                                  </span>
                                  <span
                                    className={[
                                      "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                                      row.tone === "ok"
                                        ? "bg-emerald-100 text-emerald-700"
                                        : row.tone === "warning"
                                          ? "bg-amber-100 text-amber-800"
                                          : "bg-surface-container text-on-surface-variant",
                                    ].join(" ")}
                                  >
                                    {row.value}
                                  </span>
                                </div>
                                {row.detail && (
                                  <p className="mt-1 line-clamp-2 break-words text-[10px] leading-4 text-on-surface-variant">
                                    {row.detail}
                                  </p>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="mt-2 space-y-1.5">
                          <p className="rounded-md bg-surface px-2 py-1 text-[11px] text-on-surface-variant">
                            MCP:
                            <span className="ml-1 text-on-surface">
                              {runPanelCapabilitySummary.mcpProfiles.length > 0
                                ? runPanelCapabilitySummary.mcpProfiles
                                    .slice(0, 5)
                                    .join("、")
                                : "未声明"}
                            </span>
                          </p>
                          <p className="rounded-md bg-surface px-2 py-1 text-[11px] text-on-surface-variant">
                            技能:
                            <span className="ml-1 text-on-surface">
                              {runPanelCapabilitySummary.skills.length > 0
                                ? runPanelCapabilitySummary.skills
                                    .slice(0, 5)
                                    .join("、")
                                : "未声明"}
                            </span>
                          </p>
                          <p className="rounded-md bg-surface px-2 py-1 text-[11px] text-on-surface-variant">
                            必需产物:
                            <span className="ml-1 text-on-surface">
                              {runPanelCapabilitySummary.requiredArtifacts.length > 0
                                ? runPanelCapabilitySummary.requiredArtifacts
                                    .join("、")
                                : "跟随工作流输出契约"}
                            </span>
                          </p>
                        </div>
                        {runPanelCapabilitySummary.warnings.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {runPanelCapabilitySummary.warnings.slice(0, 3).map((warning) => (
                              <p
                                key={warning}
                                className="rounded-md bg-amber-50 px-2 py-1 text-[11px] leading-4 text-amber-900"
                              >
                                {warning}
                              </p>
                            ))}
                          </div>
                        )}
                      </section>
                    )}
                    <section className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div>
                          <p className="font-semibold text-on-surface">
                            运行动态
                          </p>
                          <p className="text-[10px] text-on-surface-variant">
                            后台事件实时刷新，最新 8 条
                          </p>
                        </div>
                        <span className="font-data text-[10px] text-on-surface-variant">
                          {taskRunEvents.length} 条
                        </span>
                      </div>
                      {visibleTaskRunEvents.length > 0 ? (
                        <div className="space-y-1.5">
                          {visibleTaskRunEvents.map((event) => {
                            const tone = taskRunEventTone(event.event_type);
                            const detail = taskRunEventDetail(event);
                            return (
                              <div
                                key={event.event_id}
                                className="flex min-w-0 items-start gap-2 rounded-md bg-surface px-2 py-1.5"
                              >
                                <span
                                  className={[
                                    "mt-1 h-2 w-2 shrink-0 rounded-full",
                                    tone === "danger"
                                      ? "bg-red-500"
                                      : tone === "success"
                                        ? "bg-emerald-500"
                                        : tone === "primary"
                                          ? "bg-primary"
                                          : "bg-on-surface-variant/50",
                                  ].join(" ")}
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-[12px] font-medium text-on-surface">
                                    {taskRunEventTitle(event)}
                                  </span>
                                  {detail && (
                                    <span className="mt-0.5 block break-words text-[10px] leading-4 text-on-surface-variant">
                                      {detail}
                                    </span>
                                  )}
                                </span>
                                <span className="shrink-0 font-data text-[10px] text-on-surface-variant">
                                  #{event.event_id}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <p className="rounded-md border border-dashed border-outline-variant/30 bg-surface px-2 py-3 text-center text-on-surface-variant">
                          启动运行后展示排队、节点执行、产物生成和失败事件
                        </p>
                      )}
                    </section>
                    {runPanelFailureReasons.length > 0 && (
                      <section
                        className={`rounded-lg border p-3 ${
                          runPanelStatus === "失败"
                            ? "border-red-300/70 bg-red-50"
                            : "border-amber-300/70 bg-amber-50"
                        }`}
                      >
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                          <p
                            className={`font-semibold ${
                              runPanelStatus === "失败"
                                ? "text-red-800"
                                : "text-amber-800"
                            }`}
                          >
                            {runPanelStatus === "失败"
                              ? "失败原因"
                              : "验收提醒"}
                          </p>
                          {runPanelStatus === "失败" ? (
                            <div className="flex flex-wrap gap-1.5">
                              <button
                                type="button"
                                onClick={executeTaskRerunPlan}
                                disabled={
                                  taskRunActionBusy ||
                                  !preparedRun ||
                                  !taskRerunPlanValidation?.can_rerun
                                }
                                className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700 disabled:opacity-50"
                              >
                                <RefreshCw size={12} />
                                从失败节点重试
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setActiveWorkbenchView("workflow");
                                }}
                                className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700"
                              >
                                编辑工作流
                              </button>
                              {preparedRun && (
                                <a
                                  href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(preparedRun.task_run_id)}/diagnostic-package`}
                                  download={`${preparedRun.task_run_id}-diagnostic.zip`}
                                  className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700"
                                >
                                  <Download size={12} />
                                  下载诊断包
                                </a>
                              )}
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                setActiveWorkbenchView("workflow");
                              }}
                              className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-amber-800"
                            >
                              调整验收规则
                            </button>
                          )}
                        </div>
                        <div className="space-y-1.5">
                          {runPanelFailureReasons.map((reason) => (
                            <div
                              key={reason}
                              className={`flex items-start gap-2 rounded-md bg-white px-2 py-1.5 ${
                                runPanelStatus === "失败"
                                  ? "text-red-800"
                                  : "text-amber-900"
                              }`}
                            >
                              <AlertTriangle
                                size={13}
                                className={`mt-0.5 shrink-0 ${
                                  runPanelStatus === "失败"
                                    ? "text-red-600"
                                    : "text-amber-600"
                                }`}
                              />
                              <span className="min-w-0 break-words">
                                {reason}
                              </span>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}
                    {testActivityQuality?.status && (
                      <section
                        className={[
                          "rounded-lg border p-3",
                          ["needs_rework", "invalid"].includes(
                            String(testActivityQuality.status).toLowerCase(),
                          )
                            ? "border-amber-300/70 bg-amber-50"
                            : "border-emerald-300/70 bg-emerald-50",
                        ].join(" ")}
                      >
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <div>
                            <p className="font-semibold text-on-surface">
                              质量审计 · {testActivityQuality.deliverable ? "可交付" : "需要补证据"}
                            </p>
                            <p className="text-[10px] text-on-surface-variant">
                              分数 {Number(testActivityQuality.score ?? 0)} · 问题 {Number(testActivityQuality.issue_count ?? 0)}
                            </p>
                          </div>
                          <button
                            type="button"
                            onClick={() => previewArtifact("test_activity_quality_audit.json")}
                            disabled={taskRunActionBusy}
                            className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-primary disabled:opacity-50"
                          >
                            查看详情
                          </button>
                        </div>
                        {testActivityQuality.recommendations?.[0] && (
                          <p className="rounded-md bg-white px-2 py-1.5 text-[12px] text-on-surface">
                            {testActivityQuality.recommendations[0]}
                          </p>
                        )}
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={loadTaskRerunPlan}
                            disabled={taskRunActionBusy || !preparedRun}
                            className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-primary disabled:opacity-50"
                          >
                            生成补证据计划
                          </button>
                          <button
                            type="button"
                            onClick={executeTaskRerunPlan}
                            disabled={
                              taskRunActionBusy ||
                              !preparedRun ||
                              !taskRerunPlanValidation?.can_rerun
                            }
                            className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-primary disabled:opacity-50"
                            title={
                              taskRerunPlanValidation?.can_rerun
                                ? "按复跑计划只重跑低质量或阻塞节点"
                                : "请先生成补证据计划并通过复跑校验"
                            }
                          >
                            只重跑低质量交付件
                          </button>
                        </div>
                      </section>
                    )}
                    <section className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div>
                          <p className="font-semibold text-on-surface">
                            产物与结果
                          </p>
                          <p className="text-[10px] text-on-surface-variant">
                            最终结果优先展示，诊断信息默认折叠
                          </p>
                        </div>
                        <span className="text-[11px] text-on-surface-variant">
                          {runPanelDeliverables.length > 0
                            ? `${runPanelDeliverables.length} 个交付件`
                            : `${artifactManifest?.artifacts.length ?? 0} 个文件`}
                        </span>
                      </div>
                      <div className="space-y-1.5">
                        {runPanelDeliverables.length > 0 && (
                          <div className="grid gap-1.5 sm:grid-cols-2">
                            {runPanelDeliverables.map((deliverable) => {
                              const path =
                                deliverable.path || deliverable.artifact || "";
                              return (
                                <button
                                  key={`${deliverable.id}:${path}`}
                                  type="button"
                                  aria-label={`预览交付件 ${path}`}
                                  onClick={() => previewArtifact(path)}
                                  disabled={!path || taskRunActionBusy}
                                  className="rounded-md border border-outline-variant/25 bg-surface px-2 py-2 text-left transition-colors hover:bg-surface-container-high disabled:opacity-50"
                                >
                                  <span className="block truncate text-sm font-medium text-on-surface">
                                    {deliverable.label ||
                                      deliverable.artifact ||
                                      deliverable.id}
                                  </span>
                                  <span className="mt-0.5 block truncate font-data text-[10px] text-on-surface-variant">
                                    {path}
                                  </span>
                                  <span className="mt-1 inline-flex rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                                    {deliverable.status_label || "已生成"}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                        {(
                          [
                            ["deliverable", artifactAudienceGroups.deliverable],
                            ["input", artifactAudienceGroups.input],
                            ["support", artifactAudienceGroups.support],
                            [
                              "diagnostic",
                              artifactAudienceGroups.diagnostic,
                            ],
                          ] as const
                        ).map(([audience, artifacts]) => {
                          if (artifacts.length === 0) return null;
                          const label = `${artifactAudienceLabel(audience)} ${artifacts.length}`;
                          const artifactButtons = artifacts
                            .slice(0, 10)
                            .map((artifact) => (
                              <button
                              key={artifact.relative_path}
                              type="button"
                              aria-label={`快速预览 ${artifact.kind}:${artifact.relative_path}${
                                artifact.preview_redacted ? " 已脱敏" : ""
                              }`}
                                onClick={() =>
                                  previewArtifact(artifact.relative_path)
                                }
                                disabled={
                                  taskRunActionBusy ||
                                  busyAction ===
                                    `preview-artifact-${artifact.relative_path}`
                                }
                              className="rounded bg-surface px-2 py-1 text-left font-data text-[10px] text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              <span className="block max-w-full truncate text-on-surface">
                                {artifactShortName(artifact.relative_path)}
                              </span>
                              <span className="block truncate">
                                {artifact.kind}
                              </span>
                              {artifact.preview_redacted && (
                                <span className="ml-1 text-warning">
                                  已脱敏
                                  </span>
                                )}
                              </button>
                            ));
                          return (
                            <details
                              key={audience}
                              className="rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                              open={
                                audience === "deliverable" &&
                                runPanelDeliverables.length === 0
                              }
                            >
                              <summary className="cursor-pointer select-none font-medium text-on-surface">
                                {runPanelDeliverables.length > 0
                                  ? `全部运行文件 · ${label}`
                                  : label}
                              </summary>
                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                {artifactButtons}
                              </div>
                            </details>
                          );
                        })}
                        {!artifactManifest?.artifacts.length &&
                          runPanelDeliverables.length === 0 && (
                          <p className="rounded-md border border-dashed border-outline-variant/30 bg-surface px-2 py-3 text-center text-on-surface-variant">
                            准备运行后展示运行产物
                          </p>
                        )}
                        {artifactContent && (
                          <ArtifactPreviewCard
                            artifactContent={artifactContent}
                            fullDownloadHref={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(preparedRun.task_run_id)}/artifacts/download/${artifactContent.relative_path.split("/").map((part) => encodeURIComponent(part)).join("/")}`}
                          />
                        )}
                      </div>
                    </section>
                    <details className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                      <summary className="cursor-pointer font-semibold text-on-surface">
                        技术诊断
                      </summary>
                      <div className="mt-2 space-y-1 font-data text-[10px] text-on-surface-variant">
                        <p className="break-words">
                          task_run_id:{compactMachineToken(preparedRun.task_run_id, 24)}
                        </p>
                        <p className="break-words">
                          provider:
                          {preparedProviderReadiness?.status ?? "pending"}
                        </p>
                        {preparedProviderReadiness?.warnings
                          .slice(0, 4)
                          .map((warning, index) => (
                            <p key={`${compactReasonLabel(warning)}:${index}`} className="break-words">
                              warning:{compactReasonLabel(warning)}
                            </p>
                          ))}
                      </div>
                    </details>
                  </div>
                )}
              </aside>
              <div className="min-w-0 space-y-3">
              {preparedRun && (
                <details
                  aria-label="运行详细诊断"
                  className="rounded-xl border border-outline-variant/30 bg-surface/80 p-3 text-xs"
                >
                  <summary className="cursor-pointer select-none text-sm font-semibold text-on-surface">
                    查看详细诊断与原始产物
                  </summary>
                  <div className="mt-3 space-y-3">
                <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-on-surface">
                        Agent 运行阶段
                      </p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {compactMachineToken(preparedRun.task_run_id, 24)}
                      </span>
                    </div>
                    <div className="grid gap-1.5 sm:grid-cols-4">
                      {runPhaseCards.map((phase) => (
                        <div
                          key={phase.label}
                          className="min-w-0 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                        >
                          <p className="text-[11px] font-medium text-on-surface">
                            {phase.label}
                          </p>
                          <p className="mt-0.5 font-data text-[10px] text-primary">
                            {phase.status}
                          </p>
                          <p className="mt-0.5 truncate text-[10px] text-on-surface-variant">
                            {phase.detail}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                    <p className="mb-2 text-xs font-semibold text-on-surface">
                      可信度与可用性
                    </p>
                    <div className="flex flex-wrap gap-1.5 font-data text-[10px] text-on-surface-variant">
                      <span
                        className={[
                          "rounded bg-surface px-1.5 py-0.5",
                          preparedProviderReadiness?.status === "ready" ||
                          preparedProviderReadiness?.status === "ok"
                            ? "text-on-surface"
                            : "text-warning",
                        ].join(" ")}
                      >
                        执行器：
                        {providerStatusDisplayLabel(preparedProviderReadiness?.status)}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        产物：{artifactManifest?.artifacts.length ?? 0}
                      </span>
                      <span
                        className={[
                          "rounded bg-surface px-1.5 py-0.5",
                          (taskAcceptanceAudit?.summary.missing_required ?? 0) >
                          0
                            ? "text-warning"
                            : "text-on-surface",
                        ].join(" ")}
                      >
                        缺少必需项:
                        {taskAcceptanceAudit?.summary.missing_required ?? 0}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        证据：
                        {workflowExecution?.evidence_materialization
                          ?.evidence_count ??
                          workflowOutputMaterialize?.evidence_count ??
                          0}
                      </span>
                    </div>
                    {preparedProviderReadiness?.warnings.length ? (
                      <p className="mt-1 truncate text-[10px] text-warning">
                        {compactReasonLabel(preparedProviderReadiness.warnings[0])}
                      </p>
                    ) : null}
                  </div>
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3 lg:col-span-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-on-surface">
                        交付物状态
                      </p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        可预览 / 可下载
                      </span>
                    </div>
                    <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
                      {visibleDeliveryArtifacts.length > 0 ? (
                        visibleDeliveryArtifacts.map((artifact) => (
                          <button
                            key={artifact.relative_path}
                            type="button"
                            onClick={() => previewArtifact(artifact.relative_path)}
                            className="min-w-0 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5 text-left transition-colors hover:bg-surface-container-high"
                          >
                            <span className="block truncate text-[11px] font-medium text-on-surface">
                              {artifactShortName(artifact.relative_path)}
                            </span>
                            <span className="block truncate font-data text-[10px] text-on-surface-variant">
                              {artifact.kind} · sha:
                              {artifact.sha256.slice(0, 8)}
                            </span>
                          </button>
                        ))
                      ) : (
                        <span className="text-[11px] text-on-surface-variant">
                          准备运行后展示交付物清单
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs">
                  <p className="font-medium text-on-surface">
                    {compactMachineToken(preparedRun.task_run_id, 24)}
                  </p>
                  <p className="mt-1 break-words font-data text-on-surface-variant">
                    {preparedRun.artifact_dir}
                  </p>
                  <p className="mt-1 text-on-surface-variant">
                    Agent runs: {preparedRun.agent_runs.length}
                  </p>
                  <button
                    type="button"
                    onClick={openPreparedConversation}
                    disabled={openingConversation || taskRunActionBusy}
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
                    taskAcceptanceAudit.task_run_id ===
                      preparedRun.task_run_id && (
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
                            artifacts:
                            {taskAcceptanceAudit.summary.artifact_count}
                          </span>
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            required:
                            {taskAcceptanceAudit.summary.required_checks}
                          </span>
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              taskAcceptanceAudit.summary.missing_required > 0
                                ? "text-warning"
                                : ""
                            }`}
                          >
                            缺少必需项:
                            {taskAcceptanceAudit.summary.missing_required}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            recommended:
                            {taskAcceptanceAudit.summary.recommended_checks}
                          </span>
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              taskAcceptanceAudit.summary.missing_recommended >
                              0
                                ? "text-warning"
                                : ""
                            }`}
                          >
                            缺少建议项:
                            {taskAcceptanceAudit.summary.missing_recommended}
                          </span>
                        </div>
                        {(() => {
                          const providerIssues =
                            acceptanceProviderIssues(taskAcceptanceAudit);
                          if (providerIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                Agent 执行器就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {providerIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.provider}
                                    className="break-words"
                                  >
                                    {issue.provider}:{runStatusDisplayLabel(issue.status)}
                                    {issue.usedFallback ? " 已使用备用命令" : ""}
                                    {issue.deploymentTaskProbeStatus
                                      ? ` 部署探测:${runStatusDisplayLabel(issue.deploymentTaskProbeStatus)}`
                                      : ""}
                                    {issue.deploymentEvidenceConflict
                                      ? " 部署证据冲突"
                                      : ""}
                                    {issue.deploymentProbeId
                                      ? ` 探测编号:${issue.deploymentProbeId}`
                                      : ""}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.startupProbeEndpoint
                                      ? ` 探测:${issue.startupProbeEndpoint}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const providerIssues =
                            acceptanceCodetalkProviderIssues(
                              taskAcceptanceAudit,
                            );
                          if (providerIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                CodeTalk 工具就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {providerIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.provider}
                                    className="break-words"
                                  >
                                    {issue.provider}:{runStatusDisplayLabel(issue.status)}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.startupProbeEndpoint
                                      ? ` 检查:${issue.startupProbeEndpoint}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const outputIssues =
                            acceptanceWorkflowOutputIssues(taskAcceptanceAudit);
                          if (outputIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                工作流输出就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {outputIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.outputId}
                                    className="break-words"
                                  >
                                    {issue.outputId}:{runStatusDisplayLabel(issue.status)}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.artifact
                                      ? ` 产物:${issue.artifact}`
                                      : ""}
                                    {issue.schemaErrorCount > 0
                                      ? ` Schema 错误:${issue.schemaErrorCount}`
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
                                Agent 输入脱敏
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {redactionIssues.slice(0, 4).map((issue) => (
                                  <div key={issue.id} className="break-words">
                                    {issue.label}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.stdinSha
                                      ? ` stdin-sha:${issue.stdinSha.slice(0, 12)}`
                                      : ""}
                                    {issue.relativePath
                                      ? ` 产物:${issue.relativePath}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const policyIssues =
                            acceptanceInstructionPolicyIssues(
                              taskAcceptanceAudit,
                            );
                          if (policyIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                Agent 指令策略
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {policyIssues.slice(0, 4).map((issue) => (
                                  <div key={issue.id} className="break-words">
                                    {issue.label}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.expectedFiles.length > 0
                                      ? ` 期望文件:${issue.expectedFiles.slice(0, 3).join(",")}`
                                      : ""}
                                    {issue.relativePath
                                      ? ` 产物:${issue.relativePath}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {taskAcceptanceAudit.missing_required.length > 0 && (
                          <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                            {taskAcceptanceAudit.missing_required
                              .slice(0, 3)
                              .map((item, index) => (
                                <div
                                  key={`${String(item.id ?? index)}:${index}`}
                                >
                                  缺失项 {index + 1}: {acceptanceIssueLabel(item)}
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    )}
                  {taskRerunPlan &&
                    taskRerunPlan.task_run_id === preparedRun.task_run_id && (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>
                          复跑计划: {runStatusDisplayLabel(taskRerunPlan.status)} / 步骤{" "}
                          {taskRerunPlan.steps?.length ?? 0}
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            保留输入:
                            {String(taskRerunPlan.preserve_inputs ?? false)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            复用任务包:
                            {String(taskRerunPlan.reuse_task_bundle ?? false)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            历史:{taskRerunHistory?.count ?? 0}
                          </span>
                          {(taskRerunPlan.blocked_outputs?.length ?? 0) > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              阻塞:
                              {taskRerunPlan.blocked_outputs?.length ?? 0}
                            </span>
                          ) : null}
                        </div>
                        {taskRerunPlanValidation &&
                          taskRerunPlanValidation.task_run_id ===
                            preparedRun.task_run_id && (
                            <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                              <span
                                className={`rounded bg-surface px-1.5 py-0.5 ${
                                  taskRerunPlanValidation.can_rerun
                                    ? ""
                                    : "text-warning"
                                }`}
                              >
                                校验:{runStatusDisplayLabel(taskRerunPlanValidation.status)}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                可复跑:
                                {String(taskRerunPlanValidation.can_rerun)}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                检查项:
                                {taskRerunPlanValidation.checks?.length ?? 0}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                步骤:
                                {taskRerunPlanValidation.steps?.length ?? 0}
                              </span>
                            </div>
                          )}
                        {taskRerunExecution && (
                          <div className="mt-1 space-y-0.5 font-data text-[10px] text-on-surface-variant">
                            <p>
                              复跑执行:{runStatusDisplayLabel(taskRerunExecution.status)}{" "}
                              工作流:
                              {runStatusDisplayLabel(taskRerunExecution.execution?.status ?? "")}
                            </p>
                            {(() => {
                              const latest = taskRerunHistory?.records?.at(-1);
                              if (!latest) return null;
                              const execution =
                                latest.execution &&
                                typeof latest.execution === "object"
                                  ? (latest.execution as Record<
                                      string,
                                      unknown
                                    >)
                                  : {};
                              const executionArtifactRecord =
                                execution.artifact &&
                                typeof execution.artifact === "object"
                                  ? (execution.artifact as Record<
                                      string,
                                      unknown
                                    >)
                                  : {};
                              const latestArtifactRecord =
                                latest.artifact &&
                                typeof latest.artifact === "object"
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
                    const readiness = providerReadinessSummary(
                      preparedRun.task_bundle,
                    );
                    if (!readiness) return null;
                    const visibleCodetalk = readiness.codetalkProviders.filter(
                      (provider) =>
                        ["gitnexus", "cgc", "local-search"].includes(
                          provider.provider,
                        ),
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
                            {providerStatusDisplayLabel(readiness.status)}
                          </span>
                          <span className="ml-2">
                            源码工作区：{providerStatusDisplayLabel(readiness.repoStatus)}
                          </span>
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
                              {providerDisplayLabel(provider.provider)}：
                              {providerStatusDisplayLabel(provider.status)}
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
                              ]
                                .filter(Boolean)
                                .join(" / ")}
                            >
                              {providerDisplayLabel(provider.provider)}：
                              {providerStatusDisplayLabel(provider.status)}
                              {provider.deploymentTaskProbeStatus && (
                                <span className="ml-1">
                                  部署探测：{runStatusDisplayLabel(provider.deploymentTaskProbeStatus)}
                                </span>
                              )}
                              {provider.deploymentEvidenceConflict && (
                                <span className="ml-1">部署证据冲突</span>
                              )}
                            </span>
                          ))}
                          {readiness.blockingReasons.length > 0 && (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              阻塞项：{readiness.blockingReasons.map(compactReasonLabel).join("、")}
                            </span>
                          )}
                          {readiness.warnings.length > 0 && (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              提醒：{readiness.warnings.length} 条
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
                                  {providerDisplayLabel(provider.provider)}
                                  {provider.configuredCommand
                                    ? ` 命令:${provider.configuredCommand}`
                                    : ""}
                                  {provider.usedFallback ? " 已使用备用命令" : ""}
                                  {provider.reason
                                    ? ` 原因:${compactReasonLabel(provider.reason)}`
                                    : ""}
                                  {provider.startupProbeEndpoint
                                    ? ` 探测:${provider.startupProbeEndpoint}`
                                    : ""}
                                  {provider.manualProbeCommand
                                    ? ` 手动检查:${provider.manualProbeCommand}`
                                    : ""}
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  {(() => {
                    const contextBundle = preparedRun.task_bundle
                      .context_bundle as
                      | {
                          evidence?: unknown[];
                          deployment_evidence?: unknown[];
                          semantic_cases?: unknown[];
                        }
                      | undefined;
                    if (!contextBundle) return null;
                    return (
                      <p className="mt-1 text-on-surface-variant">
                        Context: evidence {contextBundle.evidence?.length ?? 0}{" "}
                        / deployment{" "}
                        {contextBundle.deployment_evidence?.length ?? 0} /
                        semantics {contextBundle.semantic_cases?.length ?? 0}
                      </p>
                    );
                  })()}
                  {(() => {
                    const instructions = preparedRun.task_bundle
                      .agent_instructions as
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
                    const summary = inputContextSummary(
                      preparedRun.task_bundle,
                    );
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
                                <span className="ml-1">
                                  chunks:{input.chunkCount}
                                </span>
                                {input.textTruncated && (
                                  <span className="ml-1 text-warning">
                                    truncated
                                  </span>
                                )}
                                {input.parseWarnings.length > 0 && (
                                  <span className="ml-1 break-words text-warning">
                                    warnings:
                                    {input.parseWarnings.slice(0, 2).join(",")}
                                    {input.parseWarnings.length > 2
                                      ? ",..."
                                      : ""}
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
                    const requests = agentMcpRequestSummary(
                      preparedRun.task_bundle,
                    );
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
                              <span className="ml-1">
                                {request.inputType || "input"}
                              </span>
                              <span className="ml-1">
                                owner:{request.credentialOwner || "agent_cli"}
                              </span>
                              <span
                                className={`ml-1 ${
                                  request.codetalkFetchAllowed
                                    ? "text-warning"
                                    : ""
                                }`}
                              >
                                codetalk-fetch:
                                {String(request.codetalkFetchAllowed)}
                              </span>
                              {request.mcpProfiles.length > 0 && (
                                <span className="ml-1">
                                  profiles:{request.mcpProfiles.join(",")}
                                </span>
                              )}
                              {request.requiredArtifacts.length > 0 && (
                                <span className="ml-1 break-words">
                                  artifacts:
                                  {request.requiredArtifacts
                                    .slice(0, 4)
                                    .join(",")}
                                </span>
                              )}
                            </div>
                          ))}
                          {requests.length > 4 && (
                            <p className="font-data text-[10px]">
                              +{requests.length - 4} more
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                  {(() => {
                    const summary = fastContextDecisionSummary(
                      preparedRun.task_bundle,
                    );
                    if (!summary) return null;
                    return (
                      <p className="mt-1 font-data text-[11px] text-on-surface-variant">
                        {summary}
                      </p>
                    );
                  })()}
                  {artifactManifest &&
                    artifactManifest.task_run_id ===
                      preparedRun.task_run_id && (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        {(() => {
                          const sortedArtifacts = prioritizedAuditArtifacts(
                            artifactManifest.artifacts,
                          );
                          const groupedArtifacts = {
                            deliverable: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "deliverable",
                            ),
                            input: sortedArtifacts.filter(
                              (artifact) => artifactAudience(artifact) === "input",
                            ),
                            support: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "support",
                            ),
                            diagnostic: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "diagnostic",
                            ),
                          };
                          const primaryArtifacts =
                            groupedArtifacts.deliverable.length > 0
                              ? groupedArtifacts.deliverable
                              : groupedArtifacts.support.slice(0, 6);
                          const supportArtifacts =
                            groupedArtifacts.deliverable.length > 0
                              ? groupedArtifacts.support
                              : groupedArtifacts.support.slice(6);
                          const secondaryGroups = [
                            ["input", groupedArtifacts.input],
                            ["support", supportArtifacts],
                            ["diagnostic", groupedArtifacts.diagnostic],
                          ] as const;
                          const artifactSummary = [
                            `${artifactAudienceLabel("deliverable")}: ${
                              groupedArtifacts.deliverable.length
                            }`,
                            `${artifactAudienceLabel("input")}: ${
                              groupedArtifacts.input.length
                            }`,
                            `${artifactAudienceLabel("diagnostic")}: ${
                              groupedArtifacts.diagnostic.length
                            }`,
                          ].join(" · ");
                          const visibleArtifacts = primaryArtifacts.slice(0, 10);
                          const hiddenArtifacts = primaryArtifacts.slice(
                            visibleArtifacts.length,
                          );
                          const artifactButton = (
                            artifact: WorkbenchTaskArtifact,
                          ) => (
                            <button
                              key={artifact.relative_path}
                              onClick={() =>
                                previewArtifact(artifact.relative_path)
                              }
                              disabled={
                                taskRunActionBusy ||
                                busyAction ===
                                  `preview-artifact-${artifact.relative_path}`
                              }
                              className="rounded bg-surface px-1.5 py-0.5 text-left font-data text-[10px] transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              {artifact.kind}:{artifact.relative_path}
                              {artifact.preview_redacted && (
                                <span className="ml-1 text-warning">
                                  已脱敏
                                </span>
                              )}
                            </button>
                          );
                          return (
                            <>
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-medium text-on-surface">
                                  运行产物
                                </span>
                                <span className="font-data text-[10px]">
                                  {artifactSummary}
                                </span>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                {visibleArtifacts.map(artifactButton)}
                              </div>
                              {hiddenArtifacts.length > 0 && (
                                <details className="mt-1 rounded bg-surface/70 px-2 py-1">
                                  <summary className="cursor-pointer text-[11px] font-medium text-on-surface">
                                    展开其余 {hiddenArtifacts.length} 个交付文件
                                  </summary>
                                  <div className="mt-1 flex flex-wrap gap-1.5">
                                    {hiddenArtifacts.map(artifactButton)}
                                  </div>
                                </details>
                              )}
                              {secondaryGroups.map(([audience, artifacts]) =>
                                artifacts.length > 0 ? (
                                  <details
                                    key={audience}
                                    className="mt-1 rounded bg-surface/70 px-2 py-1"
                                  >
                                    <summary className="cursor-pointer text-[11px] font-medium text-on-surface">
                                      {artifactAudienceLabel(audience)}{" "}
                                      {artifacts.length}
                                    </summary>
                                    <div className="mt-1 flex flex-wrap gap-1.5">
                                      {artifacts.map(artifactButton)}
                                    </div>
                                  </details>
                                ) : null,
                              )}
                            </>
                          );
                        })()}
                        {artifactContent && (
                          <div className="mt-2 rounded border border-outline-variant/30 bg-surface p-2">
                            <div className="flex flex-wrap items-center gap-2 text-[11px]">
                              <span className="font-medium text-on-surface">
                                {artifactContent.relative_path}
                              </span>
                              <span className="font-data">
                                {artifactContent.kind}
                              </span>
                              <span className="font-data">
                                sha:{artifactContent.sha256.slice(0, 12)}
                              </span>
                              {artifactContent.truncated && (
                                <span className="text-warning">已截断</span>
                              )}
                              {artifactContent.content_redacted && (
                                <span className="text-warning">已脱敏</span>
                              )}
                              {artifactContent.is_text && (
                                <a
                                  title={
                                    artifactContent.content_redacted
                                      ? "下载完整脱敏产物"
                                      : "下载完整产物"
                                  }
                                  href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(preparedRun?.task_run_id ?? "")}/artifacts/download/${artifactContent.relative_path.split("/").map((part) => encodeURIComponent(part)).join("/")}`}
                                  download={safeArtifactDownloadFilename(
                                    artifactContent.relative_path,
                                  )}
                                  className="inline-flex items-center gap-1 rounded bg-surface-container px-1.5 py-0.5 font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                                >
                                  <Download size={12} />
                                  {artifactContent.content_redacted
                                    ? "下载完整脱敏产物"
                                    : "下载完整产物"}
                                </a>
                              )}
                            </div>
                            {(() => {
                              const summary =
                                evidenceValidationSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      已接收产物:{" "}
                                      {summary.acceptedCount}
                                    </span>
                                    <span>
                                      被拒绝产物:{" "}
                                      {summary.rejectedCount}
                                    </span>
                                  </div>
                                  {summary.acceptedDetails.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                      {summary.acceptedDetails
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={`${item.sourceStepId}:${item.artifact}`}
                                          >
                                            {item.artifact} sha:
                                            {item.sha256.slice(0, 12)}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                  {summary.rejectedDetails.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                      {summary.rejectedDetails
                                        .slice(0, 3)
                                        .map((item) => (
                                          <div
                                            key={`${item.sourceStepId}:${item.artifact}:${item.reason}`}
                                          >
                                            {item.artifact || "artifact"}{" "}
                                            被拒绝:{item.reason || "unknown"}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                workflowOutputMaterializationSummary(
                                  artifactContent,
                                );
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      已固化证据:{" "}
                                      {summary.evidenceCount}
                                    </span>
                                    <span>
                                      被拒绝输出: {summary.rejectedCount}
                                    </span>
                                    <span>
                                      声明输出: {summary.outputCount}
                                    </span>
                                    {summary.auditSummary
                                      .evidenceMemoryDeclaredCount > 0 && (
                                      <span>
                                        证据记忆:
                                        {
                                          summary.auditSummary
                                            .evidenceMemoryDeclaredCount
                                        }
                                      </span>
                                    )}
                                  </div>
                                  {summary.auditOutputs.length > 0 && (
                                    <div className="mt-1 space-y-1 font-data text-[10px]">
                                      {summary.auditOutputs
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={item.outputId}
                                            className={
                                              item.materializationStatus ===
                                              "accepted"
                                                ? "text-on-surface"
                                                : item.materializationStatus ===
                                                    "partial"
                                                  ? "text-warning"
                                                  : "text-on-surface-variant"
                                            }
                                          >
                                            {item.outputId}:
                                            {item.materializationStatus ||
                                              "unknown"}
                                            {item.artifact
                                              ? ` 产物:${item.artifact}`
                                              : ""}
                                            {item.mappingKind
                                              ? ` 映射:${item.mappingKind}`
                                              : ""}
                                            {item.materializedCount
                                              ? ` 证据:${item.materializedCount}`
                                              : ""}
                                            {item.rejectedCount
                                              ? ` 被拒绝:${item.rejectedCount}`
                                              : ""}
                                            {item.rejectionReasons.length > 0
                                              ? ` 原因:${item.rejectionReasons[0]}`
                                              : ""}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                  {summary.firstRejected && (
                                    <div className="mt-1 flex flex-wrap gap-2">
                                      <span>
                                        首个拒绝项:{" "}
                                        {summary.firstRejected.output}
                                      </span>
                                      <span>
                                        原因:{summary.firstRejected.reason}
                                      </span>
                                      {summary.firstRejected.status && (
                                        <span>
                                          状态:{runStatusDisplayLabel(summary.firstRejected.status)}
                                        </span>
                                      )}
                                      {summary.firstRejected.schemaErrorCount >
                                        0 && (
                                        <span>
                                          Schema 错误:
                                          {
                                            summary.firstRejected
                                              .schemaErrorCount
                                          }
                                        </span>
                                      )}
                                    </div>
                                  )}
                                  {summary.workflowOutputsSha && (
                                    <div className="mt-1 font-data text-[10px]">
                                      工作流输出 sha:
                                      {summary.workflowOutputsSha.slice(0, 12)}
                                    </div>
                                  )}
                                  {summary.materializedEvidence.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                      {summary.materializedEvidence
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={`${item.evidenceId}:${item.kind}`}
                                          >
                                            {item.kind}:
                                            {item.subjectKey || item.evidenceId}
                                            {item.outputId
                                              ? ` 输出:${item.outputId}`
                                              : ""}
                                            {item.mappingKind
                                              ? ` 映射:${item.mappingKind}`
                                              : ""}
                                            {item.sourceStepId
                                              ? ` 步骤:${item.sourceStepId}`
                                              : ""}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                blackBoxGenerationPolicySummary(
                                  artifactContent,
                                );
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      Black-box terms: {summary.termCount}
                                    </span>
                                    <span>cases:{summary.caseCount}</span>
                                    {summary.firstCaseId && (
                                      <span>{summary.firstCaseId}</span>
                                    )}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstTerms
                                      .slice(0, 4)
                                      .map((term) => (
                                        <span key={term}>term:{term}</span>
                                      ))}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.allowedUses
                                      .slice(0, 3)
                                      .map((use) => (
                                        <span key={use}>allowed:{use}</span>
                                      ))}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                    {summary.mustNotUse
                                      .slice(0, 3)
                                      .map((use) => (
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
                              const summary =
                                memoryArtifactSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      {summary.kind === "memory_retrieval"
                                        ? "记忆检索"
                                        : "上下文包"}
                                    </span>
                                    <span>
                                      证据:{summary.evidenceCount}
                                    </span>
                                    <span>
                                      部署证据:{summary.deploymentCount}
                                    </span>
                                    <span>
                                      语义:{summary.semanticCount}
                                    </span>
                                    <span>
                                      源码片段:{summary.sourceSliceCount}
                                    </span>
                                  </div>
                                  {summary.query && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      查询:{summary.query}
                                    </div>
                                  )}
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstSubject && (
                                      <span>首项:{summary.firstSubject}</span>
                                    )}
                                    {summary.firstDeploymentSubject && (
                                      <span>
                                        部署:
                                        {summary.firstDeploymentSubject}
                                      </span>
                                    )}
                                  </div>
                                  {summary.firstReuseReason && (
                                    <div className="mt-1 break-words text-[10px]">
                                      复用原因:{summary.firstReuseReason}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                inputMaterialsSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>输入材料</span>
                                    <span>
                                      材料数:{summary.materialCount}
                                    </span>
                                    <span>
                                      必读:{String(summary.mustRead)}
                                    </span>
                                    <span>
                                      源码真相:
                                      {String(summary.materialsAreSourceTruth)}
                                    </span>
                                  </div>
                                  {summary.readOrder.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      阅读顺序:
                                      {summary.readOrder.slice(0, 6).join(",")}
                                    </div>
                                  )}
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstInputId && (
                                      <span>首项:{summary.firstInputId}</span>
                                    )}
                                    {summary.firstRole && (
                                      <span>角色:{summary.firstRole}</span>
                                    )}
                                    {summary.firstFilename && (
                                      <span>文件:{summary.firstFilename}</span>
                                    )}
                                    {summary.firstSha && (
                                      <span>
                                        sha:{summary.firstSha.slice(0, 12)}
                                      </span>
                                    )}
                                  </div>
                                  {summary.firstChunksPath && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      分片:{summary.firstChunksPath}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                failureRetryContextSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>失败重试</span>
                                    {summary.stepId && (
                                      <span>节点:{summary.stepId}</span>
                                    )}
                                    {summary.failureKind && (
                                      <span>类型:{summary.failureKind}</span>
                                    )}
                                    <span>
                                      可重试:{String(summary.retryable)}
                                    </span>
                                    {summary.exitCode && (
                                      <span>退出码:{summary.exitCode}</span>
                                    )}
                                  </div>
                                  {summary.missingArtifacts.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      缺失产物:
                                      {summary.missingArtifacts
                                        .slice(0, 6)
                                        .join(",")}
                                    </div>
                                  )}
                                  {summary.mustProduceArtifacts.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      必须生成:
                                      {summary.mustProduceArtifacts
                                        .slice(0, 6)
                                        .join(",")}
                                    </div>
                                  )}
                                  {summary.doNotRepeat.length > 0 && (
                                    <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                      {summary.doNotRepeat
                                        .slice(0, 3)
                                        .map((item) => (
                                          <span key={item}>避免重复:{item}</span>
                                        ))}
                                    </div>
                                  )}
                                  {summary.stderrExcerpt && (
                                    <div className="mt-1 break-words text-[10px]">
                                      错误输出:
                                      {summary.stderrExcerpt.slice(0, 180)}
                                    </div>
                                  )}
                                  {summary.stdoutExcerpt && (
                                    <div className="mt-1 break-words text-[10px]">
                                      标准输出:
                                      {summary.stdoutExcerpt.slice(0, 180)}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                replayPlanSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      回放状态: {runStatusDisplayLabel(summary.replayStatus)}
                                    </span>
                                    {summary.provider && (
                                      <span>执行器:{summary.provider}</span>
                                    )}
                                    {summary.turnId && (
                                      <span>轮次:{summary.turnId}</span>
                                    )}
                                    {summary.promptSource && (
                                      <span>提示词:{summary.promptSource}</span>
                                    )}
                                    {summary.promptTransport && (
                                      <span>
                                        传输:{summary.promptTransport}
                                      </span>
                                    )}
                                    {summary.timeoutSec > 0 && (
                                      <span>超时:{summary.timeoutSec}s</span>
                                    )}
                                    <span>
                                      只读:
                                      {String(summary.readonlyRequired)}
                                    </span>
                                    <span>
                                      校验输出:
                                      {String(summary.validatesOutputs)}
                                    </span>
                                    <span>哈希:{summary.hashCount}</span>
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.taskBundleSha && (
                                      <span>
                                        任务包 sha:
                                        {summary.taskBundleSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.executionInputSha && (
                                      <span>
                                        执行输入 sha:
                                        {summary.executionInputSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.contractSha && (
                                      <span>
                                        契约 sha:
                                        {summary.contractSha.slice(0, 12)}
                                      </span>
                                    )}
                                  </div>
                                  {summary.cwd && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      工作目录:{summary.cwd}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                executionInputSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>执行输入</span>
                                    {summary.provider && (
                                      <span>执行器:{summary.provider}</span>
                                    )}
                                    {summary.turnId && (
                                      <span>轮次:{summary.turnId}</span>
                                    )}
                                    {summary.promptTransport && (
                                      <span>
                                        传输:{summary.promptTransport}
                                      </span>
                                    )}
                                    {summary.promptTransportReason && (
                                      <span>
                                        原因:{summary.promptTransportReason}
                                      </span>
                                    )}
                                    {summary.timeoutSec > 0 && (
                                      <span>超时:{summary.timeoutSec}s</span>
                                    )}
                                    <span>
                                      标准输入已脱敏:
                                      {String(summary.stdinRedacted)}
                                    </span>
                                    {summary.readonlyEnv && (
                                      <span>
                                        只读环境:{summary.readonlyEnv}
                                      </span>
                                    )}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.stdinSha && (
                                      <span>
                                        stdin sha:
                                        {summary.stdinSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.outputContractSha && (
                                      <span>
                                        契约 sha:
                                        {summary.outputContractSha.slice(0, 12)}
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
                                产物内容已脱敏，内联预览已隐藏。
                              </p>
                            ) : artifactContent.is_text ? (
                              <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-container p-2 font-data text-[10px] text-on-surface">
                                {artifactContent.content}
                              </pre>
                            ) : (
                              <p className="mt-2 text-[11px] text-on-surface-variant">
                                二进制产物不在页面内直接渲染。
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  {workflowExecution && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      工作流: {runStatusDisplayLabel(workflowExecution.status)} / 步骤{" "}
                      {workflowExecution.step_results.length} / 输出{" "}
                      {workflowExecution.outputs?.length ?? 0}
                      {workflowExecution.audit_summary && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            智能体:
                            {workflowExecution.audit_summary.agent_step_count ??
                              0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            无效:
                            {workflowExecution.audit_summary.invalid_steps ?? 0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            错误:
                            {workflowExecution.audit_summary.error_steps ?? 0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            生命周期:
                            {workflowExecution.audit_summary
                              .agent_lifecycle_artifacts?.length ?? 0}
                          </span>
                          {workflowExecution.audit_summary.failure_kinds
                            ?.length ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              失败类型:
                              {workflowExecution.audit_summary.failure_kinds.join(
                                ",",
                              )}
                            </span>
                          ) : null}
                          {workflowExecution.audit_summary.missing_artifacts
                            ?.length ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              缺失产物:
                              {workflowExecution.audit_summary.missing_artifacts.join(
                                ",",
                              )}
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.rerun_plan && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            复跑:
                            {runStatusDisplayLabel(workflowExecution.rerun_plan.status ?? "")}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            复跑步骤:
                            {workflowExecution.rerun_plan.steps?.length ?? 0}
                          </span>
                          {(workflowExecution.rerun_plan.blocked_outputs
                            ?.length ?? 0) > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              阻塞输出:
                              {workflowExecution.rerun_plan.blocked_outputs
                                ?.length ?? 0}
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.evidence_materialization && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              workflowExecution.evidence_materialization
                                .status === "ok"
                                ? ""
                                : "text-warning"
                            }`}
                          >
                            证据:
                            {runStatusDisplayLabel(workflowExecution.evidence_materialization.status)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            证据项:
                            {
                              workflowExecution.evidence_materialization
                                .evidence_count
                            }
                          </span>
                          {workflowExecution.evidence_materialization
                            .rejected_outputs.length > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              被拒绝:
                              {
                                workflowExecution.evidence_materialization
                                  .rejected_outputs.length
                              }
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.semantic_output_import && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              workflowExecution.semantic_output_import
                                .status === "ok" ||
                              workflowExecution.semantic_output_import
                                .status === "skipped"
                                ? ""
                                : "text-warning"
                            }`}
                          >
                            语义:
                            {runStatusDisplayLabel(workflowExecution.semantic_output_import.status ?? "")}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            语义用例:
                            {
                              workflowExecution.semantic_output_import
                                .imported_count
                            }
                          </span>
                          {workflowExecution.semantic_output_import
                            .rejected_count > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              被拒绝:
                              {
                                workflowExecution.semantic_output_import
                                  .rejected_count
                              }
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
                            const recoveryDiagnostics =
                              recovery?.provider_diagnostics;
                            const displayedDiagnostics =
                              diagnostics ?? recoveryDiagnostics;
                            const firstAttempt =
                              recoveryDiagnostics?.attempts?.[0];
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
                                      {displayedDiagnostics.provider ||
                                        String(step.provider ?? "")}
                                    </span>
                                    <span className="ml-1">
                                      health:
                                      {displayedDiagnostics.health_status ||
                                        "unknown"}
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
                                    transport:
                                    {displayedDiagnostics.prompt_transport}
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_source && (
                                  <span className="ml-1">
                                    command:
                                    {
                                      displayedDiagnostics.command_resolution_source
                                    }
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_used_fallback && (
                                  <span className="ml-1 text-warning">
                                    fallback
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_reason && (
                                  <span className="ml-1">
                                    reason:
                                    {
                                      displayedDiagnostics.command_resolution_reason
                                    }
                                  </span>
                                )}
                                {displayedDiagnostics?.startup_probe_endpoint && (
                                  <span className="ml-1 break-all">
                                    probe:
                                    {
                                      displayedDiagnostics.startup_probe_endpoint
                                    }
                                  </span>
                                )}
                                {recovery && (
                                  <div className="mt-1 text-warning">
                                    <span>
                                      {recovery.user_message ||
                                        `诊断类型:${recovery.failure_kind || "unknown"}`}
                                    </span>
                                    {recovery.validation_status && (
                                      <span className="ml-1">
                                        校验:{recovery.validation_status}
                                      </span>
                                    )}
                                    {recovery.missing_artifacts?.length ? (
                                      <span className="ml-1">
                                        缺少:
                                        {recovery.missing_artifacts.join(",")}
                                      </span>
                                    ) : null}
                                    {(recovery.recommended_actions?.[0] ||
                                      recovery.suggested_actions?.[0]) && (
                                      <span className="ml-1">
                                        下一步:
                                        {recovery.recommended_actions?.[0] ||
                                          recovery.suggested_actions?.[0]}
                                      </span>
                                    )}
                                    {recoveryDiagnostics?.configured_command_text && (
                                      <span className="ml-1 break-all">
                                        configured:
                                        {
                                          recoveryDiagnostics.configured_command_text
                                        }
                                      </span>
                                    )}
                                    {firstAttempt && (
                                      <span className="ml-1 break-all">
                                        attempt:
                                        {firstAttempt.command ||
                                          firstAttempt.executable ||
                                          "agent"}
                                        ={firstAttempt.status || "unknown"}
                                        {firstAttempt.reason
                                          ? `:${firstAttempt.reason}`
                                          : ""}
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
                        输出证据: {runStatusDisplayLabel(workflowOutputMaterialize.status)} /{" "}
                        {workflowOutputMaterialize.evidence_count} 项
                        {workflowOutputMaterialize.rejected_outputs.length >
                          0 && (
                          <span className="ml-2 text-warning">
                            被拒绝{" "}
                            {workflowOutputMaterialize.rejected_outputs.length}
                          </span>
                        )}
                      </p>
                      {workflowOutputMaterialize.rejected_outputs.length >
                        0 && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                          {workflowOutputMaterialize.rejected_outputs
                            .slice(0, 4)
                            .map((item, index) => (
                              <div
                                key={`${rejectedOutputLabel(item)}:${index}`}
                                className="break-words"
                              >
                                {rejectedOutputLabel(item)} 被拒绝:
                                {rejectedOutputReason(item)}
                              </div>
                            ))}
                          {workflowOutputMaterialize.rejected_outputs.length >
                            4 && (
                            <div>
                              +
                              {workflowOutputMaterialize.rejected_outputs
                                .length - 4}{" "}
                              个更多
                            </div>
                          )}
                        </div>
                      )}
                      {(() => {
                        const outputs = materializationAuditOutputs(
                          workflowOutputMaterialize,
                        );
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
                                {item.outputId}:
                                {runStatusDisplayLabel(item.materializationStatus || "")}
                                {item.artifact
                                  ? ` 产物:${item.artifact}`
                                  : ""}
                                {item.materializedCount
                                  ? ` 证据:${item.materializedCount}`
                                  : ""}
                                {item.rejectedCount
                                  ? ` 被拒绝:${item.rejectedCount}`
                                  : ""}
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
                        语义导入:{" "}
                        {runStatusDisplayLabel(semanticOutputImport.status ?? "")} /{" "}
                        {semanticOutputImport.imported_count} 条
                        {semanticOutputImport.rejected_count > 0 && (
                          <span className="ml-2 text-warning">
                            被拒绝 {semanticOutputImport.rejected_count}
                          </span>
                        )}
                      </p>
                      {semanticOutputImport.source_ref && (
                        <p className="mt-1 break-words font-data text-[10px]">
                          来源:{semanticOutputImport.source_ref}
                        </p>
                      )}
                      {semanticOutputImport.rejected.length > 0 && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                          {semanticOutputImport.rejected
                            .slice(0, 4)
                            .map((item, index) => (
                              <div
                                key={`${String(item.output ?? item.case_id ?? "case")}:${index}`}
                                className="break-words"
                              >
                                {String(item.output ?? item.case_id ?? "case")}{" "}
                                被拒绝:
                                {item.reason}
                              </div>
                            ))}
                          {semanticOutputImport.rejected.length > 4 && (
                            <div>
                              +{semanticOutputImport.rejected.length - 4} 个更多
                            </div>
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
                      const isMaterializing =
                        busyAction === `materialize-${stepId}`;
                      const requiredArtifacts =
                        agentRun.required_artifacts ?? [];
                      const disableAgentActions =
                        taskRunActionBusy || agentRunActionBusy;
                      return (
                        <div
                          key={agentRun.run_id}
                          className="rounded-md border border-outline-variant/30 bg-surface-container px-2.5 py-2"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="min-w-0">
                              <p className="font-medium text-on-surface">
                                {stepId}
                              </p>
                              <p className="break-words font-data text-[11px] text-on-surface-variant">
                                {agentRun.provider} / {compactMachineToken(agentRun.run_id, 24)}
                              </p>
                            </div>
                            {!isV3PreparedRun && (
                              <button
                                onClick={() => executePreparedAgentRun(stepId)}
                                disabled={disableAgentActions}
                                className="inline-flex items-center gap-1.5 rounded bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                              >
                                {isExecuting ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  <PlayCircle size={12} />
                                )}
                                Execute
                              </button>
                            )}
                            {!isV3PreparedRun && (
                              <button
                                onClick={() =>
                                  validatePreparedAgentRun(
                                    stepId,
                                    requiredArtifacts,
                                  )
                                }
                                disabled={
                                  disableAgentActions ||
                                  requiredArtifacts.length === 0
                                }
                                className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                              >
                                {isValidating ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  <Search size={12} />
                                )}
                                Validate
                              </button>
                            )}
                            {!isV3PreparedRun && (
                              <button
                                onClick={() =>
                                  materializePreparedAgentRun(
                                    stepId,
                                    requiredArtifacts,
                                  )
                                }
                                disabled={
                                  disableAgentActions ||
                                  requiredArtifacts.length === 0
                                }
                                className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                              >
                                {isMaterializing ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  <Database size={12} />
                                )}
                                Materialize
                              </button>
                            )}
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
                                    {result.provider_diagnostics.provider ||
                                      agentRun.provider}
                                  </span>
                                  <span className="ml-1">
                                    health:
                                    {result.provider_diagnostics
                                      .health_status || "unknown"}
                                  </span>
                                  {result.provider_diagnostics
                                    .prompt_transport && (
                                    <span className="ml-1">
                                      transport:
                                      {
                                        result.provider_diagnostics
                                          .prompt_transport
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_source && (
                                    <span className="ml-1">
                                      command:
                                      {
                                        result.provider_diagnostics
                                          .command_resolution_source
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_used_fallback && (
                                    <span className="ml-1 text-warning">
                                      fallback
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_reason && (
                                    <span className="ml-1">
                                      reason:
                                      {
                                        result.provider_diagnostics
                                          .command_resolution_reason
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .startup_probe_endpoint && (
                                    <span className="ml-1 break-all">
                                      probe:
                                      {
                                        result.provider_diagnostics
                                          .startup_probe_endpoint
                                      }
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
                                  {validation.accepted_artifact_details
                                    .slice(0, 3)
                                    .map((item) => (
                                      <div
                                        key={String(
                                          item.artifact ??
                                            item.path ??
                                            item.sha256,
                                        )}
                                      >
                                        {String(item.artifact ?? "artifact")}{" "}
                                        sha:
                                        {String(item.sha256 ?? "").slice(0, 12)}
                                      </div>
                                    ))}
                                </div>
                              ) : null}
                              {validation.rejected_artifacts.length > 0 && (
                                <p className="mt-1 text-amber-400">
                                  Rejected:{" "}
                                  {validation.rejected_artifacts.length}
                                </p>
                              )}
                              {validation.rejected_artifact_details?.length ? (
                                <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                  {validation.rejected_artifact_details
                                    .slice(0, 3)
                                    .map((item) => (
                                      <div
                                        key={`${String(item.artifact ?? "artifact")}:${String(item.reason ?? "rejected")}`}
                                      >
                                        {String(item.artifact ?? "artifact")}{" "}
                                        被拒绝:
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
                                证据: {runStatusDisplayLabel(materialized.status)} /{" "}
                                {materialized.evidence_count} 项
                              </p>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                  </div>
                </details>
              )}
              {taskRuns.length > 0 && (
                <div
                  aria-label="最近任务运行"
                  className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="font-medium text-on-surface">
                      最近任务运行
                    </p>
                    <span className="rounded bg-surface-container px-1.5 py-0.5 text-[11px] text-on-surface-variant">
                      {taskRuns.length} 条
                    </span>
                  </div>
                  <div
                    aria-label="最近任务运行列表"
                    className="max-h-80 space-y-2 overflow-y-auto pr-1"
                  >
                    {taskRuns.map((run) => (
                      <button
                        key={run.task_run_id}
                        onClick={() => restoreExistingTaskRun(run.task_run_id)}
                        disabled={
                          taskRunActionBusy ||
                          busyAction === `restore-task-run-${run.task_run_id}`
                        }
                        className={`block w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-container-high disabled:opacity-50 ${
                          preparedRun?.task_run_id === run.task_run_id
                            ? "bg-surface-container-high"
                            : "bg-surface-container"
                        }`}
                      >
                        <span className="block font-medium text-on-surface">
                          {compactMachineToken(run.workflow_id, 28)}
                        </span>
                        <span className="block break-words font-data text-[11px] text-on-surface-variant">
                          {busyAction === `restore-task-run-${run.task_run_id}`
                            ? "restoring..."
                            : compactMachineToken(run.task_run_id, 24)}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            </div>
          </Panel>);
}

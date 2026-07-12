"use client";

import type { WorkbenchController } from "./workbench-controller";


export function WorkflowDesignerView({ scope }: { scope: WorkbenchController }) {
  const { ClipboardList, Copy, DEFAULT_BUILDER_SKILL_IDS, FilePlus2, Loader2, Panel, RefreshCw, RotateCcw, Save, Search, Trash2, WORKFLOW_BUILDER_SCENARIOS, WORKFLOW_CANVAS_HEIGHT, WORKFLOW_CANVAS_WIDTH, WORKFLOW_MODULE_PALETTE, WORKFLOW_NODE_ACCENT, WORKFLOW_NODE_TONE, WORKFLOW_NODE_WIDTH, X, activeWorkflowNode, activeWorkflowNodeId, addBuilderInputContract, addBuilderOutputContract, addPaletteNodeToCanvas, addPaletteNodeToCanvasViewportCenter, applyBuilderScenario, applyPreset, auditWorkflowDraft, builderArtifacts, builderEvidenceMappings, builderGoal, builderInputItems, builderInputLabels, builderInputSchemas, builderInputSpec, builderMcpCompatibility, builderMcpOptions, builderMcpProfile, builderOutputItems, builderOutputLabels, builderOutputPreview, builderOutputSchemas, builderOutputSpec, builderProvider, builderProviderOptions, builderScenario, builderSemanticImports, builderSkillIds, builderSkillQuery, builderWorkflowId, builderWorkflowName, busyAction, compactReasonLabel, connectWorkflowTargetFromPending, copyActiveWorkflowNode, createBlankWorkflowDraft, deleteActiveWorkflowNode, deleteWorkflowEdge, duplicateSelectedWorkflowDraft, endWorkflowBoardPan, endWorkflowNodeDrag, generateWorkflowDraft, groupedWorkflowPresets, moveWorkflowBoardPan, moveWorkflowNode, newWorkflowInputId, newWorkflowInputName, newWorkflowInputResolver, newWorkflowInputType, newWorkflowOutputArtifact, newWorkflowOutputId, newWorkflowOutputName, newWorkflowOutputType, paletteDragModuleRef, parseCommaSeparated, pretty, providerStatusDisplayLabel, renameActiveWorkflowNode, resetActiveWorkflowNodePosition, restoreBuiltinPresets, runStatusDisplayLabel, saveWorkflow, savedCustomWorkflows, selectWorkflowConnectionSource, selectedPresetId, selectedWorkflowId, selectedWorkflowIdRef, setActiveWorkflowNodeId, setBuilderArtifacts, setBuilderEvidenceMappings, setBuilderGoal, setBuilderInputSchemas, setBuilderInputSpec, setBuilderMcpProfile, setBuilderOutputSchemas, setBuilderOutputSpec, setBuilderProvider, setBuilderSemanticImports, setBuilderSkillIds, setBuilderSkillQuery, setBuilderWorkflowId, setBuilderWorkflowName, setNewWorkflowInputId, setNewWorkflowInputName, setNewWorkflowInputResolver, setNewWorkflowInputType, setNewWorkflowOutputArtifact, setNewWorkflowOutputId, setNewWorkflowOutputName, setNewWorkflowOutputType, setSelectedPresetId, setSelectedWorkflowId, startPalettePointerDrag, startWorkflowBoardPan, startWorkflowConnectionDrag, startWorkflowNodeDrag, uniqueWorkflowStrings, updateActiveWorkflowNodeConfig, updateWorkflowJsonDraft, visibleBuilderSkillOptions, visibleWorkflowCanvasEdges, workflowAuditWarningLabel, workflowBoardRef, workflowCanvasInnerRef, workflowCanvasNodes, workflowDisplayName, workflowDraftAuditSummary, workflowDraftEdge, workflowDraftServerAudit, workflowEdgePath, workflowEdgePoints, workflowItemLabel, workflowJson, workflowNodeConfigString, workflowPendingConnectionSourceId, workflowPresets, workflowSkillOptions, workflows } = scope;
  return (<Panel title="工作流编排" icon={<ClipboardList size={16} />}>
            <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface/82 p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                {workflowPresets.length > 0 ? (
                  <select
                    value={selectedPresetId}
                    onChange={(event) => setSelectedPresetId(event.target.value)}
                    className="min-w-0 max-w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary sm:min-w-72"
                    aria-label="工作流预设"
                  >
                    {groupedWorkflowPresets.map((group) => (
                      <optgroup key={group.group} label={group.group}>
                        {group.items.map((preset) => (
                          <option key={preset.id} value={preset.id}>
                            {workflowDisplayName(preset.definition)}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                    {savedCustomWorkflows.length > 0 && (
                      <optgroup label="已保存自定义工作流">
                        {savedCustomWorkflows.map((workflow) => (
                          <option key={workflow.id} value={`saved:${workflow.id}`}>
                            {workflowDisplayName(workflow)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                ) : (
                  <span className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
                    模板库暂未加载
                  </span>
                )}
                <button
                  onClick={applyPreset}
                  disabled={!selectedPresetId}
                  className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                >
                  从模板库导入
                </button>
                <button
                  onClick={restoreBuiltinPresets}
                  disabled={Boolean(busyAction)}
                  className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                >
                  {busyAction === "restore-builtin-presets" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <RefreshCw size={14} />
                  )}
                  刷新模板库
                </button>
              <button
                onClick={duplicateSelectedWorkflowDraft}
                disabled={
                  !workflows.some((item) => item.id === selectedWorkflowId)
                }
                className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                复制
              </button>
              <button
                onClick={createBlankWorkflowDraft}
                disabled={Boolean(busyAction)}
                className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                <FilePlus2 size={14} />
                新建空白工作流
              </button>
              <button
                onClick={saveWorkflow}
                disabled={Boolean(busyAction)}
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
                disabled={Boolean(busyAction)}
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
                  {workflowPresets.length} 个内置模板，{workflows.length} 个已保存
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-4 text-on-surface-variant">
                导入会替换当前画布草稿，不影响已保存的工作流。保存后才会出现在运行驾驶舱。
              </p>
            </div>
            <div className="ct-workflow-builder-grid grid gap-2.5 xl:grid-cols-[136px_minmax(0,1fr)]">
              <aside
                aria-label="Workflow module palette"
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface/82 p-1.5"
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold text-on-surface">
                    节点模块
                  </p>
                  <span className="font-data text-[10px] text-on-surface-variant">
                    drag
                  </span>
                </div>
                <div className="space-y-1">
                  {WORKFLOW_MODULE_PALETTE.map((paletteModule) => (
                    <button
                      key={paletteModule.id}
                      type="button"
                      draggable
                      aria-label={paletteModule.label}
                      onDragStart={(event) => {
                        paletteDragModuleRef.current = paletteModule.id;
                        event.dataTransfer.setData(
                          "application/x-codetalk-workflow-module",
                          paletteModule.id,
                        );
                        event.dataTransfer.setData("text/plain", paletteModule.id);
                        event.dataTransfer.effectAllowed = "copy";
                      }}
                      onDragEnd={() => {
                        paletteDragModuleRef.current = null;
                      }}
                      onPointerDown={(event) =>
                        startPalettePointerDrag(paletteModule.id, event)
                      }
                      onClick={() =>
                        addPaletteNodeToCanvasViewportCenter(paletteModule.id)
                      }
                      className={[
                        "w-full rounded-md border px-1.5 py-1 text-left text-[10px] font-medium leading-tight transition-colors hover:bg-surface-container-high",
                        paletteModule.tone,
                      ].join(" ")}
                    >
                      <span className="block text-[11px] font-semibold">
                        {paletteModule.label}
                      </span>
                      <span className="mt-0.5 block truncate text-[10px] opacity-75">
                        {paletteModule.id === "input"
                          ? "repo、patch、coverage"
                          : paletteModule.id === "agent"
                            ? "Claude / OpenCode / 内置"
                            : paletteModule.id === "mcp"
                              ? "远端凭证与工具"
                              : paletteModule.id === "skills"
                                ? "AGENTS.md 与技能"
                                : paletteModule.id === "gitnexus"
                                  ? "索引、切片、证据"
                                  : paletteModule.id === "cgc"
                                    ? "调用图与结构流"
                                    : "artifact / report"}
                      </span>
                    </button>
                  ))}
                </div>
              </aside>

              <section
                aria-label="Workflow canvas"
                onDragOver={(event) => event.preventDefault()}
                className="ct-workflow-canvas min-w-0 rounded-lg border border-outline-variant/30 bg-surface/72 p-2.5"
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium text-on-surface-variant">
                      工作区
                    </p>
                    <h3 className="truncate text-sm font-semibold text-on-surface">
                      {builderWorkflowName ||
                        workflowDisplayName(selectedWorkflowId)}
                    </h3>
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px] text-on-surface-variant">
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      inputs:{workflowDraftAuditSummary.inputCount}
                    </span>
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      outputs:{workflowDraftAuditSummary.outputCount}
                    </span>
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      artifacts:
                      {workflowDraftAuditSummary.requiredArtifacts.length}
                    </span>
                  </div>
                </div>
                <div
                  ref={workflowBoardRef}
                  className="ct-workflow-board max-h-[720px] overflow-auto rounded-lg border border-outline-variant/20 bg-surface-container/55"
                  onPointerDown={(event) => {
                    if (event.target === event.currentTarget) {
                      setActiveWorkflowNodeId("");
                    }
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    const moduleId =
                      event.dataTransfer.getData(
                        "application/x-codetalk-workflow-module",
                      ) ||
                      event.dataTransfer.getData("text/plain") ||
                      paletteDragModuleRef.current ||
                      "";
                    paletteDragModuleRef.current = null;
                    addPaletteNodeToCanvas(
                      moduleId,
                      event.clientX,
                      event.clientY,
                    );
                  }}
                >
                  <div
                    ref={workflowCanvasInnerRef}
                    className="relative"
                    onPointerDown={startWorkflowBoardPan}
                    onPointerMove={moveWorkflowBoardPan}
                    onPointerUp={endWorkflowBoardPan}
                    onPointerCancel={endWorkflowBoardPan}
                    style={{
                      height: WORKFLOW_CANVAS_HEIGHT,
                      width: WORKFLOW_CANVAS_WIDTH,
                    }}
                  >
                    <svg
                      className="pointer-events-none absolute inset-0 h-full w-full"
                      preserveAspectRatio="none"
                      viewBox={`0 0 ${WORKFLOW_CANVAS_WIDTH} ${WORKFLOW_CANVAS_HEIGHT}`}
                    >
                      {visibleWorkflowCanvasEdges.map((edge) => {
                        const source = workflowCanvasNodes.find(
                          (node) => node.id === edge.source,
                        );
                        const target = workflowCanvasNodes.find(
                          (node) => node.id === edge.target,
                        );
                        if (!source || !target) return null;
                        const { x1, y1, x2, y2 } = workflowEdgePoints(
                          source,
                          target,
                        );
                        return (
                          <g key={edge.id}>
                            <path
                              className="ct-workflow-link"
                              d={workflowEdgePath(x1, y1, x2, y2)}
                            />
                          </g>
                        );
                      })}
                      {workflowDraftEdge && (
                        <path
                          className="ct-workflow-link ct-workflow-link-draft"
                          d={workflowEdgePath(
                            workflowDraftEdge.x1,
                            workflowDraftEdge.y1,
                            workflowDraftEdge.x2,
                            workflowDraftEdge.y2,
                          )}
                        />
                      )}
                    </svg>
                    <div
                      className="relative z-20 h-full"
                      onPointerDown={startWorkflowBoardPan}
                      onPointerMove={moveWorkflowBoardPan}
                      onPointerUp={endWorkflowBoardPan}
                      onPointerCancel={endWorkflowBoardPan}
                    >
                      {workflowCanvasNodes.map((node, index) => (
                        <article
                          key={node.id}
                          style={{
                            left: node.x,
                            top: node.y,
                            width: WORKFLOW_NODE_WIDTH,
                          }}
                          onPointerDownCapture={(event) => {
                            const target = event.target as HTMLElement;
                            if (!target.closest(".ct-workflow-port")) {
                              setActiveWorkflowNodeId(node.id);
                            }
                          }}
                          onPointerDown={(event) =>
                            startWorkflowNodeDrag(event, node)
                          }
                          onPointerMove={moveWorkflowNode}
                          onPointerUp={endWorkflowNodeDrag}
                          onPointerCancel={endWorkflowNodeDrag}
                          onClick={() => setActiveWorkflowNodeId(node.id)}
                          tabIndex={0}
                          aria-label={`编辑节点 ${node.title}`}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setActiveWorkflowNodeId(node.id);
                            }
                          }}
                          className={[
                            "ct-workflow-node absolute h-24 cursor-move select-none rounded-md border bg-surface p-1.5 shadow-sm",
                            activeWorkflowNodeId === node.id
                              ? "ct-workflow-node-active"
                              : "hover:border-outline/35",
                            WORKFLOW_NODE_TONE[node.kind] ??
                              "border-outline-variant/30 bg-surface",
                          ].join(" ")}
                        >
                          <button
                            type="button"
                            aria-label={`连线目标 ${node.title}`}
                            data-workflow-target-node-id={node.id}
                            className={[
                              "ct-workflow-port ct-workflow-port-in",
                              workflowPendingConnectionSourceId
                                ? "ct-workflow-port-pending"
                                : "",
                            ].join(" ")}
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) =>
                              connectWorkflowTargetFromPending(event, node)
                            }
                          />
                          <button
                            type="button"
                            aria-label={`从 ${node.title} 拉出连线`}
                            className={[
                              "ct-workflow-port ct-workflow-port-out",
                              workflowPendingConnectionSourceId === node.id
                                ? "ct-workflow-port-selected"
                                : "",
                            ].join(" ")}
                            onPointerDown={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              startWorkflowConnectionDrag(event, node);
                            }}
                            onClick={(event) =>
                              selectWorkflowConnectionSource(event, node)
                            }
                          />
                          <div className="mb-1.5 flex items-start justify-between gap-1.5">
                            <div className="min-w-0">
                              <div className="mb-1 flex items-center gap-1.5">
                                <span
                                  className={[
                                    "h-2.5 w-2.5 shrink-0 rounded-[3px]",
                                    WORKFLOW_NODE_ACCENT[node.kind],
                                  ].join(" ")}
                                />
                                <p className="font-data text-[9px] uppercase text-on-surface-variant">
                                  node {index + 1}
                                </p>
                              </div>
                              <h4 className="truncate text-[11px] font-semibold text-on-surface">
                                {node.title}
                              </h4>
                              <p className="mt-0.5 truncate text-[10px] text-on-surface-variant">
                                {node.subtitle}
                              </p>
                            </div>
                            <span className="rounded bg-surface-container px-1.5 py-0.5 font-data text-[9px] text-on-surface-variant">
                              {node.kind}
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {node.body.map((line) => (
                              <p
                                key={line}
                                className="truncate rounded bg-surface/75 px-1 py-0.5 font-data text-[9px] text-on-surface-variant"
                              >
                                {line}
                              </p>
                            ))}
                          </div>
                        </article>
                      ))}
                    </div>
                    <div className="pointer-events-none absolute inset-0 z-30">
                      {visibleWorkflowCanvasEdges.map((edge) => {
                        const source = workflowCanvasNodes.find(
                          (node) => node.id === edge.source,
                        );
                        const target = workflowCanvasNodes.find(
                          (node) => node.id === edge.target,
                        );
                        if (!source || !target) return null;
                        const { x1, y1, x2, y2 } = workflowEdgePoints(
                          source,
                          target,
                        );
                        return (
                          <button
                            key={edge.id}
                            type="button"
                            aria-label={`删除连线 ${edge.label || edge.id}`}
                            className="ct-workflow-edge-delete pointer-events-auto"
                            style={{
                              left: (x1 + x2) / 2 - 11,
                              top: (y1 + y2) / 2 - 11,
                            }}
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={() => deleteWorkflowEdge(edge)}
                          >
                            <X size={11} />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </section>

              {activeWorkflowNode && (
              <aside
                aria-label="Workflow inspector"
                className="ct-workflow-inspector ct-workflow-inspector-popover min-w-0 overflow-y-auto rounded-lg border border-outline-variant/30 bg-surface/95 p-2 shadow-xl backdrop-blur [&_input]:!text-[10px] [&_select]:!text-[10px] [&_textarea]:!text-[10px]"
              >
                <div className="mb-2 flex items-start justify-between gap-2 border-b border-outline-variant/20 pb-2">
                  <div className="min-w-0">
                    <p className="font-data text-[10px] uppercase tracking-[0.12em] text-on-surface-variant">
                      属性
                    </p>
                    <h3 className="truncate text-sm font-semibold text-on-surface">
                      {activeWorkflowNode.title}
                    </h3>
                  </div>
                  <button
                    type="button"
                    aria-label="关闭属性面板"
                    onClick={() => {
                      setActiveWorkflowNodeId("");
                    }}
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-outline-variant/30 bg-surface text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
                  >
                    <X size={13} />
                  </button>
                </div>
                <div
                  data-testid="workflow-canvas-relation"
                  className="mb-2 rounded-lg border border-outline-variant/30 bg-surface-container/70 px-2 py-1.5 text-[11px] leading-4 text-on-surface-variant"
                >
                  <div className="grid gap-1 font-data">
                    <span>场景 / 字段契约 / 画布布局</span>
                    <span>场景会重置字段契约；字段契约用于生成与保存。</span>
                    <span>画布布局保存节点位置、连线和节点配置。</span>
                  </div>
                  <p className="mt-1 truncate text-on-surface">
                    当前节点:{activeWorkflowNode?.title ?? "未选中"} ·{" "}
                    {activeWorkflowNode?.source === "canvas"
                      ? "画布新增，已写入节点配置"
                      : "来自字段契约"}
                  </p>
                  <div className="mt-2 grid gap-1.5">
                    <label className="block">
                      <span className="mb-1 block text-[10px] text-on-surface-variant">
                        节点名称
                      </span>
                      <input
                        value={activeWorkflowNode?.title ?? ""}
                        onChange={(event) =>
                          renameActiveWorkflowNode(event.target.value)
                        }
                        disabled={!activeWorkflowNode}
                        aria-label="Workflow selected node title"
                        className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary disabled:opacity-50"
                      />
                    </label>
                    <div className="grid grid-cols-3 gap-1">
                      <button
                        type="button"
                        onClick={copyActiveWorkflowNode}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-outline-variant/25 bg-surface px-1.5 py-1 text-[10px] text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        <Copy size={11} />
                        复制节点
                      </button>
                      <button
                        type="button"
                        onClick={deleteActiveWorkflowNode}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-red-400/25 bg-red-400/5 px-1.5 py-1 text-[10px] text-red-600 transition-colors hover:bg-red-400/10 disabled:opacity-50"
                      >
                        <Trash2 size={11} />
                        删除节点
                      </button>
                      <button
                        type="button"
                        onClick={resetActiveWorkflowNodePosition}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-outline-variant/25 bg-surface px-1.5 py-1 text-[10px] text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        <RotateCcw size={11} />
                        重置位置
                      </button>
                    </div>
                    <div className="rounded-md border border-outline-variant/25 bg-surface px-2 py-1.5 text-[10px] leading-4 text-on-surface-variant">
                      从节点右侧圆点拖到目标节点左侧圆点即可连线；点击线中间的关闭按钮删除连线。按住画布空白处拖动可平移画布。
                    </div>
                  </div>
                </div>
                {["input", "agent", "output"].includes(activeWorkflowNode.kind) && (
                  <div
                    aria-label="Workflow node config"
                    className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2"
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-on-surface">
                        节点契约
                      </p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {activeWorkflowNode.kind}
                      </span>
                    </div>
                    <div className="grid gap-2">
                      <label className="block">
                        <span className="mb-1 block text-[10px] text-on-surface-variant">
                          契约 ID
                        </span>
                        <input
                          aria-label="Workflow node contract id"
                          value={workflowNodeConfigString("id")}
                          onChange={(event) =>
                            updateActiveWorkflowNodeConfig({
                              id: event.target.value,
                            })
                          }
                          placeholder={
                            activeWorkflowNode.kind === "agent"
                              ? "agent_step_id"
                              : activeWorkflowNode.kind === "output"
                                ? "output_id"
                                : "input_id"
                          }
                          className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                        />
                      </label>
                      {activeWorkflowNode.kind !== "agent" && (
                        <label className="block">
                          <span className="mb-1 block text-[10px] text-on-surface-variant">
                            展示名称
                          </span>
                          <input
                            aria-label="Workflow node label"
                            value={workflowNodeConfigString(
                              "label",
                              activeWorkflowNode.title,
                            )}
                            onChange={(event) =>
                              updateActiveWorkflowNodeConfig({
                                label: event.target.value,
                              })
                            }
                            className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[10px] text-on-surface outline-none focus:border-primary"
                          />
                        </label>
                      )}
                      {activeWorkflowNode.kind === "input" && (
                        <>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              输入类型
                            </span>
                            <select
                              aria-label="Workflow node input type"
                              value={workflowNodeConfigString("type", "free_text")}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  type: event.target.value,
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[10px] text-on-surface outline-none focus:border-primary"
                            >
                              <option value="directory">源码目录</option>
                              <option value="file">单个文件</option>
                              <option value="file_set">多个文件</option>
                              <option value="mr_link">MR 链接</option>
                              <option value="coverage_report">覆盖率报告</option>
                              <option value="long_text">长文本</option>
                              <option value="free_text">短文本</option>
                            </select>
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              获取方式
                            </span>
                            <select
                              aria-label="Workflow node input resolver"
                              value={workflowNodeConfigString("resolver", "manual")}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  resolver: event.target.value,
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[10px] text-on-surface outline-none focus:border-primary"
                            >
                              <option value="manual">用户填写</option>
                              <option value="agent_mcp">Agent/MCP 解析</option>
                              <option value="local">本地路径</option>
                            </select>
                          </label>
                        </>
                      )}
                      {activeWorkflowNode.kind === "agent" && (
                        <>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              执行器
                            </span>
                            <input
                              aria-label="Workflow node agent provider"
                              value={workflowNodeConfigString(
                                "provider",
                                builderProvider.trim() || "claude-code",
                              )}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  provider: event.target.value,
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              MCP profile
                            </span>
                            <input
                              aria-label="Workflow node MCP profile"
                              value={workflowNodeConfigString(
                                "mcp_profile",
                                builderMcpProfile,
                              )}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  mcp_profile: event.target.value,
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              Skills
                            </span>
                            <input
                              aria-label="Workflow node skills"
                              value={workflowNodeConfigString(
                                "skill_ids",
                                builderSkillIds.join(", "),
                              )}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  skill_ids: parseCommaSeparated(event.target.value),
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              必需产物
                            </span>
                            <input
                              aria-label="Workflow node required artifacts"
                              value={workflowNodeConfigString("required_artifacts")}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  required_artifacts: parseCommaSeparated(
                                    event.target.value,
                                  ),
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              节点目标
                            </span>
                            <textarea
                              aria-label="Workflow node agent goal"
                              value={workflowNodeConfigString("goal", builderGoal)}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  goal: event.target.value,
                                })
                              }
                              className="h-20 w-full resize-y rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                        </>
                      )}
                      {activeWorkflowNode.kind === "output" && (
                        <>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              输出类型
                            </span>
                            <select
                              aria-label="Workflow node output type"
                              value={workflowNodeConfigString("type", "json")}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  type: event.target.value,
                                })
                              }
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[10px] text-on-surface outline-none focus:border-primary"
                            >
                              <option value="json">JSON 表</option>
                              <option value="test_cases">测试用例</option>
                              <option value="scope_report">分析报告</option>
                              <option value="markdown">Markdown</option>
                            </select>
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-[10px] text-on-surface-variant">
                              产物文件
                            </span>
                            <input
                              aria-label="Workflow node artifact"
                              value={workflowNodeConfigString("artifact")}
                              onChange={(event) =>
                                updateActiveWorkflowNodeConfig({
                                  artifact: event.target.value,
                                })
                              }
                              placeholder="result.json"
                              className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                            />
                          </label>
                        </>
                      )}
                    </div>
                  </div>
                )}
                {groupedWorkflowPresets.length > 0 && (
                  <details className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                    <summary className="cursor-pointer text-xs font-semibold text-on-surface">
                      预设库
                    </summary>
                    <div className="mt-2 space-y-2">
                      {groupedWorkflowPresets.map((group) => (
                        <div key={group.group}>
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <p className="text-xs font-medium text-on-surface-variant">
                              {group.group}
                            </p>
                            <span className="font-data text-[10px] text-on-surface-variant">
                              {group.items.length}
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {group.items
                              .slice(0, group.group === "核心工作流" ? 8 : 10)
                              .map((preset) => (
                                <button
                                  key={preset.id}
                                  type="button"
                                  onClick={() => {
                                    setSelectedPresetId(preset.id);
                                    updateWorkflowJsonDraft(pretty(preset.definition));
                                    selectedWorkflowIdRef.current =
                                      preset.definition.id;
                                    setSelectedWorkflowId(preset.definition.id);
                                  }}
                                  className={[
                                    "max-w-full rounded-md border px-2 py-1 text-left text-[11px] transition-colors",
                                    selectedPresetId === preset.id
                                      ? "border-primary/40 bg-primary text-on-primary"
                                      : "border-outline-variant/30 bg-surface text-on-surface hover:bg-surface-container-high",
                                  ].join(" ")}
                                  title={preset.description}
                                >
                                  {workflowDisplayName(preset.definition)}
                                </button>
                              ))}
                            {group.items.length >
                              (group.group === "核心工作流" ? 8 : 10) && (
                              <span className="rounded-md border border-outline-variant/20 px-2 py-1 text-[11px] text-on-surface-variant">
                                +
                                {group.items.length -
                                  (group.group === "核心工作流" ? 8 : 10)}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                  <div className="mb-3 flex flex-wrap items-end gap-2">
                    <label className="min-w-0 flex-1">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        场景
                      </span>
                      <select
                        value={builderScenario}
                        onChange={(event) =>
                          applyBuilderScenario(
                            event.target
                              .value as keyof typeof WORKFLOW_BUILDER_SCENARIOS,
                          )
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder scenario"
                      >
                        {Object.entries(WORKFLOW_BUILDER_SCENARIOS).map(
                          ([id, scenario]) => (
                            <option key={id} value={id}>
                              {scenario.name}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <button
                      onClick={generateWorkflowDraft}
                      disabled={Boolean(busyAction)}
                      className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {busyAction === "generate-workflow" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <ClipboardList size={14} />
                      )}
                      生成草稿
                    </button>
                  </div>
                  <div className="grid gap-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        工作流 ID
                      </span>
                      <input
                        value={builderWorkflowId}
                        onChange={(event) =>
                          setBuilderWorkflowId(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder id"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        工作流名称
                      </span>
                      <input
                        value={builderWorkflowName}
                        onChange={(event) =>
                          setBuilderWorkflowName(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder name"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        执行器预设
                      </span>
                      <select
                        value={
                          builderProviderOptions.some(
                            (provider) => provider.id === builderProvider,
                          )
                            ? builderProvider
                            : ""
                        }
                        onChange={(event) => {
                          if (event.target.value) {
                            setBuilderProvider(event.target.value);
                          }
                        }}
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder provider preset"
                      >
                        <option value="">自定义执行器</option>
                        {builderProviderOptions.map((provider) => (
                          <option key={provider.id} value={provider.id}>
                            {provider.label} ({provider.owner}:
                            {providerStatusDisplayLabel(provider.status)})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        执行器 ID
                      </span>
                      <input
                        value={builderProvider}
                        onChange={(event) =>
                          setBuilderProvider(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder provider"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        MCP 配置
                      </span>
                      <select
                        value={builderMcpProfile}
                        onChange={(event) => {
                          setBuilderMcpProfile(event.target.value);
                        }}
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder MCP 配置"
                      >
                        <option value="">不启用 MCP</option>
                        {builderMcpOptions.map((profile) => (
                          <option key={profile} value={profile}>
                            {profile}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        自定义 MCP profile
                      </span>
                      <input
                        value={builderMcpProfile}
                        onChange={(event) =>
                          setBuilderMcpProfile(event.target.value)
                        }
                        placeholder="例如 codehub-readonly"
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder custom MCP profile"
                      />
                    </label>
                  </div>
                  <div
                    className={[
                      "mt-2 rounded-lg border px-3 py-2 text-xs",
                      builderMcpCompatibility.level === "ok"
                        ? "border-emerald-400/25 bg-emerald-400/8 text-emerald-700"
                        : builderMcpCompatibility.level === "fallback"
                          ? "border-sky-400/25 bg-sky-400/8 text-sky-700"
                          : builderMcpCompatibility.level === "warn"
                            ? "border-amber-400/30 bg-amber-400/10 text-amber-700"
                            : "border-outline-variant/30 bg-surface-container text-on-surface-variant",
                    ].join(" ")}
                    aria-label="Workflow builder MCP compatibility"
                  >
                    <div className="font-medium">
                      {builderMcpCompatibility.label}
                    </div>
                    <div className="mt-0.5">
                      {builderMcpCompatibility.detail}
                    </div>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-on-surface">
                        Skills
                      </span>
                      <button
                        type="button"
                        onClick={() => setBuilderSkillIds(DEFAULT_BUILDER_SKILL_IDS)}
                        className="rounded-md bg-surface-container px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container-high"
                      >
                        恢复默认
                      </button>
                    </div>
                    <div className="mb-2 grid gap-1.5">
                      <input
                        aria-label="Workflow builder skill search"
                        value={builderSkillQuery}
                        onChange={(event) =>
                          setBuilderSkillQuery(event.target.value)
                        }
                        placeholder="搜索 skill 名称、来源或用途"
                        className="w-full rounded-md border border-outline-variant/30 bg-surface-container px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                      />
                      <div className="flex flex-wrap gap-1.5 text-[10px] text-on-surface-variant">
                        <span className="rounded bg-surface-container px-1.5 py-0.5">
                          已选 {builderSkillIds.length}
                        </span>
                        <span
                          aria-label="Workflow builder visible skill count"
                          className="rounded bg-surface-container px-1.5 py-0.5"
                        >
                          显示 {visibleBuilderSkillOptions.length}/
                          {workflowSkillOptions.length}
                        </span>
                        {!builderSkillQuery.trim() &&
                          workflowSkillOptions.length >
                            visibleBuilderSkillOptions.length && (
                            <span className="rounded bg-surface-container px-1.5 py-0.5">
                              输入关键词查看更多
                            </span>
                          )}
                      </div>
                    </div>
                    <div
                      className="grid gap-1.5"
                      aria-label="Workflow builder skills"
                    >
                      {visibleBuilderSkillOptions.map((skill) => {
                        const checked = builderSkillIds.includes(skill.id);
                        return (
                          <label
                            key={skill.id}
                            className="flex items-start gap-2 rounded-md border border-outline-variant/20 bg-surface-container/50 px-2 py-1.5 text-xs text-on-surface-variant"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                setBuilderSkillIds((current) =>
                                  event.target.checked
                                    ? uniqueWorkflowStrings([...current, skill.id])
                                    : current.filter((id) => id !== skill.id),
                                );
                              }}
                              className="mt-0.5"
                              aria-label={`Workflow builder skill ${skill.id}`}
                            />
                            <span className="min-w-0">
                              <span className="block font-medium text-on-surface">
                                {skill.label}
                              </span>
                              {skill.description && (
                                <span className="block text-[11px] leading-snug">
                                  {skill.description}
                                </span>
                              )}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-on-surface">输入契约</p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {builderInputItems.length}
                      </span>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-1">
                      {builderInputItems.map((item) => (
                        <span
                          key={item.id}
                          className="rounded bg-surface px-1.5 py-1 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowItemLabel(builderInputLabels, item.id)}
                          <span className="text-on-surface-variant/70">
                            {" "}
                            {item.id}:{item.type}
                          </span>
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      <input
                        aria-label="New workflow input name"
                        value={newWorkflowInputName}
                        onChange={(event) =>
                          setNewWorkflowInputName(event.target.value)
                        }
                        placeholder="输入名称，如 MR 链接"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <input
                        aria-label="New workflow input id"
                        value={newWorkflowInputId}
                        onChange={(event) =>
                          setNewWorkflowInputId(event.target.value)
                        }
                        placeholder="input_id"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <select
                        aria-label="New workflow input type"
                        value={newWorkflowInputType}
                        onChange={(event) =>
                          setNewWorkflowInputType(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="directory">源码目录</option>
                        <option value="file">单个文件</option>
                        <option value="file_set">多个文件</option>
                        <option value="mr_link">MR 链接</option>
                        <option value="coverage_report">覆盖率报告</option>
                        <option value="long_text">长文本</option>
                        <option value="free_text">短文本</option>
                      </select>
                      <select
                        aria-label="New workflow input resolver"
                        value={newWorkflowInputResolver}
                        onChange={(event) =>
                          setNewWorkflowInputResolver(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="manual">用户选择/填写</option>
                        <option value="agent_mcp">Agent 通过 MCP 解析</option>
                        <option value="local">本地路径</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      onClick={addBuilderInputContract}
                      className="mt-1.5 rounded-md bg-surface px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                    >
                      添加输入契约
                    </button>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-on-surface">输出契约</p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {builderOutputItems.length}
                      </span>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-1">
                      {builderOutputItems.map((item) => (
                        <span
                          key={item.id}
                          className="rounded bg-surface px-1.5 py-1 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowItemLabel(builderOutputLabels, item.id)}
                          <span className="text-on-surface-variant/70">
                            {" "}
                            {item.artifact || item.type}
                          </span>
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      <input
                        aria-label="New workflow output name"
                        value={newWorkflowOutputName}
                        onChange={(event) =>
                          setNewWorkflowOutputName(event.target.value)
                        }
                        placeholder="输出名称，如 SFMEA 表"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <input
                        aria-label="New workflow output id"
                        value={newWorkflowOutputId}
                        onChange={(event) =>
                          setNewWorkflowOutputId(event.target.value)
                        }
                        placeholder="output_id"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <select
                        aria-label="New workflow output type"
                        value={newWorkflowOutputType}
                        onChange={(event) =>
                          setNewWorkflowOutputType(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="json">JSON 表</option>
                        <option value="test_cases">测试用例</option>
                        <option value="scope_report">分析报告</option>
                        <option value="markdown">Markdown</option>
                      </select>
                      <input
                        aria-label="New workflow output artifact"
                        value={newWorkflowOutputArtifact}
                        onChange={(event) =>
                          setNewWorkflowOutputArtifact(event.target.value)
                        }
                        placeholder="output.json"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={addBuilderOutputContract}
                      className="mt-1.5 rounded-md bg-surface px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                    >
                      添加输出契约
                    </button>
                  </div>
                  <details className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <summary className="cursor-pointer text-xs font-medium text-on-surface">
                      高级 DSL / Schema
                    </summary>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输入项，格式 id:type 或 id:type@resolver
                    </span>
                    <input
                      value={builderInputSpec}
                      onChange={(event) =>
                        setBuilderInputSpec(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder inputs"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输出项，格式 id:type 或 id:type=artifact
                    </span>
                    <input
                      value={builderOutputSpec}
                      onChange={(event) =>
                        setBuilderOutputSpec(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder outputs"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      必需产物
                    </span>
                    <input
                      value={builderArtifacts}
                      onChange={(event) =>
                        setBuilderArtifacts(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder required artifacts"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输出 Schema JSON
                    </span>
                    <textarea
                      value={builderOutputSchemas}
                      onChange={(event) =>
                        setBuilderOutputSchemas(event.target.value)
                      }
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
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
                      onChange={(event) =>
                        setBuilderEvidenceMappings(event.target.value)
                      }
                      className="h-28 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
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
                      onChange={(event) =>
                        setBuilderSemanticImports(event.target.value)
                      }
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder semantic imports"
                      spellCheck={false}
	                    />
	                  </label>
                  </details>
	                  {builderOutputPreview.length > 0 && (
                    <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5">
                      <p className="mb-1 text-xs font-medium text-on-surface-variant">
                        输出契约预览
                      </p>
                      <div className="space-y-1 font-data text-[10px] text-on-surface-variant">
                        {builderOutputPreview.map((output) => (
                          <div
                            key={output.id + ":" + output.type}
                            className="break-words rounded bg-surface-container px-1.5 py-1"
                          >
                            <span className="text-on-surface">
                              {output.id}:{output.type}
                            </span>
                            {output.artifact && (
                              <span> artifact:{output.artifact}</span>
                            )}
                            {output.schema && <span> schema</span>}
                            {output.evidenceMemory && (
                              <span>
                                {" "}
                                evidence_memory
                                {output.evidenceKind
                                  ? ":" + output.evidenceKind
                                  : ""}
                              </span>
                            )}
                            {output.semanticImport && (
                              <span> semantic_import</span>
                            )}
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
                      onChange={(event) =>
                        setBuilderInputSchemas(event.target.value)
                      }
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder input schemas"
                      spellCheck={false}
                    />
	                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      智能体目标
                    </span>
                    <textarea
                      value={builderGoal}
                      onChange={(event) => setBuilderGoal(event.target.value)}
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder goal"
                    />
                  </label>
                </div>

                <div
                  className={[
                    "mt-3 rounded-lg border px-3 py-2 text-xs",
                    workflowDraftAuditSummary.status === "invalid"
                      ? "border-red-400/20 bg-red-400/5 text-red-300"
                      : workflowDraftAuditSummary.status === "warning"
                        ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                        : "border-outline-variant/30 bg-surface-container text-on-surface-variant",
                  ].join(" ")}
                >
                  <div className="flex flex-wrap gap-2">
                    <span className="font-medium">
                      Draft:{workflowDraftAuditSummary.status}
                    </span>
                    <span>inputs:{workflowDraftAuditSummary.inputCount}</span>
                    <span>steps:{workflowDraftAuditSummary.stepCount}</span>
                    <span>
                      agent:{workflowDraftAuditSummary.agentStepCount}
                    </span>
                    <span>outputs:{workflowDraftAuditSummary.outputCount}</span>
                    <span>
                      evidence:
                      {workflowDraftAuditSummary.evidenceMemoryOutputCount}
                    </span>
                    <span>
                      semantic:
                      {workflowDraftAuditSummary.semanticImportOutputCount}
                    </span>
                    <span>
                      artifacts:
                      {workflowDraftAuditSummary.requiredArtifacts.length}
                    </span>
                  </div>
                  {workflowDraftAuditSummary.blocking.length > 0 && (
                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                      {workflowDraftAuditSummary.blocking
                        .slice(0, 3)
                        .map((item) => (
                          <div key={item}>blocking:{item}</div>
                        ))}
                    </div>
                  )}
                  {workflowDraftAuditSummary.warnings.length > 0 && (
                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                      {workflowDraftAuditSummary.warnings
                        .slice(0, 3)
                        .map((item) => (
                          <div key={item}>提醒：{compactReasonLabel(item)}</div>
                        ))}
                    </div>
                  )}
                </div>
                {workflowDraftServerAudit && (
                  <div
                    className={[
                      "mt-2 rounded-lg border px-3 py-2 text-xs",
                      workflowDraftServerAudit.valid
                        ? workflowDraftServerAudit.status === "warning"
                          ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                          : "border-outline-variant/30 bg-surface-container text-on-surface-variant"
                        : "border-red-400/20 bg-red-400/5 text-red-300",
                    ].join(" ")}
                  >
                    <div className="flex flex-wrap gap-2">
                      <span className="font-medium">
                        服务端审计：{runStatusDisplayLabel(workflowDraftServerAudit.status)}
                      </span>
                      <span>
                        {workflowDraftServerAudit.valid ? "可运行" : "需修复"}
                      </span>
                      <span>
                        提醒：{workflowDraftServerAudit.warnings.length}
                      </span>
                    </div>
                    {workflowDraftServerAudit.error && (
                      <div className="mt-1 break-words font-data text-[10px]">
                        错误：{compactReasonLabel(workflowDraftServerAudit.error)}
                      </div>
                    )}
                    {workflowDraftServerAudit.warnings.length > 0 && (
                      <div className="mt-1 space-y-0.5 font-data text-[10px]">
                        {workflowDraftServerAudit.warnings
                          .slice(0, 4)
                          .map((warning) => (
                            <div
                              key={warning.code + ":" + warning.path}
                              className="break-words"
                            >
                              {workflowAuditWarningLabel(warning)}
                            </div>
                          ))}
                      </div>
                    )}
                  </div>
                )}
                <details className="mt-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                  <summary className="cursor-pointer text-xs font-medium text-on-surface">
                    高级 Workflow JSON
                  </summary>
                  <textarea
                    value={workflowJson}
                    onChange={(event) => updateWorkflowJsonDraft(event.target.value)}
                    className="mt-2 h-64 max-h-[42vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                    aria-label="Workflow JSON"
                    spellCheck={false}
                  />
                </details>
              </aside>
              )}
            </div>
          </Panel>);
}

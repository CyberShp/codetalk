"use client";

import { useWorkbenchController } from "./workbench-controller";
import type { WorkbenchView } from "./workbench-shared";
import { DiagnosticsWorkbenchView } from "./diagnostics-view";
import { WorkflowDesignerView } from "./workflow-view";
import { RunCockpitView } from "./run-view";
import { SemanticLibraryView } from "./knowledge-view";

export function AgentWorkbenchExperience({ initialView = "run" }: { initialView?: WorkbenchView }) {
  const scope = useWorkbenchController({ initialView });
  const { Loader2, RefreshCw, WorkbenchStageFrame, activeWorkbenchView, error, loadWorkflows, loading, message, motionPreferenceReady, pageDescription, pageTitle, prefersReducedMotion, systemAudit, taskRuns, workbenchRootRef, workflowPresets, workflows, workspaces } = scope;
  return (<div
      ref={workbenchRootRef}
      data-hydrated={motionPreferenceReady ? "true" : "false"}
      aria-busy={!motionPreferenceReady}
      className={`ct-workbench-shell w-full px-4 xl:px-6 ${
        motionPreferenceReady ? "" : "pointer-events-none"
      }`}
    >
      <div className="ct-workbench-hero ct-liquid-glass mb-3 overflow-hidden rounded-xl px-3 py-2.5">
        <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-data text-[11px] uppercase tracking-[0.14em] text-primary">
                Agent Workflow
              </p>
              <h1 className="font-display text-sm font-semibold text-on-surface sm:text-base">
                {pageTitle}
              </h1>
            </div>
            <p className="mt-1 max-w-4xl text-xs leading-4 text-on-surface-variant">
              {pageDescription}
            </p>
          </div>
          <button
            onClick={() => void loadWorkflows()}
            disabled={loading}
            className="ct-liquid-button inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary disabled:opacity-50"
          >
            {loading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            刷新状态
          </button>
        </div>

        <div className="mt-2 flex flex-wrap gap-1.5">
          {[
            ["系统", systemAudit?.status ?? "待检查"],
            ["预设", `${workflowPresets.length}/${workflows.length}`],
            ["工作区", String(workspaces.length)],
            ["任务", String(taskRuns.length)],
          ].map(([label, value]) => (
            <span
              key={label}
              className="inline-flex items-center gap-1.5 rounded-md border border-outline-variant/25 bg-surface-container/75 px-2 py-0.5 text-[11px] text-on-surface-variant"
            >
              <span>{label}</span>
              <span className="font-data font-semibold text-on-surface">
                {value}
              </span>
            </span>
          ))}
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

      <WorkbenchStageFrame
        activeWorkbenchView={activeWorkbenchView}
        reducedMotion={motionPreferenceReady && Boolean(prefersReducedMotion)}
      >
        {activeWorkbenchView === "diagnostics" && <DiagnosticsWorkbenchView scope={scope} />}

        {activeWorkbenchView === "workflow" && <WorkflowDesignerView scope={scope} />}

        {activeWorkbenchView === "run" && <RunCockpitView scope={scope} />}

        {activeWorkbenchView === "knowledge" && <SemanticLibraryView scope={scope} />}
      </WorkbenchStageFrame>
    </div>);
}

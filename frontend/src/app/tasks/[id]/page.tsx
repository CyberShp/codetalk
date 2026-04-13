"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import MermaidRenderer from "@/components/ui/MermaidRenderer";
import { api } from "@/lib/api";
import type { TaskDetail } from "@/lib/types";

const detailTabs = ["documentation", "flow", "findings"] as const;
type Tab = (typeof detailTabs)[number];

export default function TaskDetailPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [tab, setTab] = useState<Tab>("documentation");
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!taskId) return;
    const load = async () => {
      try {
        const data = await api.tasks.get(taskId);
        setTask(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load task");
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [taskId]);

  if (error) {
    return (
      <div className="p-6">
        <p className="text-tertiary">{error}</p>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="p-6">
        <p className="text-on-surface-variant/50">Loading task...</p>
      </div>
    );
  }

  const docRun = task.tool_runs.find((r) => r.tool_name === "deepwiki");
  const documentation = (docRun?.result?.documentation as string) ?? "";
  const diagrams = (docRun?.result?.diagrams as Array<{ type: string; content: string }>) ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold text-on-surface">
            Task {task.id.slice(0, 8)}
          </h2>
          <p className="text-sm text-on-surface-variant mt-0.5">
            {task.task_type} &middot; {task.tools.join(", ")}
          </p>
        </div>
        <StatusBadge status={task.status as "running" | "completed" | "failed" | "pending"} />
      </div>

      <ProgressBar value={task.progress} />

      {task.error && (
        <GlassPanel className="bg-tertiary-container/20">
          <p className="text-sm text-tertiary">{task.error}</p>
        </GlassPanel>
      )}

      <div className="grid grid-cols-[1fr_320px] gap-6">
        {/* Main Content */}
        <div className="space-y-4">
          {/* Tabs */}
          <div className="flex gap-1 bg-surface-container-low rounded-lg p-1 w-fit">
            {detailTabs.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 text-xs rounded-md capitalize transition-colors ${
                  tab === t
                    ? "bg-surface-container-high text-on-surface"
                    : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          {tab === "documentation" && (
            <GlassPanel>
              {documentation ? (
                <>
                  <MarkdownRenderer content={documentation} />
                  {diagrams.length > 0 && (
                    <div className="mt-6 space-y-4">
                      <h4 className="text-xs text-on-surface-variant uppercase tracking-wider">
                        Architecture Diagrams
                      </h4>
                      {diagrams.map((d, i) => (
                        <MermaidRenderer key={i} chart={d.content} />
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-sm text-on-surface-variant/50">
                  {task.status === "running"
                    ? "Documentation is being generated..."
                    : task.status === "pending"
                      ? "Waiting to start..."
                      : "No documentation generated."}
                </p>
              )}
            </GlassPanel>
          )}

          {tab === "flow" && (
            <GlassPanel>
              <h4 className="text-xs text-on-surface-variant uppercase tracking-wider mb-4">
                Tool Execution Flow
              </h4>
              <div className="space-y-3">
                {task.tool_runs.map((run) => (
                  <div
                    key={run.id}
                    className="flex items-center gap-4 bg-surface-container-lowest/50 rounded-lg px-4 py-3"
                  >
                    <div className="w-10 h-10 rounded-md bg-surface-container-high flex items-center justify-center font-data text-xs text-primary-fixed-dim">
                      {run.tool_name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="flex-1">
                      <p className="text-sm text-on-surface font-medium">
                        {run.tool_name}
                      </p>
                      <p className="text-xs text-on-surface-variant">
                        {run.started_at
                          ? `${new Date(run.started_at).toLocaleTimeString()} \u2014 ${
                              run.completed_at
                                ? new Date(run.completed_at).toLocaleTimeString()
                                : "running"
                            }`
                          : "Queued"}
                      </p>
                      {run.error && (
                        <p className="text-xs text-tertiary mt-1">{run.error}</p>
                      )}
                    </div>
                    <StatusBadge status={run.status as "running" | "completed" | "failed" | "pending"} />
                  </div>
                ))}
                {task.tool_runs.length === 0 && (
                  <p className="text-sm text-on-surface-variant/50">
                    No tool runs yet.
                  </p>
                )}
              </div>
            </GlassPanel>
          )}

          {tab === "findings" && (
            <GlassPanel>
              <p className="text-sm text-on-surface-variant/50">
                No findings from other tools yet. Additional analysis tools
                (Zoekt, Joern, CodeCompass) are planned for future phases.
              </p>
            </GlassPanel>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <GlassPanel>
            <h4 className="text-xs text-on-surface-variant uppercase tracking-wider mb-3">
              AI Summary
            </h4>
            {task.ai_summary ? (
              <p className="text-sm text-on-surface/80 leading-relaxed">
                {task.ai_summary}
              </p>
            ) : (
              <p className="text-sm text-on-surface-variant/50">
                {task.ai_enabled
                  ? task.status === "running"
                    ? "Generating summary..."
                    : "No summary generated."
                  : "AI analysis disabled"}
              </p>
            )}
          </GlassPanel>

          <GlassPanel>
            <h4 className="text-xs text-on-surface-variant uppercase tracking-wider mb-3">
              Task Info
            </h4>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">Type</dt>
                <dd className="text-on-surface font-data text-xs">{task.task_type}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">AI</dt>
                <dd className="text-on-surface font-data text-xs">
                  {task.ai_enabled ? "Enabled" : "Disabled"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">Created</dt>
                <dd className="text-on-surface font-data text-xs">
                  {new Date(task.created_at).toLocaleString()}
                </dd>
              </div>
              {task.started_at && (
                <div className="flex justify-between">
                  <dt className="text-on-surface-variant">Started</dt>
                  <dd className="text-on-surface font-data text-xs">
                    {new Date(task.started_at).toLocaleTimeString()}
                  </dd>
                </div>
              )}
              {task.completed_at && (
                <div className="flex justify-between">
                  <dt className="text-on-surface-variant">Completed</dt>
                  <dd className="text-on-surface font-data text-xs">
                    {new Date(task.completed_at).toLocaleTimeString()}
                  </dd>
                </div>
              )}
            </dl>
          </GlassPanel>
        </div>
      </div>
    </div>
  );
}

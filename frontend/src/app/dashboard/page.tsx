"use client";

import { useState, useEffect } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import { api } from "@/lib/api";
import type { Project, AnalysisTask, ToolInfo } from "@/lib/types";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => {});
    api.tasks.list().then(setTasks).catch(() => {});
    api.tools.list().then(setTools).catch(() => {});
  }, []);

  const activeTasks = tasks.filter((t) => t.status === "running");
  const completedTasks = tasks.filter((t) => t.status === "completed");
  const healthyTools = tools.filter((t) => t.healthy);

  const stats = [
    { label: "Total Projects", value: projects.length, accent: "text-primary" },
    { label: "Active Tasks", value: activeTasks.length, accent: "text-primary-fixed-dim" },
    { label: "Completed", value: completedTasks.length, accent: "text-secondary-fixed-dim" },
    { label: "Tool Health", value: `${healthyTools.length}/${tools.length}`, accent: "text-primary" },
  ];

  return (
    <div className="space-y-6">
      <h2 className="font-display text-lg font-semibold text-on-surface">
        Dashboard
      </h2>

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-4">
        {stats.map((s) => (
          <GlassPanel key={s.label}>
            <p className="text-xs text-on-surface-variant uppercase tracking-wider">
              {s.label}
            </p>
            <p className={`font-display text-3xl font-bold mt-2 ${s.accent}`}>
              {s.value}
            </p>
          </GlassPanel>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Recent Activity */}
        <GlassPanel>
          <h3 className="font-display text-sm font-semibold text-on-surface mb-4">
            Recent Activity
          </h3>
          {tasks.length === 0 ? (
            <p className="text-sm text-on-surface-variant/50">
              No tasks yet. Create your first analysis from the Tasks page.
            </p>
          ) : (
            <div className="space-y-3">
              {tasks.slice(0, 5).map((task) => (
                <div
                  key={task.id}
                  className="flex items-center justify-between py-2"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-md bg-surface-container-high flex items-center justify-center text-xs font-data text-primary-fixed-dim">
                      {task.tools[0]?.slice(0, 2).toUpperCase() ?? "??"}
                    </div>
                    <div>
                      <p className="text-sm text-on-surface">
                        {task.repository_id.slice(0, 8)}
                      </p>
                      <p className="text-xs text-on-surface-variant">
                        {task.task_type} &middot; {task.tools.join(", ")}
                      </p>
                    </div>
                  </div>
                  <StatusBadge status={task.status as "running" | "completed" | "failed" | "pending"} />
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Active Tasks */}
        <GlassPanel>
          <h3 className="font-display text-sm font-semibold text-on-surface mb-4">
            Active Tasks
          </h3>
          {activeTasks.length === 0 ? (
            <p className="text-sm text-on-surface-variant/50">
              No active tasks
            </p>
          ) : (
            <div className="space-y-4">
              {activeTasks.map((task) => (
                <div key={task.id}>
                  <div className="flex justify-between text-sm mb-1.5">
                    <span className="text-on-surface">
                      {task.repository_id.slice(0, 8)}
                    </span>
                    <span className="font-data text-primary-fixed-dim">
                      {task.progress}%
                    </span>
                  </div>
                  <ProgressBar value={task.progress} />
                  <p className="text-xs text-on-surface-variant mt-1">
                    {task.tools.join(", ")} &middot;{" "}
                    {task.started_at
                      ? `Started ${new Date(task.started_at).toLocaleTimeString()}`
                      : "Queued"}
                  </p>
                </div>
              ))}
            </div>
          )}
        </GlassPanel>
      </div>
    </div>
  );
}

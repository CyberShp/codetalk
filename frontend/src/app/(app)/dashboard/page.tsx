"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import { usePageRestoreRefresh } from "@/hooks/usePageRestoreRefresh";
import { api } from "@/lib/api";
import type { Project, AnalysisTask, ToolInfo } from "@/lib/types";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    const results = await Promise.allSettled([
      api.projects.list().then(setProjects),
      api.tasks.list().then(setTasks),
      api.tools.list().then(setTools),
    ]);
    const failures = results.filter((r) => r.status === "rejected");
    if (failures.length === results.length) {
      setLoadError("无法连接后端服务");
    } else if (failures.length > 0) {
      setLoadError("部分数据加载失败");
    }
  }, []);

  useEffect(() => {
    Promise.allSettled([
      api.projects.list().then(setProjects),
      api.tasks.list().then(setTasks),
      api.tools.list().then(setTools),
    ]).then((results) => {
      const failures = results.filter((r) => r.status === "rejected");
      if (failures.length === results.length) {
        setLoadError("无法连接后端服务");
      } else if (failures.length > 0) {
        setLoadError("部分数据加载失败");
      }
    });
  }, []);
  usePageRestoreRefresh(() => {
    void load();
  });

  const activeTasks = tasks.filter((t) => t.status === "running");
  const completedTasks = tasks.filter((t) => t.status === "completed");
  const healthyTools = tools.filter((t) => t.healthy);

  const stats = [
    { label: "项目总数", value: projects.length, accent: "text-primary" },
    { label: "运行中任务", value: activeTasks.length, accent: "text-primary-fixed-dim" },
    { label: "已完成", value: completedTasks.length, accent: "text-secondary-fixed-dim" },
    { label: "工具健康", value: `${healthyTools.length}/${tools.length}`, accent: "text-primary" },
  ];

  return (
    <div className="space-y-6">
      <h2 className="font-display text-lg font-semibold text-on-surface">
        仪表盘
      </h2>

      {loadError && (
        <GlassPanel className="bg-tertiary-container/20 border-tertiary/30 py-3 flex items-center justify-between">
          <p className="text-sm text-tertiary">{loadError}</p>
          <button
            onClick={() => { void load(); }}
            className="px-3 py-1.5 rounded-lg border border-primary/20 bg-primary/10 text-primary text-xs font-bold uppercase tracking-widest hover:bg-primary/15 transition-colors"
          >
            重试
          </button>
        </GlassPanel>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-4">
        {stats.map((s) => (
          <GlassPanel key={s.label}>
            <p className="text-xs text-on-surface-variant">
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
            最近活动
          </h3>
          {tasks.length === 0 ? (
            <p className="text-sm text-on-surface-variant/50">
              暂无任务。前往任务页面创建您的第一个分析。
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
            运行中任务
          </h3>
          {activeTasks.length === 0 ? (
            <p className="text-sm text-on-surface-variant/50">
              暂无运行中任务
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
                      ? `开始于 ${new Date(task.started_at).toLocaleTimeString()}`
                      : "排队中"}
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

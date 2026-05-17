"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Plus,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  PlayCircle,
  Wrench,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Task, ToolInfo, TaskStatus } from "@/lib/types";

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; icon: typeof Clock; color: string; bg: string }
> = {
  pending: {
    label: "等待中",
    icon: Clock,
    color: "text-amber-400",
    bg: "bg-amber-400/10",
  },
  running: {
    label: "运行中",
    icon: PlayCircle,
    color: "text-blue-400",
    bg: "bg-blue-400/10",
  },
  completed: {
    label: "已完成",
    icon: CheckCircle2,
    color: "text-green-400",
    bg: "bg-green-400/10",
  },
  failed: {
    label: "失败",
    icon: XCircle,
    color: "text-red-400",
    bg: "bg-red-400/10",
  },
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DashboardPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const taskList = await api.tasks.list();
      setTasks(taskList);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTools = useCallback(async () => {
    setToolsLoading(true);
    try {
      const toolList = await api.tools.status();
      setTools(toolList);
    } catch {
      setTools([]);
    } finally {
      setToolsLoading(false);
    }
  }, []);

  const loadData = useCallback(() => {
    loadTasks();
    loadTools();
  }, [loadTasks, loadTools]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleDeleteTask = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.preventDefault();
      e.stopPropagation();
      const target = tasks.find((t) => t.id === id);
      if (!confirm(`确定要删除任务「${target?.name ?? id}」吗？此操作不可撤销。`)) return;
      try {
        await api.tasks.delete(id);
        await loadData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "删除失败");
      }
    },
    [loadData],
  );

  const stats = {
    total: tasks.length,
    running: tasks.filter((t) => t.status === "running").length,
    completed: tasks.filter((t) => t.status === "completed").length,
    failed: tasks.filter((t) => t.status === "failed").length,
  };

  return (
    <div className="max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            仪表盘
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            任务总览与工具状态
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            aria-label="刷新数据"
            className="flex items-center gap-2 px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors"
          >
            <RefreshCw size={14} />
            刷新
          </button>
          <Link
            href="/tasks/new"
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus size={16} />
            新建分析
          </Link>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: "总任务", value: stats.total, color: "text-on-surface" },
          { label: "运行中", value: stats.running, color: "text-blue-400" },
          { label: "已完成", value: stats.completed, color: "text-green-400" },
          { label: "失败", value: stats.failed, color: "text-red-400" },
        ].map((s) => (
          <div
            key={s.label}
            className="bg-surface-container rounded-xl px-5 py-4 border border-outline-variant/20"
          >
            <p className="text-xs text-on-surface-variant mb-1">{s.label}</p>
            <p className={`text-2xl font-display font-bold ${s.color}`}>
              {s.value}
            </p>
          </div>
        ))}
      </div>

      {/* Tool Status */}
      <div className="mb-6">
        <h2 className="text-sm font-medium text-on-surface-variant mb-3 flex items-center gap-2">
          <Wrench size={14} />
          工具状态
          {toolsLoading && <Loader2 size={12} className="animate-spin" />}
        </h2>
        {toolsLoading ? (
          <div className="grid grid-cols-2 gap-3">
            {[1, 2].map((i) => (
              <div
                key={i}
                className="h-12 bg-surface-container rounded-lg border border-outline-variant/20 animate-pulse"
              />
            ))}
          </div>
        ) : tools.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {tools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center justify-between bg-surface-container rounded-lg px-4 py-3 border border-outline-variant/20"
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-2 h-2 rounded-full ${
                      tool.healthy ? "bg-green-400" : "bg-red-400"
                    }`}
                  />
                  <span className="text-sm font-medium text-on-surface">
                    {tool.display_name}
                  </span>
                </div>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    tool.healthy
                      ? "bg-green-400/10 text-green-400"
                      : "bg-red-400/10 text-red-400"
                  }`}
                >
                  {tool.healthy ? "正常" : "离线"}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Task List */}
      <div>
        <h2 className="text-sm font-medium text-on-surface-variant mb-3">
          最近任务
        </h2>

        {loading ? (
          <div role="status" aria-live="polite" className="flex items-center justify-center py-16 text-on-surface-variant">
            <Loader2 size={20} className="animate-spin mr-2" />
            加载中...
          </div>
        ) : tasks.length === 0 ? (
          <div className="text-center py-16 bg-surface-container rounded-xl border border-outline-variant/20">
            <p className="text-on-surface-variant mb-4">
              还没有分析任务
            </p>
            <Link
              href="/tasks/new"
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
            >
              <Plus size={16} />
              创建第一个任务
            </Link>
          </div>
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => {
              const cfg = STATUS_CONFIG[task.status];
              const Icon = cfg.icon;
              return (
                <div
                  key={task.id}
                  role="link"
                  tabIndex={0}
                  aria-label={`任务: ${task.name}, 状态: ${cfg.label}`}
                  className="flex items-center gap-4 bg-surface-container hover:bg-surface-container-high rounded-xl px-5 py-4 border border-outline-variant/20 transition-colors group cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/50"
                  onClick={() => router.push(`/tasks/${task.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(`/tasks/${task.id}`);
                    }
                  }}
                >
                  <Icon size={18} className={cfg.color} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-on-surface group-hover:text-primary transition-colors truncate">
                      {task.name}
                    </p>
                    <p className="text-xs text-on-surface-variant mt-0.5 truncate">
                      {task.repo_path}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <div className="flex gap-1">
                      {task.tools.map((t) => (
                        <span
                          key={t}
                          className="text-[10px] px-1.5 py-0.5 bg-surface-container-high rounded text-on-surface-variant"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.color}`}>
                      {cfg.label}
                    </span>
                    {task.status === "running" && (
                      <span className="text-xs text-on-surface-variant tabular-nums">
                        {task.progress}%
                      </span>
                    )}
                    <span className="text-xs text-on-surface-variant/60">
                      {formatTime(task.created_at)}
                    </span>
                    <button
                      onClick={(e) => handleDeleteTask(e, task.id)}
                      aria-label={`删除任务: ${task.name}`}
                      className="p-1.5 rounded-lg text-on-surface-variant/40 hover:text-red-400 hover:bg-red-400/10 transition-colors opacity-0 group-hover:opacity-100"
                      title="删除任务"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

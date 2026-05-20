"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  PlayCircle,
  RefreshCw,
  Archive,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Task, TaskStatus } from "@/lib/types";

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; icon: typeof Clock; color: string; bg: string }
> = {
  pending: { label: "等待中", icon: Clock, color: "text-amber-400", bg: "bg-amber-400/10" },
  running: { label: "运行中", icon: PlayCircle, color: "text-blue-400", bg: "bg-blue-400/10" },
  completed: { label: "已完成", icon: CheckCircle2, color: "text-green-400", bg: "bg-green-400/10" },
  completed_with_warnings: { label: "部分完成", icon: AlertTriangle, color: "text-yellow-400", bg: "bg-yellow-400/10" },
  failed: { label: "失败", icon: XCircle, color: "text-red-400", bg: "bg-red-400/10" },
};

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTasks(await api.tasks.list());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface flex items-center gap-3">
            <Archive size={22} className="text-on-surface-variant" />
            历史任务
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            查看已完成的分析任务和报告
          </p>
        </div>
        <button
          onClick={loadTasks}
          aria-label="刷新"
          className="flex items-center gap-2 px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors"
        >
          <RefreshCw size={14} />
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-on-surface-variant">
          <Loader2 size={20} className="animate-spin mr-2" />
          加载中...
        </div>
      ) : tasks.length === 0 ? (
        <div className="text-center py-16 bg-surface-container rounded-xl border border-outline-variant/20">
          <Archive size={32} className="mx-auto text-on-surface-variant/30 mb-3" />
          <p className="text-on-surface-variant text-sm">暂无历史任务记录</p>
        </div>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => {
            const cfg = STATUS_CONFIG[task.status];
            const Icon = cfg.icon;
            return (
              <Link
                key={task.id}
                href={`/tasks/${task.id}`}
                className="flex items-center gap-4 bg-surface-container hover:bg-surface-container-high rounded-xl px-5 py-4 border border-outline-variant/20 transition-colors"
              >
                <Icon size={18} className={cfg.color} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-on-surface truncate">
                    {task.name}
                  </p>
                  <p className="text-xs text-on-surface-variant mt-0.5 truncate">
                    {task.repo_path}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
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
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

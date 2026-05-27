"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Clock,
  PlayCircle,
  FileText,
  Download,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Task, TaskStatus, TaskStep } from "@/lib/types";
import ProgressBar from "@/components/ui/ProgressBar";

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; icon: typeof Clock; color: string; bg: string }
> = {
  pending: { label: "等待中", icon: Clock, color: "text-amber-400", bg: "bg-amber-400/10" },
  running: { label: "运行中", icon: PlayCircle, color: "text-blue-400", bg: "bg-blue-400/10" },
  completed: { label: "已完成", icon: CheckCircle2, color: "text-green-400", bg: "bg-green-400/10" },
  completed_with_warnings: { label: "部分完成", icon: AlertTriangle, color: "text-yellow-400", bg: "bg-yellow-400/10" },
  failed: { label: "失败", icon: XCircle, color: "text-red-400", bg: "bg-red-400/10" },
  cancelled: { label: "已取消", icon: XCircle, color: "text-on-surface-variant", bg: "bg-surface-container-high" },
};

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;

  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<TaskStep[]>([]);
  const [cancellingTask, setCancellingTask] = useState(false);

  // Only show the full-page spinner on the very first fetch.
  const hasLoadedOnce = useRef(false);
  const stepsEndRef = useRef<HTMLDivElement>(null);

  const loadTask = useCallback(async () => {
    if (!taskId) return;
    if (!hasLoadedOnce.current) setLoading(true);
    setError(null);
    try {
      const data = await api.tasks.get(taskId);
      setTask(data);
      hasLoadedOnce.current = true;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载任务失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    loadTask();
  }, [loadTask]);

  // Auto-refresh task while running (poll at 8 s)
  useEffect(() => {
    if (task?.status !== "running") return;
    const taskTimer = setInterval(loadTask, 8000);
    return () => clearInterval(taskTimer);
  }, [task?.status, loadTask]);

  const loadSteps = useCallback(async () => {
    if (!taskId) return;
    try {
      const data = await api.tasks.steps(taskId);
      setSteps(data);
    } catch {
      // ignore step load errors
    }
  }, [taskId]);

  // Load steps on mount and poll every 5 s while running
  useEffect(() => {
    if (!taskId) return;
    loadSteps();
    if (task?.status !== "running") return;
    const timer = setInterval(loadSteps, 5000);
    return () => clearInterval(timer);
  }, [taskId, task?.status, loadSteps]);

  const handleCancel = useCallback(async () => {
    if (!taskId) return;
    setCancellingTask(true);
    try {
      await api.tasks.cancel(taskId);
      await loadTask();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "取消失败");
    } finally {
      setCancellingTask(false);
    }
  }, [taskId, loadTask]);

  // Auto-scroll steps terminal to bottom
  useEffect(() => {
    stepsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-on-surface-variant">
        <Loader2 size={20} className="animate-spin mr-2" />
        加载中...
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="max-w-2xl">
        <Link href="/" className="inline-flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-4">
          <ArrowLeft size={16} />
          返回仪表盘
        </Link>
        <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error ?? "任务不存在"}
        </div>
      </div>
    );
  }

  const cfg = STATUS_CONFIG[task.status];
  const StatusIcon = cfg.icon;

  return (
    <div className="w-full px-4 xl:px-6">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-start gap-3">
          <Link
            href="/"
            className="mt-1 p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="font-display text-2xl font-bold text-on-surface">
              {task.name}
            </h1>
            <p className="text-sm text-on-surface-variant mt-0.5 font-data">
              {task.repo_path}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {(task.status === "running" || task.status === "pending") && (
            <button
              onClick={handleCancel}
              disabled={cancellingTask}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 bg-red-400/5 rounded-lg border border-red-400/20 hover:bg-red-400/10 transition-colors disabled:opacity-50"
            >
              {cancellingTask ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <XCircle size={14} />
              )}
              {cancellingTask ? "取消中..." : "取消任务"}
            </button>
          )}
          <button
            onClick={loadTask}
            className="p-2 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
            title="刷新"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Status & Progress */}
      <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <StatusIcon size={20} className={cfg.color} />
            <span className={`text-sm font-medium ${cfg.color}`}>
              {cfg.label}
            </span>
          </div>
          <span className="text-sm text-on-surface-variant tabular-nums">
            {task.progress}%
          </span>
        </div>
        <ProgressBar value={task.progress} />

        {task.current_step && (
          <div className="mt-3 flex items-center gap-2 text-xs text-on-surface-variant">
            {task.status === "running" && (
              <Loader2 size={12} className="animate-spin shrink-0" />
            )}
            <span className="font-data">{task.current_step}</span>
          </div>
        )}

        {task.error_message && (
          <div className="mt-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400 font-data">
            {task.error_message}
          </div>
        )}
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-4">
          <p className="text-xs text-on-surface-variant mb-1">创建时间</p>
          <p className="text-sm text-on-surface font-data">
            {formatDateTime(task.created_at)}
          </p>
        </div>
        <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-4">
          <p className="text-xs text-on-surface-variant mb-1">更新时间</p>
          <p className="text-sm text-on-surface font-data">
            {formatDateTime(task.updated_at)}
          </p>
        </div>
      </div>

      {/* Tools */}
      <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-6">
        <h2 className="text-sm font-medium text-on-surface mb-3">
          分析工具
        </h2>
        <div className="flex gap-2">
          {task.tools.map((t) => (
            <span
              key={t}
              className="text-xs px-3 py-1.5 bg-primary/10 text-primary rounded-lg"
            >
              {t}
            </span>
          ))}
        </div>
      </div>

      {/* Analysis Progress Log */}
      {steps.length > 0 && (
        <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-6">
          <h2 className="text-sm font-medium text-on-surface mb-3">分析进度日志</h2>
          <div className="bg-[#0d1117] rounded-lg p-3 max-h-64 overflow-y-auto font-mono">
            {steps.map((s, i) => (
              <div key={i} className="flex items-start gap-2 text-xs leading-5 mb-0.5">
                <span className="text-on-surface-variant/40 shrink-0 tabular-nums">
                  {new Date(s.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
                </span>
                <span className="text-amber-400/70 shrink-0 tabular-nums w-8 text-right">
                  {s.progress}%
                </span>
                <span className="text-green-400/90 break-all">{s.step}</span>
              </div>
            ))}
            <div ref={stepsEndRef} />
          </div>
        </div>
      )}

      {/* Documents */}
      {(task.requirements_doc || task.design_doc) && (
        <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-6">
          <h2 className="text-sm font-medium text-on-surface mb-3">
            关联文档
          </h2>
          <div className="space-y-2">
            {task.requirements_doc && (
              <div className="flex items-center gap-2 text-sm text-on-surface-variant">
                <FileText size={14} />
                <span>需求文档 (已上传)</span>
              </div>
            )}
            {task.design_doc && (
              <div className="flex items-center gap-2 text-sm text-on-surface-variant">
                <FileText size={14} />
                <span>设计文档 (已上传)</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Action Buttons — read-only: only show report/export for completed tasks */}
      {(task.status === "completed" || task.status === "completed_with_warnings") && (
        <div className="flex gap-3">
          <Link
            href={`/tasks/${task.id}/report`}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity"
          >
            <FileText size={16} />
            查看报告
          </Link>
          <Link
            href={`/tasks/${task.id}/export`}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-surface-container-high text-on-surface font-medium rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors"
          >
            <Download size={16} />
            导出结果
          </Link>
        </div>
      )}
    </div>
  );
}

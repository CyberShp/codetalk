"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Clock,
  PlayCircle,
  FileText,
  Download,
  Loader2,
  Trash2,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Task, TaskStatus } from "@/lib/types";
import ProgressBar from "@/components/ui/ProgressBar";

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; icon: typeof Clock; color: string; bg: string }
> = {
  pending: { label: "等待中", icon: Clock, color: "text-amber-400", bg: "bg-amber-400/10" },
  running: { label: "运行中", icon: PlayCircle, color: "text-blue-400", bg: "bg-blue-400/10" },
  completed: { label: "已完成", icon: CheckCircle2, color: "text-green-400", bg: "bg-green-400/10" },
  failed: { label: "失败", icon: XCircle, color: "text-red-400", bg: "bg-red-400/10" },
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
  const [deleting, setDeleting] = useState(false);
  const [running, setRunning] = useState(false);

  const loadTask = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.tasks.get(taskId);
      setTask(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载任务失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    loadTask();
  }, [loadTask]);

  // Auto-refresh for running tasks
  useEffect(() => {
    if (task?.status !== "running") return;
    const timer = setInterval(loadTask, 5000);
    return () => clearInterval(timer);
  }, [task?.status, loadTask]);

  const handleRun = useCallback(async () => {
    if (!taskId) return;
    setRunning(true);
    try {
      await api.tasks.run(taskId);
      await loadTask();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "启动失败");
    } finally {
      setRunning(false);
    }
  }, [taskId, loadTask]);

  const handleDelete = useCallback(async () => {
    if (!taskId || !confirm("确定要删除此任务吗？")) return;
    setDeleting(true);
    try {
      await api.tasks.delete(taskId);
      window.location.href = "/";
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "删除失败");
      setDeleting(false);
    }
  }, [taskId]);

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
    <div className="max-w-4xl">
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
          <button
            onClick={loadTask}
            className="p-2 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
            title="刷新"
          >
            <RefreshCw size={16} />
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="p-2 rounded-lg text-red-400 hover:bg-red-400/10 transition-colors disabled:opacity-50"
            title="删除任务"
          >
            <Trash2 size={16} />
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

      {/* Action Buttons */}
      {(task.status === "pending" || task.status === "failed") && (
        <div className="flex gap-3">
          <button
            onClick={handleRun}
            disabled={running}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                启动中...
              </>
            ) : (
              <>
                <PlayCircle size={16} />
                开始分析
              </>
            )}
          </button>
        </div>
      )}
      {task.status === "completed" && (
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

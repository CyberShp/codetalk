"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Download,
  Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { TaskStatus } from "@/lib/types";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

export default function ReportPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;

  const [outputs, setOutputs] = useState<Record<string, string>>({});
  const [activeTab, setActiveTab] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);

  const loadOutputs = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    setError(null);
    try {
      const task = await api.tasks.get(taskId);
      setTaskStatus(task.status);
      const files = await api.tasks.output(taskId);
      const entries = await Promise.all(
        files.map((f) => api.tasks.outputFile(taskId, f.filename)),
      );
      const record: Record<string, string> = {};
      entries.forEach((e) => {
        record[e.filename] = e.content;
      });
      setOutputs(record);
      if (!activeTab && entries.length > 0) {
        setActiveTab(entries[0].filename);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载报告失败");
    } finally {
      setLoading(false);
    }
  }, [taskId, activeTab]);

  useEffect(() => {
    loadOutputs();
  }, [loadOutputs]);

  const tabKeys = Object.keys(outputs);

  const TAB_LABELS: Record<string, string> = {
    gitnexus: "GitNexus 分析",
    deepwiki: "DeepWiki 文档",
    summary: "总结",
    security: "安全分析",
    architecture: "架构分析",
  };

  function getTabLabel(key: string): string {
    return TAB_LABELS[key] ?? key;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-on-surface-variant">
        <Loader2 size={20} className="animate-spin mr-2" />
        加载报告...
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl">
        <Link
          href={`/tasks/${taskId}`}
          className="inline-flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-4"
        >
          <ArrowLeft size={16} />
          返回任务详情
        </Link>
        <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Link
            href={`/tasks/${taskId}`}
            className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="font-display text-2xl font-bold text-on-surface">
              分析报告
            </h1>
            <p className="text-sm text-on-surface-variant mt-0.5">
              查看各工具的分析结果
            </p>
          </div>
        </div>
        <Link
          href={`/tasks/${taskId}/export`}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors"
        >
          <Download size={14} />
          导出
        </Link>
      </div>

      {tabKeys.length > 1 && (
        <div className="flex gap-1 mb-6 bg-surface-container rounded-lg p-1 border border-outline-variant/20">
          {tabKeys.map((key) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`px-4 py-2 text-sm rounded-md transition-colors ${
                activeTab === key
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {getTabLabel(key)}
            </button>
          ))}
        </div>
      )}

      {tabKeys.length === 0 ? (
        <div className="text-center py-16 bg-surface-container rounded-xl border border-outline-variant/20">
          <p className="text-on-surface-variant mb-2">暂无报告数据</p>
          <p className="text-sm text-on-surface-variant/70 mb-4">
            {taskStatus === "running"
              ? "分析任务正在进行中，报告将在完成后自动显示"
              : taskStatus === "failed"
                ? "分析任务执行失败，请查看任务详情了解原因"
                : taskStatus === "completed"
                  ? "分析已完成但未生成报告，可能是因为数据不足"
                  : "任务尚未开始运行"}
          </p>
          <Link
            href={`/tasks/${taskId}`}
            className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
          >
            <ArrowLeft size={14} />
            返回任务详情
          </Link>
        </div>
      ) : (
        <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-6">
          <MarkdownRenderer content={outputs[activeTab] ?? ""} />
        </div>
      )}
    </div>
  );
}

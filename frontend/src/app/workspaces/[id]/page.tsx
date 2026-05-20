"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Loader2,
  FolderOpen,
  RefreshCw,
  FileText,
  Paperclip,
  ChevronDown,
  ChevronRight,
  BarChart2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace, WorkspaceReportMeta } from "@/lib/types";

type Tab = "reports" | "materials";

function IndexBadge({ indexed }: { indexed: number }) {
  if (indexed === 1) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-green-400/10 text-green-400">
        <CheckCircle2 size={12} />
        已索引
      </span>
    );
  }
  if (indexed === -1) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400">
        <XCircle size={12} />
        索引失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-400/10 text-amber-400">
      <Loader2 size={12} className="animate-spin" />
      索引中
    </span>
  );
}

function AnalyzeBadge({
  status,
  progress,
}: {
  status: string | null;
  progress: number;
}) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-blue-400/10 text-blue-400">
        <BarChart2 size={12} />
        报告已生成
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-400/10 text-indigo-400">
        <Loader2 size={12} className="animate-spin" />
        分析中 {progress}%
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400">
        <XCircle size={12} />
        分析失败
      </span>
    );
  }
  return null;
}

function ReportCard({ report, wsId }: { report: WorkspaceReportMeta; wsId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const LABELS: Record<string, string> = {
    module_map: "项目与模块地图",
    business_flow: "关键业务流程分析",
    source_reading: "源码定向阅读记录",
    test_design: "测试设计输入",
    requirements: "需求与设计理解",
    traceability: "需求-设计-代码追踪",
  };

  const handleToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && content === null && !loadingContent) {
      setLoadingContent(true);
      try {
        const full = await api.workspaces.report(wsId, report.id);
        setContent(full.content);
      } catch {
        setContent("（内容加载失败）");
      } finally {
        setLoadingContent(false);
      }
    }
  };

  return (
    <div className="rounded-lg border border-outline-variant/30 bg-surface-container-low overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-container transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-primary shrink-0" />
          <span className="font-medium text-sm text-on-surface">
            {LABELS[report.report_type] ?? report.report_type}
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={16} className="text-on-surface-variant" />
        ) : (
          <ChevronRight size={16} className="text-on-surface-variant" />
        )}
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t border-outline-variant/20">
          {loadingContent ? (
            <div className="flex justify-center mt-3">
              <Loader2 size={16} className="animate-spin text-primary" />
            </div>
          ) : (
            <pre className="mt-3 text-xs text-on-surface-variant whitespace-pre-wrap leading-relaxed font-mono overflow-auto max-h-[500px]">
              {content ?? "（暂无内容）"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const wsId = params.id;

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("reports");
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeProgress, setAnalyzeProgress] = useState(0);
  const [analyzeStatus, setAnalyzeStatus] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);

  const pollIndexRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAnalyzeRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hasLoadedRef = useRef(false);

  const loadWorkspace = useCallback(async () => {
    try {
      const ws = await api.workspaces.get(wsId);
      setWorkspace(ws);
      setAnalyzeStatus(ws.analyze_status);
      setAnalyzeProgress(ws.analyze_progress);
      if (!hasLoadedRef.current) {
        hasLoadedRef.current = true;
        setLoading(false);
      }
      return ws;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "加载失败");
      setLoading(false);
      return null;
    }
  }, [wsId]);

  const startIndexPoll = useCallback(
    (ws: Workspace) => {
      if (ws.indexed !== 0) return;
      if (pollIndexRef.current) return;

      pollIndexRef.current = setInterval(async () => {
        try {
          const s = await api.workspaces.indexStatus(wsId);
          if (s.indexed !== 0) {
            clearInterval(pollIndexRef.current!);
            pollIndexRef.current = null;
            await loadWorkspace();
          } else {
            setWorkspace((prev) =>
              prev ? { ...prev, indexed: s.indexed, index_job: s.index_job } : prev,
            );
          }
        } catch {
          // ignore transient poll errors
        }
      }, 3000);
    },
    [wsId, loadWorkspace],
  );

  const startAnalyzePoll = useCallback(() => {
    if (pollAnalyzeRef.current) return;

    pollAnalyzeRef.current = setInterval(async () => {
      try {
        const s = await api.workspaces.analyzeStatus(wsId);
        setAnalyzeStatus(s.analyze_status);
        setAnalyzeProgress(s.analyze_progress);

        if (s.analyze_status !== "running") {
          clearInterval(pollAnalyzeRef.current!);
          pollAnalyzeRef.current = null;
          setAnalyzing(false);
          await loadWorkspace();
        }
      } catch {
        // ignore
      }
    }, 5000);
  }, [wsId, loadWorkspace]);

  useEffect(() => {
    loadWorkspace().then((ws) => {
      if (!ws) return;
      startIndexPoll(ws);
      if (ws.analyze_status === "running") {
        setAnalyzing(true);
        startAnalyzePoll();
      }
    });

    return () => {
      if (pollIndexRef.current) clearInterval(pollIndexRef.current);
      if (pollAnalyzeRef.current) clearInterval(pollAnalyzeRef.current);
    };
  }, [wsId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAnalyze = async () => {
    if (!workspace) return;
    setAnalyzing(true);
    setAnalyzeStatus("running");
    setAnalyzeProgress(0);
    try {
      await api.workspaces.analyze(wsId);
      startAnalyzePoll();
    } catch (e: unknown) {
      setAnalyzing(false);
      setAnalyzeStatus(workspace.analyze_status);
      alert(e instanceof Error ? e.message : "启动分析失败");
    }
  };

  const handleReindex = async () => {
    if (!workspace) return;
    setReindexing(true);
    try {
      await api.workspaces.reindex(wsId);
      setWorkspace((prev) => (prev ? { ...prev, indexed: 0 } : prev));
      startIndexPoll({ ...workspace, indexed: 0 });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "重新索引失败");
    } finally {
      setReindexing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  if (error || !workspace) {
    return (
      <div className="max-w-3xl mx-auto">
        <Link
          href="/workspaces"
          className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
        >
          <ArrowLeft size={16} />
          返回工作空间列表
        </Link>
        <div className="rounded-lg bg-error/10 border border-error/20 px-4 py-3 text-sm text-error">
          {error ?? "工作空间不存在"}
        </div>
      </div>
    );
  }

  const canAnalyze = workspace.indexed === 1 && analyzeStatus !== "running";

  return (
    <div className="max-w-5xl mx-auto">
      <Link
        href="/workspaces"
        className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
      >
        <ArrowLeft size={16} />
        返回工作空间列表
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <FolderOpen size={28} className="text-primary shrink-0" />
          <div>
            <h1 className="text-2xl font-bold text-on-surface">{workspace.name}</h1>
            <p className="text-sm text-on-surface-variant mt-0.5">{workspace.repo_path}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <IndexBadge indexed={workspace.indexed} />
              <AnalyzeBadge status={analyzeStatus} progress={analyzeProgress} />
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleReindex}
            disabled={reindexing || workspace.indexed === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-outline-variant/40 text-on-surface-variant hover:bg-surface-container disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <RefreshCw size={13} className={reindexing ? "animate-spin" : ""} />
            重新索引
          </button>

          <button
            onClick={handleAnalyze}
            disabled={!canAnalyze}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-primary text-on-primary hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity font-medium"
          >
            {analyzing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <BarChart2 size={14} />
            )}
            生成报告
          </button>
        </div>
      </div>

      {/* Analysis progress bar */}
      {analyzeStatus === "running" && (
        <div className="mb-6">
          <div className="flex items-center justify-between text-xs text-on-surface-variant mb-1">
            <span>分析进度</span>
            <span>{analyzeProgress}%</span>
          </div>
          <div className="h-1.5 bg-surface-container rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-500"
              style={{ width: `${analyzeProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-outline-variant/20">
        {(["reports", "materials"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-on-surface-variant hover:text-on-surface"
            }`}
          >
            {t === "reports" ? <FileText size={14} /> : <Paperclip size={14} />}
            {t === "reports"
              ? `报告 (${workspace.reports.length})`
              : `材料 (${workspace.materials.length})`}
          </button>
        ))}
      </div>

      {/* Reports tab */}
      {tab === "reports" && (
        <div>
          {workspace.reports.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
              <FileText size={36} className="text-on-surface-variant/30" />
              <p className="text-on-surface-variant text-sm">
                {workspace.indexed === 1
                  ? "尚未生成报告，点击「生成报告」开始分析"
                  : workspace.indexed === 0
                    ? "等待索引完成后可生成报告"
                    : "索引失败，请重新索引后生成报告"}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {workspace.reports.map((report) => (
                <ReportCard key={report.id} report={report} wsId={wsId} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Materials tab */}
      {tab === "materials" && (
        <div>
          {workspace.materials.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
              <Paperclip size={36} className="text-on-surface-variant/30" />
              <p className="text-on-surface-variant text-sm">尚未上传任何材料</p>
            </div>
          ) : (
            <div className="space-y-2">
              {workspace.materials.map((mat) => (
                <div
                  key={mat.id}
                  className="flex items-center gap-3 px-4 py-3 rounded-lg border border-outline-variant/30 bg-surface-container-low"
                >
                  <Paperclip size={16} className="text-primary shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm text-on-surface truncate">{mat.filename}</p>
                    <p className="text-xs text-on-surface-variant mt-0.5">{mat.content_type}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

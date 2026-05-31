"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Upload,
  Loader2,
  Trash2,
  Play,
  ChevronDown,
  ChevronUp,
  FileText,
  AlertTriangle,
  CheckCircle2,
  BarChart3,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  CoverageAnalysis,
  CoverageDetail,
  CoverageModuleResult,
  Workspace,
} from "@/lib/types";

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function rateColor(rate: number): string {
  if (rate >= 0.8) return "text-green-400";
  if (rate >= 0.6) return "text-amber-400";
  return "text-red-400";
}

function rateBg(rate: number): string {
  if (rate >= 0.8) return "bg-green-500";
  if (rate >= 0.6) return "bg-amber-500";
  return "bg-red-500";
}

function RateBar({ rate, label }: { rate: number; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-on-surface-variant w-16 shrink-0">
        {label}
      </span>
      <div className="flex-1 h-2 bg-surface-container rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${rateBg(rate)}`}
          style={{ width: `${Math.min(rate * 100, 100)}%` }}
        />
      </div>
      <span className={`text-sm font-mono w-14 text-right ${rateColor(rate)}`}>
        {pct(rate)}
      </span>
    </div>
  );
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  parsed: { label: "已解析", color: "text-blue-400" },
  analyzing: { label: "分析中", color: "text-amber-400" },
  analyzed: { label: "已分析", color: "text-green-400" },
};

export default function CoveragePage() {
  const [analyses, setAnalyses] = useState<CoverageAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CoverageDetail | null>(null);
  const [moduleResults, setModuleResults] = useState<CoverageModuleResult[]>(
    [],
  );
  const [expandedModule, setExpandedModule] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadList = useCallback(async () => {
    try {
      const list = await api.coverage.list();
      setAnalyses(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const list = await api.workspaces.list();
      setWorkspaces(list);
    } catch {
      setWorkspaces([]);
    }
  }, []);

  useEffect(() => {
    loadList();
    loadWorkspaces();
  }, [loadList, loadWorkspaces]);

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError("");
    try {
      const files = Array.from(fileList);
      await api.coverage.upload(
        files,
        uploadName || undefined,
        selectedWorkspaceId || undefined,
      );
      setUploadName("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const handleAnalyze = async (id: string) => {
    setAnalyzing(id);
    setError("");
    try {
      const result = await api.coverage.analyze(id);
      const d = await api.coverage.get(id);
      setDetail(d);
      if (result.results?.length) {
        setModuleResults(result.results);
      } else if (d.analysis_results_json) {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } else {
        setModuleResults([]);
      }
      setExpandedId(id);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setAnalyzing(null);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.coverage.delete(id);
      if (expandedId === id) {
        setExpandedId(null);
        setDetail(null);
        setModuleResults([]);
      }
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleExpand = async (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      setModuleResults([]);
      return;
    }
    try {
      const d = await api.coverage.get(id);
      setDetail(d);
      if (d.analysis_results_json) {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } else {
        setModuleResults([]);
      }
      setExpandedId(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载详情失败");
    }
  };

  return (
    <div className="w-full px-4 xl:px-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-display font-bold text-on-surface">
          精准测试覆盖率分析
        </h1>
        <p className="text-sm text-on-surface-variant mt-1">
          上传覆盖率报告（XML/HTML/CSV/TSV/TXT），AI 分析未覆盖代码并推荐测试用例
        </p>
      </div>

      {/* Upload section */}
      <div className="bg-surface-container-low rounded-xl p-5 border border-outline-variant/20">
        <h2 className="text-sm font-medium text-on-surface mb-3 flex items-center gap-2">
          <Upload size={16} />
          上传覆盖率报告
        </h2>
        <div className="space-y-3">
          <input
            type="text"
            placeholder="分析名称（可选）"
            value={uploadName}
            onChange={(e) => setUploadName(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-surface-container border border-outline-variant/30 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <select
            value={selectedWorkspaceId}
            onChange={(e) => setSelectedWorkspaceId(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-surface-container border border-outline-variant/30 text-sm text-on-surface focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">No workspace binding</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name} - {ws.repo_path}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".xml,.html,.htm,.csv,.tsv,.txt"
              onChange={(e) => handleUpload(e.target.files)}
              className="flex-1 text-sm text-on-surface-variant file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-primary/10 file:text-primary file:text-sm file:font-medium file:cursor-pointer hover:file:bg-primary/20"
              disabled={uploading}
            />
            {uploading && (
              <Loader2 size={18} className="animate-spin text-primary" />
            )}
          </div>
          <p className="text-xs text-on-surface-variant/60">
            支持 Cobertura XML、JaCoCo XML、HTML 覆盖率报告，以及内网函数命中表（CSV/TSV/TXT）。可多文件上传（按模块目录分类）
          </p>
        </div>

        {/* Reserved: intranet API */}
        <div className="mt-4 pt-4 border-t border-outline-variant/15">
          <div className="flex items-center gap-2 text-xs text-on-surface-variant/50">
            <AlertTriangle size={14} />
            <span>
              内网精准测试工具 API 对接（预留） — 待工具方提供接口规范后启用
            </span>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-error-container/20 text-error rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Analysis list */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 size={24} className="animate-spin text-primary" />
        </div>
      ) : analyses.length === 0 ? (
        <div className="text-center py-16 text-on-surface-variant">
          <BarChart3 size={48} className="mx-auto mb-4 opacity-30" />
          <p className="text-lg">暂无覆盖率分析</p>
          <p className="text-sm mt-1">上传覆盖率报告文件开始分析</p>
        </div>
      ) : (
        <div className="space-y-3">
          {analyses.map((a) => (
            <div
              key={a.id}
              className="bg-surface-container-low rounded-xl border border-outline-variant/20 overflow-hidden"
            >
              {/* Summary row */}
              <div className="p-4 flex items-center gap-4">
                <button
                  onClick={() => handleExpand(a.id)}
                  className="flex-1 flex items-center gap-4 text-left"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <FileText size={16} className="text-primary shrink-0" />
                      <span className="font-medium text-on-surface truncate">
                        {a.name}
                      </span>
                      <span
                        className={`text-xs ${STATUS_MAP[a.status]?.color ?? "text-on-surface-variant"}`}
                      >
                        {STATUS_MAP[a.status]?.label ?? a.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 mt-1 text-xs text-on-surface-variant">
                      <span>{a.module_count} 个模块</span>
                      <span>格式: {a.source_format}</span>
                      {a.workspace_id && (
                        <span>workspace: {a.workspace_id.slice(0, 8)}</span>
                      )}
                      <span>
                        {new Date(a.created_at).toLocaleString("zh-CN")}
                      </span>
                    </div>
                  </div>

                  {/* Mini rates */}
                  <div className="flex items-center gap-4 shrink-0">
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_line_rate)}`}
                      >
                        {pct(a.overall_line_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        行
                      </div>
                    </div>
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_branch_rate)}`}
                      >
                        {pct(a.overall_branch_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        分支
                      </div>
                    </div>
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_function_rate)}`}
                      >
                        {pct(a.overall_function_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        函数
                      </div>
                    </div>
                  </div>

                  {expandedId === a.id ? (
                    <ChevronUp size={16} className="text-on-surface-variant" />
                  ) : (
                    <ChevronDown
                      size={16}
                      className="text-on-surface-variant"
                    />
                  )}
                </button>

                {/* Actions */}
                <div className="flex items-center gap-2 shrink-0">
                  {a.status === "parsed" && (
                    <button
                      onClick={() => handleAnalyze(a.id)}
                      disabled={analyzing === a.id}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-sm hover:bg-primary/20 disabled:opacity-50"
                    >
                      {analyzing === a.id ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Play size={14} />
                      )}
                      AI 分析
                    </button>
                  )}
                  {a.status === "analyzed" && (
                    <button
                      onClick={() => handleAnalyze(a.id)}
                      disabled={analyzing === a.id}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-container text-on-surface-variant text-sm hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {analyzing === a.id ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Play size={14} />
                      )}
                      重新分析
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(a.id)}
                    className="p-1.5 rounded-lg text-on-surface-variant hover:bg-error-container/20 hover:text-error"
                    aria-label="删除"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Expanded detail */}
              {expandedId === a.id && detail && (
                <div className="border-t border-outline-variant/15 p-4 space-y-4">
                  {/* Overall rates */}
                  <div className="space-y-2">
                    <RateBar
                      rate={detail.overall_line_rate}
                      label="行覆盖"
                    />
                    <RateBar
                      rate={detail.overall_branch_rate}
                      label="分支覆盖"
                    />
                    <RateBar
                      rate={detail.overall_function_rate}
                      label="函数覆盖"
                    />
                  </div>

                  {/* Module results */}
                  {moduleResults.length > 0 && (
                    <div className="space-y-2">
                      <h3 className="text-sm font-medium text-on-surface flex items-center gap-2">
                        <CheckCircle2 size={14} className="text-green-400" />
                        AI 分析结果
                      </h3>
                      {moduleResults.map((mr) => {
                        const resultId = [
                          mr.module_path,
                          mr.function_name ?? "",
                          mr.file_path ?? "",
                          mr.line_start ?? "",
                        ].join(":");
                        return (
                        <div
                          key={resultId}
                          className="bg-surface-container rounded-lg overflow-hidden"
                        >
                          <button
                            onClick={() =>
                              setExpandedModule(
                                expandedModule === resultId
                                  ? null
                                  : resultId,
                              )
                            }
                            className="w-full px-4 py-3 flex items-center justify-between text-left"
                          >
                            <div>
                              <span className="text-sm font-mono text-on-surface">
                                {mr.function_name ?? mr.module_path}
                              </span>
                              {mr.function_name && (
                                <div className="mt-1 text-xs text-on-surface-variant">
                                  {mr.file_path}
                                  {mr.line_start ? `:${mr.line_start}` : ""}
                                  {mr.risk_level ? ` · risk ${mr.risk_level}` : ""}
                                  {mr.confidence ? ` · confidence ${mr.confidence}` : ""}
                                </div>
                              )}
                              <div className="flex items-center gap-3 mt-1 text-xs">
                                <span className={rateColor(mr.line_rate)}>
                                  行 {pct(mr.line_rate)}
                                </span>
                                <span className={rateColor(mr.branch_rate)}>
                                  分支 {pct(mr.branch_rate)}
                                </span>
                                <span className={rateColor(mr.function_rate)}>
                                  函数 {pct(mr.function_rate)}
                                </span>
                              </div>
                            </div>
                            {expandedModule === resultId ? (
                              <ChevronUp
                                size={14}
                                className="text-on-surface-variant"
                              />
                            ) : (
                              <ChevronDown
                                size={14}
                                className="text-on-surface-variant"
                              />
                            )}
                          </button>
                          {expandedModule === resultId && (
                            <div className="px-4 pb-4 border-t border-outline-variant/10">
                              {mr.error ? (
                                <p className="text-sm text-error mt-2">
                                  {mr.error}
                                </p>
                              ) : mr.analysis ? (
                                <div className="mt-3 prose prose-invert prose-sm max-w-none text-on-surface-variant leading-relaxed whitespace-pre-wrap">
                                  {mr.analysis}
                                </div>
                              ) : (
                                <p className="text-sm text-on-surface-variant mt-2">
                                  暂无分析结果
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                        );
                      })}
                    </div>
                  )}

                  {/* No results yet hint */}
                  {moduleResults.length === 0 &&
                    detail.status === "parsed" && (
                      <div className="text-center py-6 text-on-surface-variant text-sm">
                        覆盖率数据已解析，点击「AI 分析」获取测试建议
                      </div>
                    )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

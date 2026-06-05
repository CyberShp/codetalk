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
  GitBranch,
  FlaskConical,
  LogIn,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  CoverageAnalysis,
  CoverageDetail,
  CoverageModuleResult,
  CoverageTestScenario,
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

const ENTRY_KIND_LABEL: Record<string, string> = {
  cli: "CLI",
  api: "API",
  message: "消息",
  config: "配置",
  file: "文件",
  callback: "注册回调",
  timer: "定时任务",
  service: "服务入口",
  unknown: "未知",
};

function entryKindLabel(kind: string): string {
  return ENTRY_KIND_LABEL[kind] ?? kind;
}

function providerLabel(tool?: string): string {
  if (!tool) return "";
  if (tool === "claude-code") return "claude-code";
  if (tool === "opencode") return "opencode";
  if (tool === "source-registration") return "source-registration";
  return tool;
}

const ENTRY_TRACE_STATUS_LABEL: Record<string, string> = {
  entry_found: "已确认外部入口",
  source_read_ok_entry_not_found: "源码已读，入口仍需确认",
  source_not_found: "未读到源码窗口",
  workspace_not_bound: "未绑定工作区",
  trace_skipped_by_cap: "超过追踪上限",
  tool_unavailable: "工具不可用",
};

function entryTraceStatusLabel(status?: string): string {
  if (!status) return "入口发现状态未知";
  return ENTRY_TRACE_STATUS_LABEL[status] ?? status;
}

const CASE_TYPE_LABEL: Record<string, string> = {
  black_box_ready: "黑盒可执行",
  black_box_hypothesis: "黑盒假设",
  gray_box_required: "需要灰盒辅助",
};

const LEVEL_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function levelLabel(level?: string): string {
  if (!level) return "";
  return LEVEL_LABEL[level] ?? level;
}

function caseTypeLabel(type?: string): string {
  if (!type) return "";
  return CASE_TYPE_LABEL[type] ?? type;
}

function TestScenarioCard({ scenario }: { scenario: CoverageTestScenario }) {
  return (
    <div className="rounded-lg bg-surface-container-high/70 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs px-2 py-0.5 rounded-full bg-primary/15 text-primary">
          {caseTypeLabel(scenario.case_type)}
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-surface-container text-on-surface-variant">
          优先级：{levelLabel(scenario.priority)}
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-surface-container text-on-surface-variant">
          置信度：{levelLabel(scenario.confidence)}
        </span>
      </div>
      <div className="text-sm font-medium text-on-surface">
        {scenario.flow_purpose}
      </div>
      <div className="grid gap-2 sm:grid-cols-2 text-[11px] text-on-surface-variant">
        <div><span className="text-on-surface">外部触发：</span>{scenario.external_trigger}</div>
        <div><span className="text-on-surface">输入构造：</span>{scenario.input_construction}</div>
        <div><span className="text-on-surface">正常路径：</span>{scenario.normal_path}</div>
        <div><span className="text-on-surface">异常路径：</span>{scenario.error_path}</div>
        <div><span className="text-on-surface">预期结果：</span>{scenario.expected_result}</div>
        <div>
          <span className="text-on-surface">可观测信号：</span>
          {(scenario.observable_signals ?? []).join("、") || "待补充"}
        </div>
      </div>
      {scenario.gray_box_aid && (
        <div className="text-[11px] text-amber-300/90">
          灰盒辅助：{scenario.gray_box_aid}
        </div>
      )}
      {scenario.sfmea && (
        <div className="text-[11px] text-on-surface-variant/90 border-t border-outline-variant/10 pt-2">
          <span className="text-on-surface">SFMEA：</span>
          故障模式 {scenario.sfmea.failure_mode || "待确认"}；
          触发条件 {scenario.sfmea.trigger_condition || "待确认"}；
          传播影响 {scenario.sfmea.propagation_effect || "待确认"}；
          可观测现象 {scenario.sfmea.observable_effect || "待确认"}；
          推荐测试 {scenario.sfmea.recommended_test || "待确认"}
        </div>
      )}
      {(scenario.evidence_refs?.length || scenario.verification_gaps?.length) ? (
        <div className="text-[10px] text-on-surface-variant/70">
          {scenario.evidence_refs?.length ? `证据：${scenario.evidence_refs.join("、")}` : ""}
          {scenario.verification_gaps?.length ? ` 待确认：${scenario.verification_gaps.join("、")}` : ""}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Structured render of the coverage-test-design-v1 enrichment for one uncovered
 * function: trigger conditions, external entry paths, black-box cases, gray-box
 * scheme, and the evidence / pending-verification gaps.
 */
function GapDesignDetail({ mr }: { mr: CoverageModuleResult }) {
  const triggers = mr.trigger_branches ?? [];
  const entries = mr.entry_paths ?? [];
  const scenarios = mr.test_scenarios ?? [];
  const aiAttempted =
    Boolean(mr.ai_generation_status) && mr.ai_generation_status !== "skipped";
  const cases = mr.black_box_cases ?? [];
  const gaps = mr.evidence_gaps ?? [];
  const sw = mr.source_window ?? null;
  const grayRequired = mr.gray_box_required ?? false;
  const discovery = mr.entry_discovery ?? null;
  const discoveryCandidates = discovery?.candidate_external_entries ?? [];
  const discoveryReasons = discovery?.unresolved_reasons ?? [];
  const hasBlackBoxEntry =
    entries.length > 0 ||
    discoveryCandidates.length > 0 ||
    scenarios.some((scenario) => scenario.case_type === "black_box_ready");

  const hasDesign =
    triggers.length > 0 ||
    entries.length > 0 ||
    discoveryCandidates.length > 0 ||
    discoveryReasons.length > 0 ||
    cases.length > 0 ||
    scenarios.length > 0 ||
    gaps.length > 0 ||
    grayRequired;
  if (!hasDesign) return null;

  return (
    <div className="mt-3 space-y-3">
      {/* Status badges */}
      <div className="flex flex-wrap items-center gap-2">
        {grayRequired ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 text-xs">
            <FlaskConical size={12} /> 需要灰盒辅助
          </span>
        ) : hasBlackBoxEntry ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/15 text-green-300 text-xs">
            <LogIn size={12} /> 黑盒可触达
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant text-xs">
            <Search size={12} /> 入口待确认
          </span>
        )}
        {sw?.available && (
          <span className="text-xs text-on-surface-variant/70">
            源码 {sw.path}
            {sw.start ? `:${sw.start}-${sw.end}` : ""}
          </span>
        )}
      </div>

      {discovery && (
        <div className="rounded-lg bg-surface-container-high/50 p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-on-surface">入口发现</span>
            <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">
              {entryTraceStatusLabel(discovery.entry_trace_status ?? mr.entry_trace_status)}
            </span>
            {discovery.source_verification_status && (
              <span className="text-on-surface-variant/70">
                {discovery.source_verification_status}
              </span>
            )}
          </div>
          {discoveryCandidates.length > 0 && (
            <ul className="space-y-1">
              {discoveryCandidates.slice(0, 4).map((candidate, i) => (
                <li
                  key={`disc-${i}-${candidate.entry_symbol ?? ""}`}
                  className="text-xs text-on-surface-variant"
                >
                  <span className="px-1.5 py-0.5 rounded bg-green-500/10 text-green-300 text-[10px] mr-1">
                    {entryKindLabel(candidate.entry_type)}
                  </span>
                  <span className="text-on-surface">
                    {candidate.entry_label ?? candidate.entry_symbol ?? "外部入口候选"}
                  </span>
                  {candidate.confidence && (
                    <span className="opacity-60"> · {candidate.confidence}</span>
                  )}
                  {candidate.tool && (
                    <span className="opacity-60"> via {providerLabel(candidate.tool)}</span>
                  )}
                  {candidate.validation_error && (
                    <span className="opacity-60"> {candidate.validation_error}</span>
                  )}
                  {candidate.evidence && (
                    <div className="opacity-60 mt-0.5 font-mono">{candidate.evidence}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {discoveryReasons.length > 0 && (
            <ul className="space-y-1">
              {discoveryReasons.slice(0, 3).map((reason, i) => (
                <li key={`disc-reason-${i}`} className="text-xs text-amber-200/90">
                  {reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Trigger conditions / branches */}
      {triggers.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1 flex items-center gap-1">
            <GitBranch size={12} /> 触发条件 / 分支
          </div>
          <ul className="space-y-1">
            {triggers.map((b, i) => (
              <li
                key={`trig-${i}-${b.file ?? ""}-${b.line_number ?? ""}`}
                className="text-xs text-on-surface-variant"
              >
                <span className="px-1.5 py-0.5 rounded bg-surface-container-high text-[10px] mr-1">
                  {b.source === "caller" ? "调用点守卫" : "函数内"}
                </span>
                <code className="text-on-surface">{b.condition}</code>
                {b.file && (
                  <span className="opacity-60">
                    {" "}
                    · {b.file}
                    {b.line_number ? `:${b.line_number}` : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* External entry paths (entry-oriented tracing) */}
      {entries.length > 0 ? (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">
            外部入口路径（入口导向分层追踪）
          </div>
          <ul className="space-y-1">
            {entries.map((e, i) => (
              <li
                key={`entry-${i}-${e.entry_symbol ?? ""}`}
                className="text-xs text-on-surface-variant"
              >
                <span className="px-1.5 py-0.5 rounded bg-primary/15 text-primary text-[10px] mr-1">
                  {entryKindLabel(e.entry_kind)}
                </span>
                {e.tool && (
                  <span className="px-1.5 py-0.5 rounded bg-surface-container-high text-[10px] mr-1">
                    {providerLabel(e.tool)}
                  </span>
                )}
                <span className="font-mono text-on-surface">
                  {(e.chain ?? []).join(" → ")}
                </span>
                {e.evidence && (
                  <div className="opacity-60 mt-0.5 font-mono">{e.evidence}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : grayRequired && mr.gray_box ? (
        <div className="text-xs text-amber-300/90">
          确定性追踪未确认外部入口，入口发现仍需验证；必要时使用灰盒辅助方案：{mr.gray_box.scheme}
          {mr.gray_box.injection_points && mr.gray_box.injection_points.length > 0 && (
            <div className="opacity-70 mt-0.5">
              注入点：{mr.gray_box.injection_points.join("；")}
            </div>
          )}
        </div>
      ) : null}

      {scenarios.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">AI 生成的测试场景</div>
          <div className="space-y-2">
            {scenarios.map((scenario) => (
              <TestScenarioCard key={scenario.scenario_id} scenario={scenario} />
            ))}
          </div>
        </div>
      )}

      {aiAttempted && scenarios.length === 0 && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-500/10 p-3 text-xs text-amber-200">
          AI 没有生成通过结构校验和反白盒校验的推荐用例。当前只展示覆盖率证据、源码窗口和缺口原因，不把确定性模板草稿伪装成可执行用例。
        </div>
      )}

      {/* Black-box / gray-box test cases */}
      {cases.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">测试用例</div>
          <div className="space-y-2">
            {cases.map((c, i) => (
              <div
                key={`case-${i}-${c.title}`}
                className="rounded-lg bg-surface-container-high/60 p-2 space-y-0.5"
              >
                <div className="text-xs font-medium text-on-surface">
                  {c.title}
                </div>
                {c.preconditions && (
                  <div className="text-[11px] text-on-surface-variant">
                    前置：{c.preconditions}
                  </div>
                )}
                {c.inputs && (
                  <div className="text-[11px] text-on-surface-variant">
                    输入：{c.inputs}
                  </div>
                )}
                {c.steps && c.steps.length > 0 && (
                  <div className="text-[11px] text-on-surface-variant">
                    步骤：{c.steps.join(" → ")}
                  </div>
                )}
                {c.expected && (
                  <div className="text-[11px] text-on-surface-variant">
                    预期：{c.expected}
                  </div>
                )}
                {c.observable_signals && c.observable_signals.length > 0 && (
                  <div className="text-[11px] text-on-surface-variant/80">
                    可观测：{c.observable_signals.join("、")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evidence / pending-verification gaps */}
      {gaps.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1 flex items-center gap-1">
            <AlertTriangle size={12} className="text-amber-400" /> 待验证 / 证据缺口
          </div>
          <ul className="list-disc list-inside space-y-0.5">
            {gaps.map((g) => (
              <li key={g} className="text-[11px] text-on-surface-variant">
                {g}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tool availability */}
      {mr.tool_status && (
        <div className="text-[10px] text-on-surface-variant/60">
          工具：
          {Object.entries(mr.tool_status)
            .map(([k, v]) => `${k}=${v}`)
            .join(" · ")}
        </div>
      )}
    </div>
  );
}

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
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const autoExpandedRef = useRef(false);

  const applyDetail = useCallback((id: string, d: CoverageDetail) => {
    setDetail(d);
    if (d.analysis_results_json) {
      try {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } catch {
        setModuleResults([]);
      }
    } else {
      setModuleResults([]);
    }
    setExpandedId(id);
  }, []);

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

  useEffect(() => {
    if (!selectedWorkspaceId && workspaces.length === 1) {
      setSelectedWorkspaceId(workspaces[0].id);
    }
  }, [selectedWorkspaceId, workspaces]);

  useEffect(() => {
    if (autoExpandedRef.current || expandedId || analyses.length === 0) {
      return;
    }
    const latest =
      analyses.find((item) => item.status === "analyzed") ?? analyses[0];
    if (!latest) return;
    autoExpandedRef.current = true;
    api.coverage
      .get(latest.id)
      .then((d) => applyDetail(latest.id, d))
      .catch(() => {
        autoExpandedRef.current = false;
      });
  }, [analyses, applyDetail, expandedId]);

  const handleFileSelect = (fileList: FileList | null) => {
    setSelectedFiles(fileList ? Array.from(fileList) : []);
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError("请先选择覆盖率文件");
      return;
    }
    setUploading(true);
    setError("");
    try {
      await api.coverage.upload(
        selectedFiles,
        uploadName || undefined,
        selectedWorkspaceId || undefined,
      );
      setUploadName("");
      setSelectedFiles([]);
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
      let d = await api.coverage.get(id);
      for (let i = 0; d.status === "analyzing" && i < 30; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        d = await api.coverage.get(id);
      }
      setDetail(d);
      if (result.results?.length) {
        setModuleResults(result.results);
      } else if (d.analysis_results_json) {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } else {
        setModuleResults([]);
      }
      applyDetail(id, d);
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
      applyDetail(id, d);
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
          上传覆盖率报告（XML/HTML/CSV/TSV/TXT/XLSX/XLS），AI 结合工作区报告、源码和工具证据推荐测试用例
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
            <option value="">不绑定工作区</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name} - {ws.repo_path}
              </option>
            ))}
          </select>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".xml,.html,.htm,.csv,.tsv,.txt,.xlsx,.xls"
              onChange={(e) => handleFileSelect(e.target.files)}
              className="sr-only"
              disabled={uploading}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-surface-container text-on-surface-variant text-sm hover:bg-surface-container-high disabled:opacity-50"
            >
              <Upload size={14} />
              选择文件
            </button>
            <div className="flex-1 min-w-0 text-xs text-on-surface-variant">
              {selectedFiles.length > 0
                ? selectedFiles.map((f) => f.name).join("、")
                : "尚未选择文件"}
            </div>
            <button
              type="button"
              onClick={handleUpload}
              disabled={uploading || selectedFiles.length === 0}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/10 text-primary text-sm hover:bg-primary/20 disabled:opacity-50"
            >
              {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              上传并解析
            </button>
          </div>
          <p className="text-xs text-on-surface-variant/60">
            支持 Cobertura XML、JaCoCo XML、HTML 覆盖率报告，以及内网函数命中表（CSV/TSV/TXT/XLSX，兼容文本导出的 XLS）。可多文件上传（按模块目录分类）
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
              <div className="p-4 flex flex-col gap-4 lg:flex-row lg:items-center">
                <button
                  onClick={() => handleExpand(a.id)}
                  className="flex-1 flex flex-col gap-3 text-left sm:flex-row sm:items-center"
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
                        <span>工作区：{a.workspace_id.slice(0, 8)}</span>
                      )}
                      <span>
                        {new Date(a.created_at).toLocaleString("zh-CN")}
                      </span>
                    </div>
                  </div>

                  {/* Mini rates */}
                  <div className="flex flex-wrap items-center gap-4 shrink-0">
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
                        {a.source_format === "internal_function_hits"
                          ? "无数据"
                          : pct(a.overall_branch_rate)}
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
                <div className="flex flex-wrap items-center gap-2 shrink-0">
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
                    {detail.source_format === "internal_function_hits" ? (
                      <div className="text-xs text-on-surface-variant">
                        分支覆盖：无数据（当前文件只包含函数命中信息）
                      </div>
                    ) : (
                      <RateBar
                        rate={detail.overall_branch_rate}
                        label="分支覆盖"
                      />
                    )}
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
                                  {mr.risk_level ? ` · 风险：${levelLabel(mr.risk_level)}` : ""}
                                  {mr.confidence ? ` · 证据置信：${levelLabel(mr.confidence)}` : ""}
                                </div>
                              )}
                              {mr.kind === "function" || mr.function_name ? (
                                <div className="flex flex-wrap items-center gap-3 mt-1 text-xs">
                                  <span className="text-red-300">
                                    未覆盖 · hit_count={mr.hit_count ?? 0}
                                  </span>
                                  <span className="text-on-surface-variant">
                                    模块：{mr.module_path}
                                  </span>
                                </div>
                              ) : (
                                <div className="flex flex-wrap items-center gap-3 mt-1 text-xs">
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
                              )}
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
                              ) : (
                                <>
                                  <GapDesignDetail mr={mr} />
                                  {mr.analysis ? (
                                    <details className="mt-3 group">
                                      <summary className="text-xs text-on-surface-variant/70 cursor-pointer hover:text-on-surface">
                                        原始建议（Markdown）
                                      </summary>
                                      <div className="mt-2 prose prose-sm max-w-none text-on-surface-variant leading-relaxed whitespace-pre-wrap">
                                        {mr.analysis}
                                      </div>
                                    </details>
                                  ) : null}
                                  {!mr.analysis &&
                                    !mr.trigger_branches?.length &&
                                    !mr.entry_paths?.length &&
                                    !mr.black_box_cases?.length && (
                                      <p className="text-sm text-on-surface-variant mt-2">
                                        暂无分析结果
                                      </p>
                                    )}
                                </>
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

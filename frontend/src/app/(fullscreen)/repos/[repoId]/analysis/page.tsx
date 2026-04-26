"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import type {
  AnalysisSummary,
  TestPoint,
  TaintPath,
  JoernMethodBranch,
  JoernErrorPath,
  JoernBoundaryValue,
  JoernCallContext,
  JoernCalleeImpact,
  JoernMethod,
  VarUsage,
} from "@/lib/types";
import {
  ArrowLeft,
  RefreshCw,
  Download,
  GitBranch,
  FlaskConical,
  Network,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  Play,
  Loader2,
  CheckCircle2,
  Maximize2,
  ZoomIn,
  ZoomOut,
  Maximize,
  X,
  Code,
  ExternalLink,
  Search,
  ShieldAlert,
  AlertTriangle,
  TrendingUp,
  ArrowUpDown,
  FileText,
  BarChart3,
  FileDown,
  Filter,
} from "lucide-react";

// ── Shared Interactive Components ─────────────────────────────────────────

function SourceViewerModal({
  repoId,
  filePath,
  line,
  onClose,
}: {
  repoId: string;
  filePath: string;
  line?: number;
  onClose: () => void;
}) {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.repos
      .file(repoId, filePath, line ? Math.max(1, line - 20) : undefined, line ? line + 20 : undefined)
      .then((res) => setCode(res.content))
      .catch(() => setCode("无法加载源代码"))
      .finally(() => setLoading(false));
  }, [repoId, filePath, line]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, y: 20 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.95, y: 20 }}
        className="w-full max-w-4xl h-[80vh] bg-surface-container-lowest rounded-2xl border border-outline-variant/20 shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-outline-variant/10 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Code size={16} className="text-primary" />
            <div className="flex flex-col">
              <span className="text-xs font-data font-bold text-on-surface uppercase tracking-widest">{filePath}</span>
              {line && <span className="text-[10px] font-data text-on-surface-variant/40 tracking-wider">Line {line}</span>}
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-surface-container-high rounded-full transition-colors">
            <X size={18} className="text-on-surface-variant/60" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-6 font-mono text-[13px] leading-relaxed relative bg-surface-container/30">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="animate-spin text-primary" />
            </div>
          ) : (
            <pre className="text-on-surface/80">
              <code>
                {code.split("\n").map((l, i) => {
                  const currentLine = line ? line - 20 + i : i + 1;
                  const isHighlight = currentLine === line;
                  return (
                    <div key={i} className={`flex gap-4 ${isHighlight ? "bg-primary/10 -mx-6 px-6 border-l-2 border-primary" : ""}`}>
                      <span className="w-10 text-right text-on-surface-variant/20 select-none">{currentLine}</span>
                      <span>{l}</span>
                    </div>
                  );
                })}
              </code>
            </pre>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}

function CodeLink({
  filePath,
  line,
  onOpen,
}: {
  filePath: string;
  line?: number;
  onOpen: (path: string, l?: number) => void;
}) {
  return (
    <button
      onClick={() => onOpen(filePath, line)}
      className="group inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md hover:bg-primary/5 transition-all cursor-pointer"
    >
      <span className="text-[10px] font-data text-on-surface-variant/40 group-hover:text-primary transition-colors underline decoration-outline-variant/20 group-hover:decoration-primary/40 underline-offset-2">
        {shortPath(filePath)}{line ? `:${line}` : ""}
      </span>
      <ExternalLink size={10} className="text-on-surface-variant/20 group-hover:text-primary transition-colors" />
    </button>
  );
}

function MethodLink({
  name,
  onClick,
}: {
  name: string;
  onClick: (name: string) => void;
}) {
  return (
    <button
      onClick={() => onClick(name)}
      className="group inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border border-outline-variant/10 bg-surface-container-low hover:border-primary/40 hover:bg-primary/5 transition-all text-left shadow-sm hover:shadow-md"
    >
      <Search size={12} className="text-on-surface-variant/20 group-hover:text-primary transition-colors" />
      <span className="font-data text-sm font-bold text-on-surface group-hover:text-primary transition-colors">
        {name}
      </span>
    </button>
  );
}

function VariableTrackerModal({
  repoId,
  methodName,
  varName,
  onClose,
  onOpenSource,
}: {
  repoId: string;
  methodName: string;
  varName: string;
  onClose: () => void;
  onOpenSource: (path: string, line?: number) => void;
}) {
  const [usages, setUsages] = useState<VarUsage[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.repos.analysis.joern
      .variableTracking(repoId, methodName, varName)
      .then((res) => setUsages(res.usages))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [repoId, methodName, varName]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, y: 20 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.95, y: 20 }}
        className="w-full max-w-2xl h-[60vh] bg-surface-container-lowest rounded-2xl border border-outline-variant/20 shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-outline-variant/10 flex items-center justify-between bg-surface-container-low/50">
          <div className="flex items-center gap-3">
            <Network size={16} className="text-amber-400" />
            <div className="flex flex-col">
              <span className="text-xs font-data font-bold text-on-surface uppercase tracking-widest">
                变量追踪: {varName}
              </span>
              <span className="text-[10px] font-data text-on-surface-variant/40 tracking-wider uppercase">
                作用域: {methodName}()
              </span>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-surface-container-high rounded-full transition-colors">
            <X size={18} className="text-on-surface-variant/60" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-6 space-y-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-on-surface-variant/40">
              <Loader2 className="animate-spin text-amber-400" />
              <span className="text-[10px] font-data uppercase tracking-widest">正在分析变量数据流...</span>
            </div>
          ) : usages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-on-surface-variant/20">
              <CheckCircle2 size={32} />
              <p className="text-[10px] font-data uppercase tracking-widest">未找到该变量的显式使用点</p>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mb-4 px-1">
                检测到 {usages.length} 个使用节点
              </p>
              {usages.map((u, i) => (
                <div
                  key={i}
                  className="group relative flex items-center justify-between p-3 rounded-xl border border-outline-variant/10 bg-surface-container-low/50 hover:border-amber-400/30 hover:bg-amber-400/[0.02] transition-all"
                >
                  <div className="flex items-center gap-4">
                    <span className="text-[10px] font-data text-on-surface-variant/20 w-4 font-bold">#{i + 1}</span>
                    <code className="text-[12px] font-data text-on-surface/80 group-hover:text-amber-400 transition-colors">
                      {u.code}
                    </code>
                  </div>
                  <CodeLink filePath={u.filename ?? ""} line={u.line_number ?? undefined} onOpen={onOpenSource} />
                </div>
              ))}
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}

// ── Pagination constants & component ──────────────────────────────────────
const PAGE_SIZE = 10;

function Pagination({
  current,
  total,
  onChange,
}: {
  current: number;
  total: number;
  onChange: (page: number) => void;
}) {
  if (total <= 1) return null;

  const pages: (number | "...")[] = [];
  for (let i = 1; i <= total; i++) {
    if (i === 1 || i === total || (i >= current - 1 && i <= current + 1)) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== "...") {
      pages.push("...");
    }
  }

  return (
    <div className="flex items-center justify-center gap-1 pt-6 pb-2">
      <button
        onClick={() => onChange(current - 1)}
        disabled={current === 1}
        className="p-1.5 rounded-lg text-on-surface-variant/40 hover:text-on-surface hover:bg-surface-container-high transition-colors disabled:opacity-20 disabled:cursor-not-allowed"
      >
        <ChevronLeft size={14} />
      </button>
      {pages.map((p, i) =>
        p === "..." ? (
          <span key={`e${i}`} className="px-1 text-[10px] text-on-surface-variant/30">...</span>
        ) : (
          <button
            key={p}
            onClick={() => onChange(p)}
            className={`min-w-[28px] h-7 rounded-lg text-[11px] font-data transition-all ${
              p === current
                ? "bg-primary/15 text-primary border border-primary/30 font-bold"
                : "text-on-surface-variant/50 hover:text-on-surface hover:bg-surface-container-high"
            }`}
          >
            {p}
          </button>
        )
      )}
      <button
        onClick={() => onChange(current + 1)}
        disabled={current === total}
        className="p-1.5 rounded-lg text-on-surface-variant/40 hover:text-on-surface hover:bg-surface-container-high transition-colors disabled:opacity-20 disabled:cursor-not-allowed"
      >
        <ChevronRight size={14} />
      </button>
      <span className="ml-3 text-[10px] font-data text-on-surface-variant/30">
        {current} / {total}
      </span>
    </div>
  );
}

function usePagination<T>(items: T[], pageSize = PAGE_SIZE) {
  const safe = Array.isArray(items) ? items : [];
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(safe.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const paged = safe.slice((safePage - 1) * pageSize, safePage * pageSize);
  return { page: safePage, setPage, totalPages, paged };
}

// ── Nav items ──────────────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: "overview", label: "风险总览", icon: ShieldAlert },
  { id: "branches", label: "深度分析", icon: GitBranch },
  { id: "testpoints", label: "测试计划", icon: FlaskConical },
  { id: "taint", label: "数据追踪", icon: Network },
  { id: "complexity", label: "复杂度", icon: Maximize2 },
  { id: "search", label: "模式搜索", icon: Search },
] as const;
type NavId = (typeof NAV_ITEMS)[number]["id"];

// ── Complexity sub-view ───────────────────────────────────────────────────
/** Synthetic Joern nodes that represent file-level scope, not real functions */
const SYNTHETIC_NAMES = new Set(["<global>", "<clinit>", "<init>", "<meta>"]);

function ComplexityView({
  repoId,
  onOpenSource,
}: {
  repoId: string;
  onOpenSource: (p: string, l?: number) => void;
}) {
  const [rawMethods, setRawMethods] = useState<JoernMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.repos.analysis.joern.methods(repoId)
      .then((res) => setRawMethods((res.methods || []) as JoernMethod[]))
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [repoId]);

  // Filter out synthetic nodes — they're not real functions
  const methods = useMemo(
    () => rawMethods.filter(m => !SYNTHETIC_NAMES.has(m.name)),
    [rawMethods],
  );
  const syntheticCount = rawMethods.length - methods.length;

  // Density = complexity / lines (how concentrated the branching is)
  const withDensity = useMemo(
    () => methods.map(m => {
      const lines = Math.max((m.lineEnd ?? m.line) - m.line + 1, 1);
      const comp = m.complexity ?? 0;
      return { ...m, density: comp / lines, lines };
    }),
    [methods],
  );

  const stats = useMemo(() => {
    if (!withDensity.length) return null;
    const comps = withDensity.map(m => m.complexity ?? 0);
    const maxComp = Math.max(...comps, 0);
    const avgComp = comps.reduce((a, b) => a + b, 0) / comps.length;
    const highRisk = comps.filter(c => c > 15).length;
    const maxDensity = Math.max(...withDensity.map(m => m.density), 0);
    return { maxComp, avgComp, highRisk, total: withDensity.length, maxDensity };
  }, [withDensity]);

  // Top 10 by raw complexity
  const topByComplexity = useMemo(
    () => [...withDensity].sort((a, b) => (b.complexity ?? 0) - (a.complexity ?? 0)).slice(0, 10),
    [withDensity],
  );

  // Top 10 by density (only functions with complexity >= 3 to avoid trivial noise)
  const topByDensity = useMemo(
    () => [...withDensity]
      .filter(m => (m.complexity ?? 0) >= 3)
      .sort((a, b) => b.density - a.density)
      .slice(0, 10),
    [withDensity],
  );

  const [rankMode, setRankMode] = useState<"absolute" | "density">("absolute");

  if (loading) return (
    <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/40">
      <Loader2 className="animate-spin text-primary" />
      <span className="text-[10px] font-data uppercase tracking-widest">正在加载函数控制结构数据...</span>
    </div>
  );

  if (err) return (
    <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/40">
      <ShieldAlert className="w-8 h-8 text-tertiary/60" />
      <span className="text-xs text-tertiary/80">{err}</span>
    </div>
  );

  const topList = rankMode === "absolute" ? topByComplexity : topByDensity;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Metric explanation */}
      <div className="px-1 py-2 border-l-2 border-primary/30 pl-4">
        <p className="text-[11px] text-on-surface-variant/60 leading-relaxed">
          统计每个函数内 <span className="text-primary font-data">if / for / while / switch / try</span> 等控制结构的数量。
          数值越高表示分支逻辑越多，但不等同于圈复杂度 (Cyclomatic Complexity)。
          {syntheticCount > 0 && (
            <span className="text-on-surface-variant/40"> 已过滤 {syntheticCount} 个文件级合成节点。</span>
          )}
        </p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="rounded-xl border border-outline-variant/10 bg-surface-container-low p-5 space-y-2">
          <span className="text-[9px] font-data uppercase tracking-widest text-on-surface-variant/40">函数总数</span>
          <p className="text-2xl font-display font-bold text-on-surface">{stats?.total}</p>
        </div>
        <div className="rounded-xl border border-outline-variant/10 bg-surface-container-low p-5 space-y-2">
          <span className="text-[9px] font-data uppercase tracking-widest text-on-surface-variant/40">最高控制结构数</span>
          <p className="text-2xl font-display font-bold text-tertiary">{stats?.maxComp}</p>
        </div>
        <div className="rounded-xl border border-outline-variant/10 bg-surface-container-low p-5 space-y-2">
          <span className="text-[9px] font-data uppercase tracking-widest text-on-surface-variant/40">平均控制结构数</span>
          <p className="text-2xl font-display font-bold text-primary">{stats?.avgComp.toFixed(1)}</p>
        </div>
        <div className="rounded-xl border border-outline-variant/10 bg-surface-container-low p-5 space-y-2">
          <span className="text-[9px] font-data uppercase tracking-widest text-on-surface-variant/40">高风险 (&gt;15)</span>
          <p className="text-2xl font-display font-bold text-amber-400">{stats?.highRisk}</p>
        </div>
      </div>

      {/* Distribution Histogram */}
      <div className="rounded-2xl border border-outline-variant/10 bg-surface-container-lowest p-6 space-y-6">
        <div className="flex items-center justify-between">
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">控制结构分布 (Distribution)</h3>
          <span className="text-[9px] font-data text-on-surface-variant/20 uppercase tracking-widest">仅含真实函数</span>
        </div>

        {(() => {
          const buckets = [
            { label: "0", min: 0, max: 0 },
            { label: "1–5", min: 1, max: 5 },
            { label: "6–10", min: 6, max: 10 },
            { label: "11–15", min: 11, max: 15 },
            { label: "16–30", min: 16, max: 30 },
            { label: "31–50", min: 31, max: 50 },
            { label: "51+", min: 51, max: Infinity },
          ];
          const counts = buckets.map(b => ({
            ...b,
            count: methods.filter(m => {
              const c = m.complexity ?? 0;
              return c >= b.min && c <= b.max;
            }).length,
          }));
          const maxCount = Math.max(...counts.map(c => c.count), 1);
          const barMaxH = 200;

          return (
            <div className="flex items-end gap-3 px-8 pt-4 pb-2">
              {counts.map((b, i) => {
                const h = Math.max((b.count / maxCount) * barMaxH, 4);
                const isHighRisk = b.min > 15;
                const pct = stats ? ((b.count / stats.total) * 100).toFixed(0) : "0";
                return (
                  <div key={i} className="flex-1 flex flex-col items-center gap-1.5 group">
                    <span className="text-[10px] font-data font-bold text-on-surface-variant/50 group-hover:text-on-surface transition-colors">
                      {b.count}
                    </span>
                    <div
                      className={`w-full rounded-t-lg transition-all duration-500 ${
                        isHighRisk
                          ? "bg-tertiary/40 group-hover:bg-tertiary/70"
                          : "bg-primary/30 group-hover:bg-primary/60"
                      }`}
                      style={{ height: `${h}px` }}
                    />
                    <span className={`text-[9px] font-data ${isHighRisk ? "text-tertiary/60" : "text-on-surface-variant/40"}`}>
                      {b.label}
                    </span>
                    <span className="text-[8px] font-data text-on-surface-variant/20">
                      {pct}%
                    </span>
                  </div>
                );
              })}
            </div>
          );
        })()}
      </div>

      {/* Top 10 — switchable between absolute and density */}
      <div className="space-y-4">
        <div className="flex items-center justify-between px-2">
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">
            {rankMode === "absolute" ? "控制结构最多 Top 10" : "分支密度最高 Top 10"}
          </h3>
          <div className="flex bg-surface-container-low/40 p-0.5 rounded-full border border-white/5">
            <button
              onClick={() => setRankMode("absolute")}
              className={`px-3 py-1 text-[9px] font-data rounded-full transition-all ${
                rankMode === "absolute"
                  ? "bg-primary/20 text-primary"
                  : "text-on-surface-variant/40 hover:text-on-surface-variant"
              }`}
            >
              绝对数量
            </button>
            <button
              onClick={() => setRankMode("density")}
              className={`px-3 py-1 text-[9px] font-data rounded-full transition-all ${
                rankMode === "density"
                  ? "bg-secondary/20 text-secondary"
                  : "text-on-surface-variant/40 hover:text-on-surface-variant"
              }`}
            >
              密度 (结构/行)
            </button>
          </div>
        </div>
        <div className="grid gap-3">
          {topList.map((m, i) => (
            <div key={`${rankMode}-${i}`} className="group flex items-center justify-between p-4 rounded-xl border border-outline-variant/10 bg-surface-container-low hover:border-tertiary/20 transition-all">
              <div className="flex items-center gap-4">
                <span className="text-[10px] font-data text-on-surface-variant/20 w-4 font-bold">#{i + 1}</span>
                <div className="flex flex-col">
                  <span className="text-sm font-data font-bold text-on-surface group-hover:text-tertiary transition-colors">{m.name}</span>
                  <span className="text-[10px] font-data text-on-surface-variant/40">
                    {shortPath(m.filename)}:{m.line}
                    <span className="ml-2 text-on-surface-variant/25">{m.lines} 行</span>
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-6">
                <div className="flex flex-col items-end">
                  <span className="text-[8px] font-data uppercase tracking-widest text-on-surface-variant/30">控制结构</span>
                  <span className={`text-sm font-data font-bold ${(m.complexity ?? 0) > 15 ? 'text-tertiary' : 'text-primary'}`}>{m.complexity ?? 0}</span>
                </div>
                <div className="flex flex-col items-end">
                  <span className="text-[8px] font-data uppercase tracking-widest text-on-surface-variant/30">密度</span>
                  <span className={`text-sm font-data font-bold ${m.density > 0.3 ? 'text-tertiary' : 'text-secondary'}`}>{m.density.toFixed(2)}</span>
                </div>
                <CodeLink filePath={m.filename} line={m.line} onOpen={onOpenSource} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Show last 2 path segments for disambiguation (e.g. "handlers/request.c") */
function shortPath(filepath: string | undefined): string {
  if (!filepath) return "";
  return filepath.split("/").slice(-2).join("/");
}

// ── Overview ──────────────────────────────────────────────────────────────
function ToolStatusDot({ healthy, label }: { healthy: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-1.5 h-1.5 rounded-full ${healthy ? "bg-secondary shadow-[0_0_6px_rgba(236,255,227,0.6)]" : "bg-on-surface-variant/20"}`} />
      <span className={`text-[10px] font-mono uppercase tracking-wider ${healthy ? "text-on-surface-variant/70" : "text-on-surface-variant/30"}`}>
        {label}
      </span>
    </div>
  );
}

type RiskLevel = "HIGH" | "MED" | "LOW";
interface EnrichedMethod extends JoernMethod {
  lines: number;
  density: number;
  riskScore: number;
  riskLevel: RiskLevel;
}

function riskLevel(complexity: number, density: number): RiskLevel {
  if (complexity > 15 || density > 0.5) return "HIGH";
  if (complexity > 8 || density > 0.2) return "MED";
  return "LOW";
}

const RISK_COLORS: Record<RiskLevel, string> = {
  HIGH: "text-tertiary bg-tertiary/10 border-tertiary/20",
  MED: "text-amber-400 bg-amber-400/10 border-amber-400/20",
  LOW: "text-secondary bg-secondary/10 border-secondary/20",
};

function RiskDashboardView({
  repoId,
  summary,
  onNavigate,
  onExport,
}: {
  repoId: string;
  summary: AnalysisSummary | null;
  onNavigate: (method: string) => void;
  onExport: (data: EnrichedMethod[]) => void;
}) {
  const [rawMethods, setRawMethods] = useState<JoernMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<"riskScore" | "complexity" | "density" | "lines">("riskScore");
  const [sortAsc, setSortAsc] = useState(false);
  const [filterLevel, setFilterLevel] = useState<RiskLevel | "ALL">("ALL");
  const PAGE = 20;
  const [page, setPage] = useState(0);
  const [trend, setTrend] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    api.repos.analysis.joern.methods(repoId)
      .then((res) => setRawMethods((res.methods || []) as JoernMethod[]))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [repoId]);

  const enriched = useMemo<EnrichedMethod[]>(() => {
    return rawMethods
      .filter(m => !SYNTHETIC_NAMES.has(m.name))
      .map(m => {
        const lines = Math.max(1, (m.lineEnd || m.line) - m.line);
        const c = m.complexity ?? 0;
        const density = c / lines;
        const rl = riskLevel(c, density);
        return { ...m, lines, density, riskScore: c * (1 + density), riskLevel: rl };
      });
  }, [rawMethods]);

  // Snapshot persistence + trend calculation
  const snapshotSavedRef = useRef(false);
  useEffect(() => {
    if (enriched.length === 0 || snapshotSavedRef.current) return;
    snapshotSavedRef.current = true;
    const hc = enriched.filter(m => m.riskLevel === "HIGH").length;
    const mc = enriched.filter(m => m.riskLevel === "MED").length;
    const ac = enriched.length > 0
      ? Math.round(enriched.reduce((s, m) => s + (m.complexity ?? 0), 0) / enriched.length * 10) / 10
      : 0;
    const currentSummary = { total: enriched.length, high: hc, med: mc, avgComplexity: ac };

    // Fetch previous snapshot for trend, then save current
    api.repos.analysis.snapshots.list(repoId)
      .then((res) => {
        const prev = res.snapshots?.[0]?.summary;
        if (prev) {
          setTrend({
            total: currentSummary.total - (prev.total ?? 0),
            high: currentSummary.high - (prev.high ?? 0),
            med: currentSummary.med - (prev.med ?? 0),
            avgComplexity: Math.round((currentSummary.avgComplexity - (prev.avgComplexity ?? 0)) * 10) / 10,
          });
        }
      })
      .catch(() => {})
      .finally(() => {
        // Save new snapshot
        api.repos.analysis.snapshots.save(repoId, enriched, currentSummary).catch(() => {});
      });
  }, [enriched, repoId]);

  const filtered = useMemo(() => {
    if (filterLevel === "ALL") return enriched;
    return enriched.filter(m => m.riskLevel === filterLevel);
  }, [enriched, filterLevel]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const av = sortKey === "complexity" ? (a.complexity ?? 0) : a[sortKey];
      const bv = sortKey === "complexity" ? (b.complexity ?? 0) : b[sortKey];
      return sortAsc ? av - bv : bv - av;
    });
  }, [filtered, sortKey, sortAsc]);

  const paged = sorted.slice(page * PAGE, (page + 1) * PAGE);
  const totalPages = Math.ceil(sorted.length / PAGE);

  const highCount = enriched.filter(m => m.riskLevel === "HIGH").length;
  const medCount = enriched.filter(m => m.riskLevel === "MED").length;
  const avgC = enriched.length > 0
    ? Math.round(enriched.reduce((s, m) => s + (m.complexity ?? 0), 0) / enriched.length * 10) / 10
    : 0;
  const maxMethod = enriched.length > 0
    ? enriched.reduce((mx, m) => m.riskScore > mx.riskScore ? m : mx, enriched[0])
    : null;

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
    setPage(0);
  };

  const joernHealthy = summary?.tools.joern.healthy ?? false;

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/40">
        <Loader2 className="animate-spin text-primary" />
        <span className="text-[10px] font-data uppercase tracking-widest">加载风险矩阵...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Tool status */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 rounded-xl border border-outline-variant/10 bg-surface-container-low px-4 py-2">
          <ToolStatusDot healthy={joernHealthy} label="Joern CPG" />
        </div>
        <button
          onClick={() => onExport(sorted)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-outline-variant/15 text-[10px] font-data uppercase tracking-widest text-on-surface-variant/50 hover:border-primary/30 hover:text-primary transition-all"
        >
          <FileDown size={12} />
          导出 CSV
        </button>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "总函数", value: enriched.length, icon: BarChart3, color: "text-primary", trendKey: "total" },
          { label: "高风险", value: highCount, icon: AlertTriangle, color: "text-tertiary", trendKey: "high" },
          { label: "中风险", value: medCount, icon: ShieldAlert, color: "text-amber-400", trendKey: "med" },
          { label: "平均复杂度", value: avgC, icon: TrendingUp, color: "text-secondary", trendKey: "avgComplexity" },
        ].map(({ label, value, icon: Ic, color, trendKey }) => {
          const delta = trend?.[trendKey];
          return (
            <div key={label} className="rounded-xl border border-outline-variant/10 bg-surface-container-low/50 p-4 space-y-2">
              <div className="flex items-center gap-2">
                <Ic size={12} className={`${color} opacity-60`} />
                <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40">{label}</span>
              </div>
              <div className="flex items-baseline gap-2">
                <p className={`text-2xl font-display font-bold ${color}`}>{value}</p>
                {delta != null && delta !== 0 && (
                  <span className={`text-[10px] font-data font-bold ${delta > 0 ? "text-tertiary" : "text-secondary"}`}>
                    {delta > 0 ? `+${delta}` : delta}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Hottest function callout */}
      {maxMethod && maxMethod.riskLevel === "HIGH" && (
        <button
          onClick={() => onNavigate(maxMethod.name)}
          className="w-full group rounded-xl border border-tertiary/20 bg-tertiary/5 p-4 flex items-center justify-between hover:bg-tertiary/10 transition-all text-left"
        >
          <div className="flex items-center gap-3">
            <AlertTriangle size={16} className="text-tertiary" />
            <div>
              <p className="text-xs font-data font-bold text-on-surface">
                最高风险: <code className="text-tertiary">{maxMethod.name}()</code>
              </p>
              <p className="text-[10px] font-data text-on-surface-variant/40 mt-0.5">
                复杂度 {maxMethod.complexity ?? 0} · 密度 {maxMethod.density.toFixed(2)} · {shortPath(maxMethod.filename)}
              </p>
            </div>
          </div>
          <ChevronRight size={14} className="text-on-surface-variant/30 group-hover:text-tertiary transition-colors" />
        </button>
      )}

      {/* Filter row */}
      <div className="flex items-center gap-2">
        <Filter size={12} className="text-on-surface-variant/30" />
        {(["ALL", "HIGH", "MED", "LOW"] as const).map(level => (
          <button
            key={level}
            onClick={() => { setFilterLevel(level); setPage(0); }}
            className={`text-[10px] font-data px-3 py-1 rounded-full border transition-all uppercase tracking-widest ${
              filterLevel === level
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-outline-variant/15 text-on-surface-variant/40 hover:border-outline-variant/30"
            }`}
          >
            {level === "ALL" ? `全部 (${enriched.length})` : `${level} (${enriched.filter(m => m.riskLevel === level).length})`}
          </button>
        ))}
      </div>

      {/* Risk table */}
      <div className="rounded-2xl border border-outline-variant/10 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="bg-surface-container-low/50 border-b border-outline-variant/10">
              {([
                { key: "riskScore" as const, label: "风险" },
                { key: null, label: "函数" },
                { key: "complexity" as const, label: "复杂度" },
                { key: "density" as const, label: "密度" },
                { key: "lines" as const, label: "行数" },
              ] as const).map(({ key, label }) => (
                <th
                  key={label}
                  className={`px-4 py-3 text-left text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 ${key ? "cursor-pointer hover:text-on-surface-variant/60 select-none" : ""}`}
                  onClick={() => key && toggleSort(key)}
                >
                  <span className="flex items-center gap-1">
                    {label}
                    {key && sortKey === key && <ArrowUpDown size={10} className="text-primary" />}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((m, i) => (
              <tr
                key={`${m.name}-${m.filename}-${i}`}
                className="border-b border-outline-variant/5 hover:bg-surface-container-low/30 transition-colors cursor-pointer group"
                onClick={() => onNavigate(m.name)}
              >
                <td className="px-4 py-3">
                  <span className={`inline-flex px-2 py-0.5 rounded-full text-[9px] font-data font-bold uppercase tracking-wider border ${RISK_COLORS[m.riskLevel]}`}>
                    {m.riskLevel}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-col">
                    <code className="text-xs font-data text-on-surface group-hover:text-primary transition-colors">{m.name}()</code>
                    <span className="text-[10px] font-data text-on-surface-variant/30 mt-0.5">{shortPath(m.filename)}</span>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className={`text-sm font-data font-bold ${(m.complexity ?? 0) > 15 ? "text-tertiary" : (m.complexity ?? 0) > 8 ? "text-amber-400" : "text-on-surface-variant/60"}`}>
                    {m.complexity ?? 0}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 rounded-full bg-surface-container-high overflow-hidden">
                      <div
                        className={`h-full rounded-full ${m.density > 0.5 ? "bg-tertiary" : m.density > 0.2 ? "bg-amber-400" : "bg-secondary"}`}
                        style={{ width: `${Math.min(100, m.density * 200)}%` }}
                      />
                    </div>
                    <span className="text-[10px] font-data text-on-surface-variant/40">{m.density.toFixed(2)}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-xs font-data text-on-surface-variant/40">{m.lines}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="p-1.5 rounded-lg border border-outline-variant/15 disabled:opacity-20">
            <ChevronLeft size={14} />
          </button>
          <span className="text-[10px] font-data text-on-surface-variant/40 uppercase tracking-widest">
            {page + 1} / {totalPages}
          </span>
          <button onClick={() => setPage(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1} className="p-1.5 rounded-lg border border-outline-variant/15 disabled:opacity-20">
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}


// ── Structured renderers for Joern results ───────────────────────────────

function CallContextCards({
  items,
  onOpenSource,
  onMethodClick,
}: {
  items: JoernCallContext[];
  onOpenSource: (p: string, l?: number) => void;
  onMethodClick: (n: string) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      <AnimatePresence mode="popLayout">
        {paged.map((ctx, i) => (
          <motion.div
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            key={`${ctx.caller}-${i}`}
            className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-3 hover:border-primary/20 transition-all"
          >
            <div className="flex items-center justify-between">
              <MethodLink name={ctx.caller} onClick={onMethodClick} />
              <CodeLink filePath={ctx.callerFile ?? ""} line={ctx.callerLine ?? undefined} onOpen={onOpenSource} />
            </div>
            {ctx.callSites?.length > 0 && (
              <div className="space-y-1">
                <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40">调用位置</span>
                {ctx.callSites.map((site, j) => (
                  <div key={j} className="flex items-center gap-2 text-[11px] font-data pl-2 border-l border-primary/20">
                    <span className="text-primary/50 font-bold">L{site.line}</span>
                    <span className="text-on-surface-variant/60">{site.args?.join(", ") || "—"}</span>
                  </div>
                ))}
              </div>
            )}
            {ctx.callerBranches?.length > 0 && (
              <div className="space-y-1 pt-1 border-t border-outline-variant/5">
                <span className="text-[9px] font-data uppercase tracking-[0.2em] text-tertiary/50">调用者分支（影响下游走向）</span>
                {ctx.callerBranches.map((br, j) => (
                  <div key={j} className="flex items-start gap-2 text-[11px] font-data pl-2 border-l border-tertiary/20">
                    <span className="text-tertiary/40 shrink-0 uppercase text-[9px] mt-0.5">{br.type}</span>
                    <code className="text-on-surface-variant/70 break-all">{br.condition}</code>
                    <span className="text-on-surface-variant/30 shrink-0 ml-auto">L{br.line}</span>
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function CalleeImpactCards({
  items,
  onOpenSource,
  onMethodClick,
}: {
  items: JoernCalleeImpact[];
  onOpenSource: (p: string, l?: number) => void;
  onMethodClick: (n: string) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      <AnimatePresence mode="popLayout">
        {paged.map((imp, i) => (
          <motion.div
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            key={`${imp.callee}-${i}`}
            className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-3 hover:border-primary/20 transition-all"
          >
            <div className="flex items-center justify-between">
              <MethodLink name={imp.callee} onClick={onMethodClick} />
              <CodeLink filePath={imp.calleeFile ?? ""} line={imp.calleeLine ?? undefined} onOpen={onOpenSource} />
            </div>
            {imp.callSitesInTarget?.length > 0 && (
              <div className="space-y-1">
                <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40">调用点</span>
                {imp.callSitesInTarget.map((site, j) => (
                  <button
                    key={j}
                    onClick={() => onOpenSource(imp.calleeFile ?? "", site.line)}
                    className="block w-full text-left group/line hover:bg-primary/5 rounded px-2 py-1 transition-colors"
                  >
                    <code className="text-[11px] font-data text-on-surface-variant/60 group-hover/line:text-primary transition-colors">
                      L{site.line}: {site.code}
                    </code>
                  </button>
                ))}
              </div>
            )}
            {imp.errorReturns?.length > 0 && (
              <div className="space-y-1 pt-1 border-t border-outline-variant/5">
                <span className="text-[9px] font-data uppercase tracking-[0.2em] text-tertiary/50">被调用方异常返回</span>
                {imp.errorReturns.map((er, j) => (
                  <div key={j} className="flex items-start gap-2 text-[11px] font-data pl-2 border-l border-tertiary/20">
                    <span className="text-tertiary/40 shrink-0 font-bold">L{er.line}</span>
                    <code className="text-on-surface-variant/70 break-all">{er.code}</code>
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function BranchCards({
  items,
  onOpenSource,
}: {
  items: JoernMethodBranch[];
  onOpenSource: (p: string, l?: number) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  const TYPE_LABEL: Record<string, string> = { IfStatement: "IF", ElseStatement: "ELSE", SwitchStatement: "SWITCH", ForStatement: "FOR", WhileStatement: "WHILE", DoStatement: "DO", TryStatement: "TRY" };
  return (
    <div className="space-y-3">
      <AnimatePresence mode="popLayout">
        {paged.map((br, i) => (
          <motion.div
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            key={i}
            className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-2 hover:border-primary/20 transition-all"
          >
            <div className="flex items-center justify-between">
              <span className="text-[9px] font-data font-bold uppercase tracking-widest px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                {TYPE_LABEL[br.control_structure_type] ?? br.control_structure_type}
              </span>
              <CodeLink filePath={br.filename ?? ""} line={br.line_number ?? undefined} onOpen={onOpenSource} />
            </div>
            {br.condition && (
              <code className="block text-[12px] font-data text-on-surface/80 pl-3 border-l-2 border-primary/20 py-1">{br.condition}</code>
            )}
            {br.children?.length > 0 && (
              <div className="space-y-1.5 pt-1">
                {br.children.map((child, j) => (
                  <div key={j} className="flex items-start gap-2 text-[11px] font-data pl-3 group/child">
                    <span className="text-on-surface-variant/30 shrink-0 uppercase text-[8px] mt-1 tracking-tighter">{child.label}</span>
                    <code className="text-on-surface-variant/60 group-hover/child:text-on-surface transition-colors break-words bg-surface-container/50 px-1.5 py-0.5 rounded">{child.code}</code>
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function ErrorPathCards({
  items,
  onOpenSource,
}: {
  items: JoernErrorPath[];
  onOpenSource: (p: string, l?: number) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  const KIND_STYLE: Record<string, string> = {
    throw: "bg-tertiary/10 text-tertiary border-tertiary/20",
    "try-catch": "bg-amber-400/10 text-amber-400 border-amber-400/20",
    "error-return": "bg-primary/10 text-primary border-primary/20",
    "error-call": "bg-tertiary/10 text-tertiary border-tertiary/20",
    goto: "bg-amber-400/10 text-amber-400 border-amber-400/20",
  };
  return (
    <div className="space-y-3">
      <AnimatePresence mode="popLayout">
        {paged.map((ep, i) => (
          <motion.div
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            key={i}
            className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 hover:border-tertiary/20 transition-all flex items-start gap-3"
          >
            <span className={`text-[9px] font-data font-bold uppercase tracking-widest px-2 py-0.5 rounded-full border shrink-0 ${KIND_STYLE[ep.kind] ?? KIND_STYLE["error-return"]}`}>
              {ep.kind}
            </span>
            <code className="text-[12px] font-data text-on-surface/80 break-words flex-1 py-0.5">{ep.code}</code>
            <CodeLink filePath={ep.filename ?? ""} line={ep.line_number ?? undefined} onOpen={onOpenSource} />
          </motion.div>
        ))}
      </AnimatePresence>
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function BoundaryCards({
  methodName,
  items,
  onOpenSource,
  onVarClick,
}: {
  methodName: string;
  items: JoernBoundaryValue[];
  onOpenSource: (p: string, l?: number) => void;
  onVarClick: (m: string, v: string) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      <AnimatePresence mode="popLayout">
        {paged.map((bv, i) => (
          <motion.div
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            key={i}
            className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-3 hover:border-amber-400/20 hover:shadow-lg transition-all"
          >
            <div className="flex items-center justify-between">
              <code className="text-[12px] font-data text-on-surface/80 bg-surface-container-high px-2 py-1 rounded">{bv.code}</code>
              <CodeLink filePath={bv.filename ?? ""} line={bv.line_number ?? undefined} onOpen={onOpenSource} />
            </div>
            {bv.operands?.length > 0 && (
              <div className="flex gap-2 flex-wrap pt-1">
                {bv.operands.map((op, j) => (
                  <button
                    key={j}
                    onClick={() => onVarClick(methodName, op.code)}
                    className="group/op flex items-center gap-1.5 text-[10px] font-data px-2.5 py-1 rounded-lg bg-amber-400/5 border border-amber-400/10 text-on-surface-variant/60 hover:bg-amber-400/10 hover:border-amber-400/30 transition-all active:scale-95"
                    title={`追踪变量 ${op.code} 的数据流`}
                  >
                    {op.order && <span className="text-amber-400/40 font-bold font-mono">#{op.order}</span>}
                    <span className="group-hover/op:text-amber-400 transition-colors">{op.code}</span>
                    <Network size={10} className="text-amber-400/20 group-hover/op:text-amber-400/60 transition-colors" />
                  </button>
                ))}
              </div>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

// ── Branches sub-view ──────────────────────────────────────────────────────
function BranchesView({
  repoId,
  initialMethod,
  onOpenSource,
}: {
  repoId: string;
  initialMethod?: string;
  onOpenSource: (p: string, l?: number) => void;
}) {
  const [methodName, setMethodName] = useState("");
  const [loading, setLoading] = useState(false);
  const [branches, setBranches] = useState<JoernMethodBranch[]>([]);
  const [errors, setErrors] = useState<JoernErrorPath[]>([]);
  const [boundaries, setBoundaries] = useState<JoernBoundaryValue[]>([]);
  const [callContext, setCallContext] = useState<JoernCallContext[]>([]);
  const [calleeImpact, setCalleeImpact] = useState<JoernCalleeImpact[]>([]);
  const [queriedMethod, setQueriedMethod] = useState("");
  const [queried, setQueried] = useState(false);
  const [err, setErr] = useState("");
  const [methodMeta, setMethodMeta] = useState<JoernMethod | null>(null);
  const [impactRadius, setImpactRadius] = useState<{ callerFiles: string[]; moduleDeps: Array<{ source: string; target: string; type: string }>; callerCount: number } | null>(null);

  // Variable tracker state
  const [varTracker, setVarTracker] = useState<{ methodName: string; varName: string } | null>(null);

  const handleQuery = useCallback(
    async (targetName?: string) => {
      const name = (targetName || methodName).trim();
      if (!name) return;
      if (targetName) setMethodName(name);

      setLoading(true);
      setErr("");
      try {
        const [result, methodsRes] = await Promise.all([
          api.repos.analysis.joern.allForMethod(repoId, name),
          api.repos.analysis.joern.methods(repoId),
        ]);
        setBranches(Array.isArray(result.branches) ? result.branches : []);
        setErrors(Array.isArray(result.errors) ? result.errors : []);
        setBoundaries(Array.isArray(result.boundaries) ? result.boundaries : []);
        setCallContext(Array.isArray(result.callContext) ? result.callContext : []);
        setCalleeImpact(Array.isArray(result.calleeImpact) ? result.calleeImpact : []);
        // Find this method's complexity metadata
        const allMethods = (methodsRes.methods || []) as JoernMethod[];
        const match = allMethods.find(m => m.name === name);
        setMethodMeta(match ?? null);
        setQueriedMethod(name);
        setQueried(true);
        // Fetch impact radius in background (best-effort)
        setImpactRadius(null);
        api.repos.analysis.impactRadius(repoId, name)
          .then((ir) => setImpactRadius({
            callerFiles: ir.caller_files,
            moduleDeps: ir.module_dependencies,
            callerCount: ir.caller_count,
          }))
          .catch(() => {});
      } catch (e) {
        setErr(e instanceof Error ? e.message : "查询失败");
      } finally {
        setLoading(false);
      }
    },
    [repoId, methodName]
  );

  // Auto-query when navigated from risk dashboard
  const lastInitialRef = useRef("");
  useEffect(() => {
    if (initialMethod && initialMethod !== lastInitialRef.current) {
      lastInitialRef.current = initialMethod;
      handleQuery(initialMethod);
    }
  }, [initialMethod, handleQuery]);

  return (
    <div className="space-y-5">
      <AnimatePresence>
        {varTracker && (
          <VariableTrackerModal
            repoId={repoId}
            methodName={varTracker.methodName}
            varName={varTracker.varName}
            onClose={() => setVarTracker(null)}
            onOpenSource={onOpenSource}
          />
        )}
      </AnimatePresence>

      {/* Method query input */}
      <div className="rounded-xl border border-outline-variant/15 bg-surface-container p-4">
        <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant/60 mb-3">
          输入函数名，跨函数分析调用链上下文与运行时风险
        </p>
        <div className="flex gap-2">
          <input
            value={methodName}
            onChange={(e) => setMethodName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleQuery();
            }}
            placeholder="e.g. handleRequest, validateInput..."
            className="flex-1 rounded-lg bg-surface-container-high border border-outline-variant/20 px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:border-primary/40 font-mono"
          />
          <button
            onClick={() => handleQuery()}
            disabled={loading || !methodName.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-primary/10 border border-primary/20 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-primary hover:bg-primary/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            查询
          </button>
        </div>
        {err && <p className="mt-2 text-xs text-tertiary">{err}</p>}
      </div>

      {/* ── Method Risk Summary Card (Phase B) ── */}
      {queried && methodMeta && (() => {
        const lines = Math.max(1, (methodMeta.lineEnd || methodMeta.line) - methodMeta.line + 1);
        const density = lines > 0 ? (methodMeta.complexity ?? 0) / lines : 0;
        const rl = riskLevel(methodMeta.complexity ?? 0, density);
        const score = (methodMeta.complexity ?? 0) * (1 + density);
        return (
          <div className={`rounded-xl border p-4 ${RISK_COLORS[rl]}`}>
            <div className="flex items-center gap-3 mb-3">
              <ShieldAlert size={16} />
              <span className="text-xs font-bold uppercase tracking-widest">{queriedMethod} — 风险概览</span>
              <span className={`ml-auto inline-flex px-2 py-0.5 rounded-full text-[9px] font-data font-bold uppercase tracking-wider border ${RISK_COLORS[rl]}`}>
                {rl}
              </span>
            </div>
            <div className="grid grid-cols-4 gap-4">
              {[
                { label: "圈复杂度", value: methodMeta.complexity ?? 0, icon: BarChart3 },
                { label: "代码行数", value: lines, icon: FileText },
                { label: "复杂度密度", value: density.toFixed(3), icon: TrendingUp },
                { label: "风险评分", value: score.toFixed(1), icon: AlertTriangle },
              ].map(({ label, value, icon: Icon }) => (
                <div key={label} className="flex items-center gap-2">
                  <Icon size={12} className="opacity-60" />
                  <div>
                    <p className="text-[9px] uppercase tracking-wider opacity-60">{label}</p>
                    <p className="text-sm font-mono font-bold">{value}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {queried && (
        <div className="space-y-6">
          {/* ── 控制流图 ── */}
          <Section title="控制流图 (CFG)" count={1} accent="primary">
            <CfgViewer
              repoId={repoId}
              methodName={queriedMethod}
              onMethodClick={handleQuery}
            />
          </Section>

          {/* ── 跨函数上下文 ── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <Section title="调用上下文（谁调用了此函数）" count={callContext.length} accent="primary">
              {callContext.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40 italic">无调用上下文 — 该函数可能是入口函数</p>
              ) : (
                <CallContextCards
                  items={callContext}
                  onOpenSource={onOpenSource}
                  onMethodClick={handleQuery}
                />
              )}
            </Section>
            <Section title="被调用影响（此函数调用了谁）" count={calleeImpact.length} accent="primary">
              {calleeImpact.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40 italic">无被调用函数 — 该函数是叶子函数</p>
              ) : (
                <CalleeImpactCards
                  items={calleeImpact}
                  onOpenSource={onOpenSource}
                  onMethodClick={handleQuery}
                />
              )}
            </Section>
          </div>

          {/* ── 模块影响面 (Phase F) ── */}
          {impactRadius && (impactRadius.callerFiles.length > 0 || impactRadius.moduleDeps.length > 0) && (
            <Section
              title={`模块影响面 — ${impactRadius.callerCount} 个调用者, ${impactRadius.moduleDeps.length} 条模块依赖`}
              count={impactRadius.callerFiles.length + impactRadius.moduleDeps.length}
              accent="primary"
            >
              <div className="space-y-3">
                {impactRadius.callerFiles.length > 0 && (
                  <div>
                    <p className="text-[10px] font-data uppercase tracking-widest text-on-surface-variant/50 mb-2">涉及文件</p>
                    <div className="flex flex-wrap gap-1.5">
                      {impactRadius.callerFiles.map((f) => (
                        <button
                          key={f}
                          onClick={() => onOpenSource(f)}
                          className="px-2 py-1 rounded-md bg-surface-container-high border border-outline-variant/10 text-[10px] font-mono text-on-surface-variant hover:border-primary/30 hover:text-primary transition-all"
                        >
                          {f.split("/").pop()}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {impactRadius.moduleDeps.length > 0 && (
                  <div>
                    <p className="text-[10px] font-data uppercase tracking-widest text-on-surface-variant/50 mb-2">模块级依赖链</p>
                    <div className="space-y-1">
                      {impactRadius.moduleDeps.slice(0, 10).map((dep, i) => (
                        <div key={i} className="flex items-center gap-2 text-[11px] font-mono text-on-surface-variant/70">
                          <span className="text-primary">{dep.source}</span>
                          <span className="text-on-surface-variant/30">--{dep.type}--&gt;</span>
                          <span className="text-secondary">{dep.target}</span>
                        </div>
                      ))}
                      {impactRadius.moduleDeps.length > 10 && (
                        <p className="text-[10px] text-on-surface-variant/40 italic">
                          ... 及其余 {impactRadius.moduleDeps.length - 10} 条依赖
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </Section>
          )}

          {/* ── 函数内部分析 ── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
            <Section title="控制流分支" count={branches.length} accent="primary">
              {branches.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40 italic">无控制流分支数据</p>
              ) : (
                <BranchCards items={branches} onOpenSource={onOpenSource} />
              )}
            </Section>
            <Section title="异常处理路径" count={errors.length} accent="tertiary">
              {errors.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40 italic">无异常处理路径</p>
              ) : (
                <ErrorPathCards items={errors} onOpenSource={onOpenSource} />
              )}
            </Section>
            <Section title="边界值比较" count={boundaries.length} accent="amber">
              {boundaries.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40 italic">无边界值比较</p>
              ) : (
                <BoundaryCards
                  methodName={queriedMethod}
                  items={boundaries}
                  onOpenSource={onOpenSource}
                  onVarClick={(m, v) => setVarTracker({ methodName: m, varName: v })}
                />
              )}
            </Section>
          </div>
        </div>
      )}

      {!queried && (
        <div className="flex flex-col items-center justify-center h-40 gap-2 text-on-surface-variant/30">
          <GitBranch size={32} className="opacity-30" />
          <p className="text-sm">输入函数名，分析跨函数调用链与运行时风险</p>
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  accent,
  children,
}: {
  title: string;
  count: number;
  accent?: "primary" | "tertiary" | "amber";
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const accentColor = { primary: "text-primary", tertiary: "text-tertiary", amber: "text-amber-400" }[accent ?? "primary"];
  const accentBg = { primary: "bg-primary", tertiary: "bg-tertiary", amber: "bg-amber-400" }[accent ?? "primary"];
  const accentBorder = { primary: "hover:border-primary/30", tertiary: "hover:border-tertiary/30", amber: "hover:border-amber-400/30" }[accent ?? "primary"];

  return (
    <div className={`group/section rounded-xl border border-outline-variant/15 bg-surface-container overflow-hidden transition-all duration-500 ${accentBorder} hover:shadow-2xl hover:shadow-black/40`}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 hover:bg-surface-container-high/40 transition-colors relative"
      >
        <div className={`absolute left-0 top-0 bottom-0 w-1 ${accentBg} opacity-0 group-hover/section:opacity-100 transition-opacity`} />
        <motion.div animate={{ rotate: open ? 0 : -90 }}>
          <ChevronDown size={14} className="text-on-surface-variant/50" />
        </motion.div>
        <span className="text-[11px] font-mono font-bold uppercase tracking-widest text-on-surface">{title}</span>
        <span className={`text-[10px] font-mono ml-1 ${accentColor}`}>({count})</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="border-t border-outline-variant/10 p-4 bg-surface-container-lowest/30 relative">
              {/* Kinetic depth background */}
              <div className="absolute inset-0 bg-gradient-to-b from-black/5 to-transparent pointer-events-none" />
              <div className="relative z-10">{children}</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── CFG Viewer ───────────────────────────────────────────────────────────
function CfgViewer({
  repoId,
  methodName,
  onMethodClick,
}: {
  repoId: string;
  methodName: string;
  onMethodClick: (n: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [svgHtml, setSvgHtml] = useState("");

  const loadCfg = useCallback(async () => {
    if (!methodName) return;
    setLoading(true);
    setError("");
    setSvgHtml("");
    try {
      const { dot } = await api.repos.analysis.joern.cfg(repoId, methodName);
      if (!dot || typeof dot !== "string") {
        setError("No CFG data returned");
        return;
      }
      const { Graphviz } = await import("@hpcc-js/wasm-graphviz");
      const graphviz = await Graphviz.load();
      const svg = graphviz.dot(dot, "svg");
      setSvgHtml(svg);
    } catch (e) {
      setError(e instanceof Error ? e.message : "CFG 加载失败");
    } finally {
      setLoading(false);
    }
  }, [repoId, methodName]);

  useEffect(() => {
    loadCfg();
  }, [loadCfg]);

  // Apply dark theme styling to SVG after render
  useEffect(() => {
    if (!containerRef.current || !svgHtml) return;
    const svg = containerRef.current.querySelector("svg");
    if (!svg) return;
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", "100%");
    svg.style.cursor = "grab";

    // Add filter for shadow
    const defs = svg.querySelector("defs") || svg.insertBefore(document.createElementNS("http://www.w3.org/2000/svg", "defs"), svg.firstChild);
    defs.innerHTML += `
      <filter id="kinetic-shadow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur in="SourceAlpha" stdDeviation="2" />
        <feOffset dx="0" dy="2" result="offsetblur" />
        <feComponentTransfer>
          <feFuncA type="linear" slope="0.3" />
        </feComponentTransfer>
        <feMerge>
          <feMergeNode />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    `;

    // Style nodes
    svg.querySelectorAll(".node").forEach((node) => {
      const g = node as SVGGElement;
      const rect = g.querySelector("polygon, ellipse, rect") as SVGElement | null;
      const text = g.querySelector("text") as SVGElement | null;
      const title = g.querySelector("title")?.textContent || "";

      if (rect) {
        const fill = rect.getAttribute("fill");
        if (fill === "white" || fill === "#ffffff" || fill === "none") {
          rect.setAttribute("fill", "rgba(164, 230, 255, 0.03)");
        }
        rect.setAttribute("stroke", "rgba(164, 230, 255, 0.2)");
        rect.setAttribute("stroke-width", "1");
        rect.style.filter = "url(#kinetic-shadow)";
        rect.style.transition = "all 0.3s ease";
      }

      if (text) {
        text.setAttribute("fill", "var(--on-surface)");
        text.style.fontFamily = "JetBrains Mono, monospace";
        text.style.fontSize = "10px";
        text.style.letterSpacing = "-0.02em";
        text.style.pointerEvents = "none";
      }

      // Interactivity
      g.style.cursor = "pointer";
      g.addEventListener("mouseenter", () => {
        if (rect) {
          rect.setAttribute("stroke", "var(--primary)");
          rect.setAttribute("stroke-width", "1.5");
          rect.setAttribute("fill", "rgba(164, 230, 255, 0.08)");
        }
      });
      g.addEventListener("mouseleave", () => {
        if (rect) {
          const isSpecial = title.includes("METHOD") || title.includes("RETURN");
          rect.setAttribute("stroke", isSpecial ? "var(--primary)" : "rgba(164, 230, 255, 0.2)");
          rect.setAttribute("stroke-width", isSpecial ? "2" : "1");
          rect.setAttribute("fill", isSpecial ? "rgba(164, 230, 255, 0.1)" : "rgba(164, 230, 255, 0.03)");
        }
      });

      g.addEventListener("click", (e) => {
        e.stopPropagation();
        // Try to find method call pattern: CALL, name(...)
        const codeText = text?.textContent || "";
        const callMatch = codeText.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\(/);
        if (callMatch) {
          onMethodClick(callMatch[1]);
        } else {
          // Fallback: search for filename and line in title (Joern often puts node IDs or info there)
          // For now, if we can't find a method, we try to open current file (we don't have filename in node usually)
          // But we can use the method's own filename if we had it.
        }
      });
    });

    svg.querySelectorAll("path").forEach((p) => {
      p.setAttribute("stroke", "var(--primary)");
      p.setAttribute("stroke-opacity", "0.4");
      p.setAttribute("stroke-width", "1");
    });

    // Arrow heads
    svg.querySelectorAll("polygon[stroke]").forEach((p) => {
      if (p.closest("g")?.querySelector("path")) {
        p.setAttribute("fill", "var(--primary)");
        p.setAttribute("fill-opacity", "0.4");
        p.setAttribute("stroke", "var(--primary)");
        p.setAttribute("stroke-opacity", "0.4");
      }
    });

    // Highlight special nodes
    svg.querySelectorAll("title").forEach((title) => {
      const text = title.textContent ?? "";
      const g = title.parentElement;
      if (!g) return;
      const rect = g.querySelector("polygon, ellipse, rect");
      if (text.includes("METHOD") && rect) {
        rect.setAttribute("fill", "rgba(164, 230, 255, 0.1)");
        rect.setAttribute("stroke", "var(--primary)");
        rect.setAttribute("stroke-opacity", "0.8");
        rect.setAttribute("stroke-width", "2");
      }
      if (text.includes("RETURN") && rect) {
        rect.setAttribute("fill", "rgba(255, 209, 205, 0.1)");
        rect.setAttribute("stroke", "var(--tertiary)");
        rect.setAttribute("stroke-opacity", "0.8");
        rect.setAttribute("stroke-width", "2");
      }
    });
  }, [svgHtml, onMethodClick]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[400px] gap-2 text-on-surface-variant/40 bg-surface-container-lowest/50 rounded-xl border border-dashed border-outline-variant/20">
        <Loader2 size={24} className="animate-spin text-primary/40" />
        <span className="text-xs font-data uppercase tracking-widest">生成控制流图…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-[400px] gap-3 text-tertiary bg-surface-container-lowest/50 rounded-xl border border-tertiary/20">
        <ShieldAlert size={32} className="opacity-40" />
        <p className="text-xs font-data">{error}</p>
      </div>
    );
  }
  if (!svgHtml) return null;

  return (
    <div className="relative group rounded-xl bg-surface-container-lowest border border-outline-variant/10 overflow-hidden h-[600px]">
      <TransformWrapper
        initialScale={1}
        minScale={0.1}
        maxScale={8}
        centerOnInit={true}
        limitToBounds={false}
      >
        {({ zoomIn, zoomOut, resetTransform, centerView }) => (
          <>
            {/* Controls */}
            <div className="absolute right-4 top-4 z-20 flex flex-col gap-2 p-1.5 rounded-2xl bg-surface-container-high/80 backdrop-blur-md border border-outline-variant/20 shadow-xl opacity-0 group-hover:opacity-100 transition-all transform translate-x-4 group-hover:translate-x-0">
              <button
                onClick={() => zoomIn()}
                className="p-2.5 rounded-xl hover:bg-primary/10 text-on-surface-variant/60 hover:text-primary transition-all"
                title="放大"
              >
                <ZoomIn size={18} />
              </button>
              <button
                onClick={() => zoomOut()}
                className="p-2.5 rounded-xl hover:bg-primary/10 text-on-surface-variant/60 hover:text-primary transition-all"
                title="缩小"
              >
                <ZoomOut size={18} />
              </button>
              <button
                onClick={() => resetTransform()}
                className="p-2.5 rounded-xl hover:bg-primary/10 text-on-surface-variant/60 hover:text-primary transition-all"
                title="重置"
              >
                <RefreshCw size={18} />
              </button>
              <div className="h-px bg-outline-variant/10 mx-2" />
              <button
                onClick={() => centerView()}
                className="p-2.5 rounded-xl hover:bg-primary/10 text-on-surface-variant/60 hover:text-primary transition-all"
                title="全屏自适应"
              >
                <Maximize size={18} />
              </button>
            </div>

            {/* Hint */}
            <div className="absolute left-4 bottom-4 z-20 pointer-events-none opacity-40 group-hover:opacity-100 transition-opacity">
              <span className="text-[10px] font-data text-on-surface-variant/30 uppercase tracking-widest bg-surface-container-low/50 px-3 py-1.5 rounded-full border border-outline-variant/5 backdrop-blur-sm">
                滚轮缩放 · 鼠标拖拽平移
              </span>
            </div>

            <TransformComponent
              wrapperStyle={{ width: "100%", height: "100%" }}
              contentStyle={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyItems: "center" }}
            >
              <div
                ref={containerRef}
                className="w-full h-full flex items-center justify-center p-8"
                dangerouslySetInnerHTML={{ __html: svgHtml }}
              />
            </TransformComponent>
          </>
        )}
      </TransformWrapper>
    </div>
  );
}

// ── Test Points sub-view ───────────────────────────────────────────────────
function TestPointsView({ repoId }: { repoId: string }) {
  const [target, setTarget] = useState("");
  const [testPoints, setTestPoints] = useState<TestPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [generated, setGenerated] = useState(false);


  const doExportMarkdown = () => {
    const md = testPoints.map((tp, i) =>
      `## TP-${String(i + 1).padStart(2, "0")}: ${tp.scenario}\n\n**风险等级**: ${tp.risk_level}\n\n**输入条件**: ${tp.input_conditions}\n\n**预期行为**: ${tp.expected_behavior}\n\n**风险场景**: ${tp.risk_scenario}\n\n${tp.boundary_values ? `**边界值**: ${tp.boundary_values}\n\n` : ""}`
    ).join("---\n\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "test-points.md"; a.click();
    URL.revokeObjectURL(url);
  };

  const doExportJSON = () => {
    const blob = new Blob([JSON.stringify(testPoints, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "test-points.json"; a.click();
    URL.revokeObjectURL(url);
  };

  const handleGenerate = async () => {
    setLoading(true);
    setErr("");
    try {
      const resp = await api.repos.analysis.testPoints.generate(
        repoId,
        target.trim() || undefined,
        "black_box"
      );
      setTestPoints(resp.test_points ?? []);
      setGenerated(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "生成失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-500">
      {/* Generate panel */}
      <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 space-y-4 transition-all hover:border-outline-variant/30">
        <div className="flex items-center gap-2">
          <FlaskConical size={14} className="text-secondary/60" />
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">联合分析引擎</h3>
        </div>
        <div className="flex gap-3">
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="目标函数（可选，默认全量分析）"
            className="flex-1 rounded-xl bg-surface-container border border-outline-variant/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-secondary/40 focus:ring-1 focus:ring-secondary/20 transition-all font-data"
          />
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl bg-secondary/5 border border-secondary/20 px-6 py-3 text-[11px] font-bold uppercase tracking-[0.2em] text-secondary hover:bg-secondary/10 transition-all disabled:opacity-40 active:scale-95"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            生成测试点
          </button>
        </div>
        {err && <p className="text-xs text-tertiary font-data">{err}</p>}
      </div>

      {/* Export controls */}
      {generated && testPoints.length > 0 && (
        <div className="flex items-center gap-3 px-2 animate-in fade-in duration-700">
          <span className="text-[10px] font-data text-on-surface-variant/40 tracking-widest">{testPoints.length} 个测试点</span>
          <div className="h-px flex-1 bg-outline-variant/10" />
          <div className="flex gap-2">
            <button onClick={doExportMarkdown} className="text-[10px] font-data uppercase tracking-widest text-on-surface-variant/60 hover:text-primary transition-colors flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-transparent hover:border-primary/20 hover:bg-primary/5">
              <Download size={12} /> Markdown
            </button>
            <button onClick={doExportJSON} className="text-[10px] font-data uppercase tracking-widest text-on-surface-variant/60 hover:text-primary transition-colors flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-transparent hover:border-primary/20 hover:bg-primary/5">
              <Download size={12} /> JSON
            </button>
          </div>
        </div>
      )}

      {/* Test point cards */}
      {testPoints.length > 0 ? (
        <TestPointCards testPoints={testPoints} />
      ) : (
        !loading && (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/20">
            <FlaskConical size={32} className="opacity-20" />
            <p className="text-[10px] font-data uppercase tracking-[0.2em]">{generated ? "未生成测试点" : "等待生成"}</p>
          </div>
        )
      )}
    </div>
  );
}

// ── Test Point Cards with pagination ──────────────────────────────────────
const TP_RISK_COLOR: Record<string, string> = {
  high: "text-tertiary bg-tertiary/10 border-tertiary/20 shadow-[0_0_12px_rgba(255,209,205,0.1)]",
  medium: "text-amber-400 bg-amber-400/10 border-amber-400/20 shadow-[0_0_12px_rgba(251,191,36,0.1)]",
  low: "text-secondary bg-secondary/10 border-secondary/20 shadow-[0_0_12px_rgba(236,255,227,0.1)]",
};

function TestPointCards({ testPoints }: { testPoints: TestPoint[] }) {
  const { page, setPage, totalPages, paged } = usePagination(testPoints);
  return (
    <div className="space-y-4">
      {paged.map((tp, i) => {
        const globalIdx = (page - 1) * PAGE_SIZE + i;
        return (
          <div key={tp.id ?? globalIdx} className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30 hover:shadow-xl overflow-hidden">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-secondary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
            <div className="flex items-start gap-4">
              <div className="flex-1 min-w-0 space-y-4">
                <div className="flex items-center gap-3">
                  <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest ${TP_RISK_COLOR[tp.risk_level] ?? TP_RISK_COLOR.low}`}>
                    {tp.risk_level}
                  </span>
                  <span className="text-[10px] font-data text-on-surface-variant/40 tracking-tighter uppercase">{tp.category}</span>
                  <div className="h-px flex-1 bg-outline-variant/5" />
                  <span className="text-[10px] font-data text-on-surface-variant/20 tracking-widest">TP-{String(globalIdx + 1).padStart(2, "0")}</span>
                </div>
                <h4 className="text-base font-display font-medium text-on-surface group-hover:text-secondary-fixed-dim transition-colors">{tp.scenario}</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1 h-1 rounded-full bg-primary/40" />
                      <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">输入条件</span>
                    </div>
                    <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.input_conditions}</p>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1 h-1 rounded-full bg-secondary/40" />
                      <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">预期行为</span>
                    </div>
                    <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.expected_behavior}</p>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <div className="w-1 h-1 rounded-full bg-tertiary/40" />
                      <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">风险场景</span>
                    </div>
                    <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.risk_scenario}</p>
                  </div>
                  {tp.boundary_values && (
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <div className="w-1 h-1 rounded-full bg-amber-400/40" />
                        <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">边界值</span>
                      </div>
                      <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.boundary_values}</p>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {tp.source_location && (
              <div className="mt-6 pt-4 border-t border-outline-variant/5 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <GitBranch size={10} className="text-on-surface-variant/20" />
                  <span className="text-[10px] font-data text-on-surface-variant/20 group-hover:text-on-surface-variant/40 transition-colors uppercase tracking-tight">{tp.source_location}</span>
                </div>
              </div>
            )}
          </div>
        );
      })}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

// ── Confidence scoring for taint results ──────────────────────────────────
/** Compute proximity score for a taint path (lower = higher confidence).
 *  Measures minimum line distance between any source and sink element. */
function computeProximity(path: TaintPath): number {
  const els = path.elements ?? [];
  const sources = els.filter(e => e.is_source === true);
  const sinks = els.filter(e => e.is_source === false);
  if (!sources.length || !sinks.length) return 9999;
  let minDist = Infinity;
  for (const src of sources) {
    for (const snk of sinks) {
      const sl = src.line_number ?? 0;
      const skl = snk.line_number ?? 0;
      // Prefer source before sink (positive distance)
      const dist = skl - sl;
      if (dist > 0 && dist < minDist) minDist = dist;
    }
  }
  return minDist === Infinity ? 9999 : minDist;
}

function confidenceLabel(proximity: number): { text: string; color: string } {
  if (proximity <= 10) return { text: "HIGH", color: "text-secondary bg-secondary/10 border-secondary/20" };
  if (proximity <= 30) return { text: "MED", color: "text-amber-400 bg-amber-400/10 border-amber-400/20" };
  return { text: "LOW", color: "text-on-surface-variant/40 bg-surface-container-high/30 border-outline-variant/10" };
}

// ── Taint sub-view ─────────────────────────────────────────────────────────
function TaintView({
  repoId,
  onOpenSource,
}: {
  repoId: string;
  onOpenSource: (p: string, l?: number) => void;
}) {
  const [source, setSource] = useState("");
  const [sink, setSink] = useState("");
  const [activeMode, setActiveMode] = useState<"cooccur" | "absence">("cooccur");
  const [loading, setLoading] = useState(false);
  const [paths, setPaths] = useState<TaintPath[]>([]);
  const [queried, setQueried] = useState(false);
  const [err, setErr] = useState("");

  const handleAnalyze = async () => {
    const s = source.trim();
    const sk = sink.trim();
    if (!s || !sk) return;
    setLoading(true);
    setErr("");
    try {
      const resp = await api.repos.analysis.joern.taint(repoId, s, sk, activeMode);
      // Sort by confidence: proximity between source and sink lines
      const sorted = [...(resp.paths ?? [])].sort((a, b) => {
        return computeProximity(a) - computeProximity(b);
      });
      setPaths(sorted);
      setQueried(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "分析失败");
    } finally {
      setLoading(false);
    }
  };

  type Preset = { label: string; source: string; sink: string; mode?: "cooccur" | "absence"; hint: string };

  const PRESETS: Preset[] = [
    {
      label: "数值溢出",
      source: ".*scanf.*|.*fgets.*|.*recv.*|.*atoi.*|.*strtol.*|.*strtoul.*",
      sink: ".*<operator>.addition.*|.*<operator>.multiplication.*|.*<operator>.shiftLeft.*",
      hint: "外部输入参与算术运算，可能溢出",
    },
    {
      label: "空指针",
      source: ".*malloc.*|.*calloc.*|.*realloc.*|.*strdup.*",
      sink: ".*<operator>.indirection.*|.*memcpy.*|.*strcpy.*|.*memset.*",
      hint: "分配返回值未检查即解引用",
    },
    {
      label: "边界越界",
      source: ".*recv.*|.*fread.*|.*fgets.*|.*strlen.*",
      sink: ".*<operator>.indexAccess.*|.*memcpy.*|.*strncpy.*|.*sprintf.*",
      hint: "外部输入控制数组索引或拷贝长度",
    },
    {
      label: "资源泄漏",
      source: "(open|fopen|fdopen|opendir|socket|accept|dup|dup2|pipe|malloc|calloc|realloc|mmap)",
      sink: "(close|fclose|closedir|shutdown|free|munmap)",
      mode: "absence",
      hint: "疑似获取资源但函数作用域内未见对应释放（可能经调用方释放）",
    },
    {
      label: "数值翻转",
      source: ".*atoi.*|.*strtol.*|.*strtoul.*|.*scanf.*|.*recv.*",
      sink: ".*<operator>.minus.*|.*<operator>.not.*|.*<operator>.negation.*",
      hint: "外部数值经取反/符号翻转操作",
    },
  ];

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Input panel */}
      <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 space-y-6 transition-all hover:border-outline-variant/30">
        <div className="flex items-center gap-2">
          <Network size={14} className="text-primary/60" />
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">传播分析</h3>
        </div>

        {/* Presets */}
        <div className="flex gap-2 flex-wrap">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => { setSource(p.source); setSink(p.sink); setActiveMode(p.mode ?? "cooccur"); }}
              className="group/preset text-[10px] font-data px-3 py-1.5 rounded-full border border-outline-variant/20 text-on-surface-variant/50 hover:border-primary/40 hover:text-primary transition-all bg-surface-container/50 hover:bg-primary/5 active:scale-95"
            >
              <span className="tracking-widest uppercase">{p.label}</span>
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="text-[9px] font-data uppercase tracking-[0.3em] text-secondary/50 ml-1">源模式</label>
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="e.g. getParameter"
              className="w-full rounded-xl bg-surface-container border border-secondary/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-secondary/40 focus:ring-1 focus:ring-secondary/10 transition-all font-data shadow-inner"
            />
          </div>
          <div className="space-y-2">
            <label className="text-[9px] font-data uppercase tracking-[0.3em] text-tertiary/50 ml-1">汇模式</label>
            <input
              value={sink}
              onChange={(e) => setSink(e.target.value)}
              placeholder="e.g. executeQuery"
              className="w-full rounded-xl bg-surface-container border border-tertiary/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-tertiary/40 focus:ring-1 focus:ring-tertiary/10 transition-all font-data shadow-inner"
            />
          </div>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={handleAnalyze}
            disabled={loading || !source.trim() || !sink.trim()}
            className="inline-flex items-center gap-2 rounded-xl bg-primary/5 border border-primary/20 px-8 py-3 text-[11px] font-bold uppercase tracking-[0.2em] text-primary hover:bg-primary/10 transition-all disabled:opacity-40 active:scale-95"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Network size={14} />}
            {activeMode === "absence" ? "检测缺失" : "追踪数据流"}
          </button>
          {activeMode === "absence" && (
            <span className="text-[9px] font-data text-amber-400/60 uppercase tracking-widest">缺失检测模式：查找有源无汇的函数</span>
          )}
          {err && <span className="text-xs text-tertiary font-data">{err}</span>}
        </div>
      </div>

      {/* Results */}
      {queried && (
        <div className="space-y-4 animate-in fade-in duration-700">
          <div className="flex items-center gap-3 px-2">
            <span className="text-xl font-display font-bold text-on-surface">{paths.length}</span>
            <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mt-1">
              {activeMode === "absence" ? "个疑似泄漏点（需人工确认）" : "条共现路径（按置信度排序）"}
            </span>
          </div>
          {activeMode === "cooccur" && paths.length > 0 && (
            <p className="text-[10px] text-on-surface-variant/40 px-2 -mt-2">
              基于方法共现检测，非真实数据流。置信度由 source→sink 行距计算：HIGH &le;10行, MED &le;30行, LOW &gt;30行。
            </p>
          )}
          {activeMode === "absence" && paths.length > 0 && (
            <p className="text-[10px] text-on-surface-variant/40 px-2 -mt-2">
              基于函数作用域内缺失检测：发现 source 调用但未发现对应 sink。资源可能经返回值或参数转交调用方释放，请结合上下文人工确认。
            </p>
          )}
          {paths.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-on-surface-variant/20 rounded-2xl border border-dashed border-outline-variant/10">
              <CheckCircle2 size={32} className="opacity-30 text-secondary" />
              <p className="text-[10px] font-data uppercase tracking-[0.2em]">未检测到异常传播</p>
            </div>
          ) : (
            <TaintPathCards repoId={repoId} paths={paths} source={source} sink={sink} activeMode={activeMode} onOpenSource={onOpenSource} />
          )}
        </div>
      )}

      {!queried && (
        <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/20">
          <Network size={32} className="opacity-20" />
          <p className="text-[10px] font-data uppercase tracking-[0.2em]">追踪跨函数异常数据传播</p>
        </div>
      )}
    </div>
  );
}

// ── Taint Path Cards with pagination + verify ────────────────────────────
function TaintPathCards({
  repoId,
  paths,
  source,
  sink,
  activeMode,
  onOpenSource,
}: {
  repoId: string;
  paths: TaintPath[];
  source: string;
  sink: string;
  activeMode: "cooccur" | "absence";
  onOpenSource: (p: string, l?: number) => void;
}) {
  const { page, setPage, totalPages, paged } = usePagination(paths);
  const [verifyState, setVerifyState] = useState<Record<string, "pending" | "verified" | "unverified" | "timeout">>({});
  const [verifyingKey, setVerifyingKey] = useState<string | null>(null);

  const handleVerify = async (path: TaintPath, key: string) => {
    if (!path.method || verifyState[key]) return;
    setVerifyingKey(key);
    try {
      const res = await api.repos.analysis.joern.taintVerify(repoId, path.method, source, sink);
      setVerifyState(prev => ({ ...prev, [key]: res.verified ? "verified" : res.fallback === "timeout" ? "timeout" : "unverified" }));
    } catch {
      setVerifyState(prev => ({ ...prev, [key]: "unverified" }));
    } finally {
      setVerifyingKey(null);
    }
  };

  return (
    <div className="grid gap-4">
      {paged.map((path, i) => {
        const globalIdx = (page - 1) * PAGE_SIZE + i;
        const pathKey = `${path.method}-${globalIdx}`;
        const prox = computeProximity(path);
        const conf = confidenceLabel(prox);
        const vs = verifyState[pathKey];
        return (
          <motion.div
            layout
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            key={globalIdx}
            className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30 hover:shadow-xl"
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-primary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
            <div className="flex justify-between items-center mb-6">
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">传播路径 {globalIdx + 1}</span>
                {path.method && (
                  <span className="text-[10px] font-data text-primary/60 tracking-tight">{path.method}()</span>
                )}
                {prox < 9999 && (
                  <span className={`text-[8px] font-data font-bold px-2 py-0.5 rounded-full border ${conf.color}`}>
                    {conf.text}
                  </span>
                )}
                {/* Verify badge */}
                {vs === "verified" && (
                  <span className="text-[8px] font-data font-bold px-2 py-0.5 rounded-full border border-secondary/30 bg-secondary/10 text-secondary">✓ 已验证</span>
                )}
                {vs === "unverified" && (
                  <span className="text-[8px] font-data font-bold px-2 py-0.5 rounded-full border border-outline-variant/20 bg-surface-container-high/30 text-on-surface-variant/40">✗ 未验证</span>
                )}
                {vs === "timeout" && (
                  <span className="text-[8px] font-data font-bold px-2 py-0.5 rounded-full border border-amber-400/20 bg-amber-400/10 text-amber-400">⏱ 超时</span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {/* Verify button — only for co-occur mode, when method is known */}
                {activeMode === "cooccur" && path.method && !vs && (
                  <button
                    onClick={(e) => { e.stopPropagation(); handleVerify(path, pathKey); }}
                    disabled={verifyingKey === pathKey}
                    className="text-[9px] font-data px-2 py-1 rounded-lg border border-primary/20 text-primary/60 hover:bg-primary/5 hover:text-primary transition-all disabled:opacity-40"
                  >
                    {verifyingKey === pathKey ? <Loader2 size={10} className="animate-spin inline" /> : "验证数据流"}
                  </button>
                )}
                {prox < 9999 && <span className="text-[9px] font-data text-on-surface-variant/20">{prox}行距</span>}
                {path.file && <span className="text-[9px] font-data text-on-surface-variant/20 tracking-tight">{shortPath(path.file)}</span>}
                <span className="text-[10px] font-data text-on-surface-variant/20 tracking-widest">{path.elements?.length} 个节点</span>
              </div>
            </div>
            <div className="space-y-0.5 relative">
              <div className="absolute left-2 top-2 bottom-2 w-px bg-outline-variant/10" />
              {(path.elements ?? []).map((el, j) => {
                const isSource = el.is_source === true || (el.is_source === undefined && j === 0);
                const isSink = el.is_source === false || (el.is_source === undefined && j === (path.elements.length - 1));
                return (
                  <motion.div
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: j * 0.05 }}
                    key={j}
                    className="flex items-start gap-4 py-2 relative group/node"
                  >
                    <div className={`mt-1.5 w-4 h-4 rounded-full border flex items-center justify-center z-10 transition-all ${
                      isSource ? "bg-secondary/20 border-secondary/40" :
                      isSink ? "bg-tertiary/20 border-tertiary/40" :
                      "bg-surface-container-high border-outline-variant/20 group-hover/node:border-outline-variant/40"
                    }`}>
                      <div className={`w-1 h-1 rounded-full ${
                        isSource ? "bg-secondary shadow-[0_0_8px_var(--color-secondary)]" :
                        isSink ? "bg-tertiary shadow-[0_0_8px_var(--color-tertiary)]" :
                        "bg-on-surface-variant/20"
                      }`} />
                    </div>
                    <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <span className={`text-[12px] font-data transition-colors ${
                        isSource ? "text-secondary/80 font-bold" :
                        isSink ? "text-tertiary/80 font-bold" :
                        "text-on-surface-variant/70 group-hover/node:text-on-surface"
                      }`}>
                        {el.code}
                      </span>
                      <div className="flex items-center gap-2">
                        <CodeLink filePath={el.filename ?? ""} line={el.line_number ?? undefined} onOpen={onOpenSource} />
                      </div>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        );
      })}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

// ── Zoekt Pattern Search ──────────────────────────────────────────────────
type SearchResult = { file: string; matches: { line_number: number; line_content: string }[] };

const SEARCH_PRESETS = [
  { label: "未检查的 malloc", query: "malloc\\(.*(?!.*if.*NULL)", hint: "分配内存后可能未做空指针检查" },
  { label: "硬编码凭证", query: "(password|secret|token|key)\\s*=\\s*[\"']", hint: "代码中可能存在硬编码的敏感信息" },
  { label: "危险字符串函数", query: "\\b(strcpy|strcat|sprintf|gets)\\s*\\(", hint: "无长度限制的字符串操作，可能导致缓冲区溢出" },
  { label: "未处理的返回值", query: "^\\s+(open|fopen|malloc|socket)\\(", hint: "行首调用资源获取函数但可能未赋值检查" },
  { label: "TODO/FIXME", query: "(TODO|FIXME|HACK|XXX|BUG)", hint: "开发者标记的待修复问题" },
];

function PatternSearchView({
  repoId,
  onOpenSource,
}: {
  repoId: string;
  onOpenSource: (p: string, l?: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [totalMatches, setTotalMatches] = useState(0);
  const [searched, setSearched] = useState(false);
  const [err, setErr] = useState("");

  const handleSearch = async (q?: string) => {
    const searchQuery = (q || query).trim();
    if (!searchQuery) return;
    if (q) setQuery(searchQuery);
    setLoading(true);
    setErr("");
    try {
      const res = await api.repos.search(repoId, searchQuery);
      setResults(res.results ?? []);
      setTotalMatches(res.total_matches ?? 0);
      setSearched(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "搜索失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Search input */}
      <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 space-y-5 transition-all hover:border-outline-variant/30">
        <div className="flex items-center gap-2">
          <Search size={14} className="text-primary/60" />
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">代码模式搜索 (Zoekt)</h3>
        </div>

        {/* Presets */}
        <div className="flex gap-2 flex-wrap">
          {SEARCH_PRESETS.map(p => (
            <button
              key={p.label}
              onClick={() => handleSearch(p.query)}
              title={p.hint}
              className="group/preset text-[10px] font-data px-3 py-1.5 rounded-full border border-outline-variant/20 text-on-surface-variant/50 hover:border-primary/40 hover:text-primary transition-all bg-surface-container/50 hover:bg-primary/5 active:scale-95"
            >
              <span className="tracking-widest uppercase">{p.label}</span>
            </button>
          ))}
        </div>

        {/* Input + button */}
        <div className="flex gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="正则表达式搜索..."
            className="flex-1 rounded-xl bg-surface-container border border-primary/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-primary/40 focus:ring-1 focus:ring-primary/10 transition-all font-data shadow-inner"
          />
          <button
            onClick={() => handleSearch()}
            disabled={loading || !query.trim()}
            className="inline-flex items-center gap-2 rounded-xl bg-primary/5 border border-primary/20 px-6 py-3 text-[11px] font-bold uppercase tracking-[0.2em] text-primary hover:bg-primary/10 transition-all disabled:opacity-40 active:scale-95"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            搜索
          </button>
        </div>
        {err && <span className="text-xs text-tertiary font-data">{err}</span>}
      </div>

      {/* Results */}
      {searched && (
        <div className="space-y-4 animate-in fade-in duration-700">
          <div className="flex items-center gap-3 px-2">
            <span className="text-xl font-display font-bold text-on-surface">{totalMatches}</span>
            <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mt-1">
              处匹配 · {results.length} 个文件
            </span>
          </div>

          {results.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-on-surface-variant/20 rounded-2xl border border-dashed border-outline-variant/10">
              <CheckCircle2 size={32} className="opacity-30 text-secondary" />
              <p className="text-[10px] font-data uppercase tracking-[0.2em]">未找到匹配</p>
            </div>
          ) : (
            <div className="space-y-3">
              {results.slice(0, 20).map((file) => (
                <div key={file.file} className="rounded-xl border border-outline-variant/10 bg-surface-container-low overflow-hidden">
                  <div className="px-4 py-2 bg-surface-container-low/80 border-b border-outline-variant/5 flex items-center justify-between">
                    <span className="text-[11px] font-data text-on-surface/80 tracking-tight">{shortPath(file.file)}</span>
                    <span className="text-[9px] font-data text-on-surface-variant/30 uppercase tracking-widest">{file.matches.length} 处</span>
                  </div>
                  <div className="divide-y divide-outline-variant/5">
                    {file.matches.slice(0, 10).map((m, mi) => (
                      <div key={mi} className="px-4 py-2 flex items-center gap-3 hover:bg-surface-container-high/20 transition-colors">
                        <button
                          onClick={() => onOpenSource(file.file, m.line_number)}
                          className="text-[10px] font-data text-primary/50 hover:text-primary transition-colors min-w-[3rem] text-right"
                        >
                          :{m.line_number}
                        </button>
                        <code className="text-[11px] font-data text-on-surface-variant/70 truncate flex-1">{m.line_content}</code>
                      </div>
                    ))}
                    {file.matches.length > 10 && (
                      <div className="px-4 py-2 text-[9px] font-data text-on-surface-variant/30 uppercase tracking-widest">
                        ... 还有 {file.matches.length - 10} 处匹配
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!searched && (
        <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/20">
          <Search size={32} className="opacity-20" />
          <p className="text-[10px] font-data uppercase tracking-[0.2em]">使用正则表达式搜索代码模式</p>
        </div>
      )}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────
export default function AnalysisPage() {
  const params = useParams();
  const repoId = params.repoId as string;

  const [activeNav, setActiveNav] = useState<NavId>("overview");
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [repoName, setRepoName] = useState("");
  const [initialMethod, setInitialMethod] = useState("");

  // Source viewer state
  const [sourceModal, setSourceModal] = useState<{ path: string; line?: number } | null>(null);

  const navigateToMethod = useCallback((method: string) => {
    setInitialMethod(method);
    setActiveNav("branches");
  }, []);

  const exportCsv = useCallback((data: EnrichedMethod[]) => {
    const header = "函数,文件,行,复杂度,密度,行数,风险等级,风险分";
    const rows = data.map(m =>
      `"${m.name}","${m.filename}",${m.line},${m.complexity ?? 0},${m.density.toFixed(3)},${m.lines},${m.riskLevel},${m.riskScore.toFixed(2)}`
    );
    const csv = [header, ...rows].join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `risk-matrix-${repoName || repoId}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [repoName, repoId]);

  useEffect(() => {
    api.repos.get(repoId).then((d) => setRepoName(d.repo.name)).catch(() => {});
  }, [repoId]);

  useEffect(() => {
    api.repos.analysis.summary(repoId).then(setSummary).catch((e) => {
      setLoadError(e instanceof Error ? e.message : "分析摘要加载失败");
    });
  }, [repoId]);

  const handleRebuild = useCallback(async () => {
    setRebuilding(true);
    setLoadError("");
    try {
      await api.repos.analysis.joern.rebuild(repoId);
      const s = await api.repos.analysis.summary(repoId);
      setSummary(s);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "CPG 重建失败");
    } finally {
      setRebuilding(false);
    }
  }, [repoId]);

  return (
    <main className="h-full bg-surface-container-lowest text-on-surface flex flex-col selection:bg-primary/20">
      <AnimatePresence>
        {sourceModal && (
          <SourceViewerModal
            repoId={repoId}
            filePath={sourceModal.path}
            line={sourceModal.line}
            onClose={() => setSourceModal(null)}
          />
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="h-16 shrink-0 border-b border-outline-variant/10 px-6 flex items-center justify-between bg-surface-container-lowest/80 backdrop-blur-md sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <Link href={`/repos/${repoId}`} className="p-2 rounded-full hover:bg-surface-container-high text-on-surface-variant transition-colors">
            <ArrowLeft size={18} />
          </Link>
          <div className="flex flex-col">
            <h1 className="text-sm font-display font-bold tracking-tight text-on-surface">{repoName}</h1>
            <span className="text-[10px] font-data text-on-surface-variant/40 uppercase tracking-[0.2em]">Repository Analysis Center</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleRebuild}
            disabled={rebuilding}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary/5 border border-primary/20 text-[11px] font-bold uppercase tracking-widest text-primary hover:bg-primary/10 transition-all disabled:opacity-40"
          >
            <RefreshCw size={14} className={rebuilding ? "animate-spin" : ""} />
            {rebuilding ? "重建 CPG..." : "重新构建索引"}
          </button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar Nav */}
        <nav className="w-20 shrink-0 border-r border-outline-variant/5 flex flex-col items-center py-8 gap-8 bg-surface-container-low/30">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activeNav === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveNav(item.id)}
                className={`relative p-3 rounded-2xl transition-all group ${
                  active ? "bg-primary/10 text-primary" : "text-on-surface-variant/30 hover:text-on-surface-variant/60"
                }`}
              >
                {active && (
                  <motion.div
                    layoutId="active-nav"
                    className="absolute inset-0 rounded-2xl border border-primary/20"
                    transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
                  />
                )}
                <Icon size={20} />
                <span className="absolute left-full ml-4 px-2 py-1 rounded bg-surface-container-high border border-outline-variant/10 text-[10px] font-data font-bold uppercase tracking-widest text-on-surface opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50">
                  {item.label}
                </span>
              </button>
            );
          })}
        </nav>

        {/* Content Area */}
        <div className="flex-1 overflow-auto custom-scrollbar bg-gradient-to-br from-surface-container-lowest via-surface-container-lowest to-surface-container-low/20">
          <div className="max-w-6xl mx-auto px-10 py-12">
            {loadError && (
              <div className="mb-8 p-4 rounded-xl bg-tertiary/10 border border-tertiary/20 text-tertiary text-sm font-data">
                {loadError}
              </div>
            )}

            <motion.div
              key={activeNav}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.4, ease: "easeOut" }}
            >
              {activeNav === "overview" && (
                <RiskDashboardView repoId={repoId} summary={summary} onNavigate={navigateToMethod} onExport={exportCsv} />
              )}
              {activeNav === "branches" && (
                <BranchesView repoId={repoId} initialMethod={initialMethod} onOpenSource={(path, line) => setSourceModal({ path, line })} />
              )}
              {activeNav === "testpoints" && <TestPointsView repoId={repoId} />}
              {activeNav === "taint" && (
                <TaintView repoId={repoId} onOpenSource={(path, line) => setSourceModal({ path, line })} />
              )}
              {activeNav === "complexity" && (
                <ComplexityView repoId={repoId} onOpenSource={(path, line) => setSourceModal({ path, line })} />
              )}
              {activeNav === "search" && (
                <PatternSearchView repoId={repoId} onOpenSource={(path, line) => setSourceModal({ path, line })} />
              )}
            </motion.div>
          </div>
        </div>
      </div>
    </main>
  );
}

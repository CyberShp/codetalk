"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type {
  AnalysisSummary,
  TestPoint,
  TaintPath,
  JoernMethodBranch,
  JoernErrorPath,
  JoernBoundaryValue,
  JoernCallContext,
  JoernCalleeImpact,
} from "@/lib/types";
import {
  ArrowLeft,
  RefreshCw,
  Download,
  GitBranch,
  FlaskConical,
  Network,
  LayoutDashboard,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  Play,
  Loader2,
  CheckCircle2,
} from "lucide-react";

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

  // Build visible page numbers: always show first, last, current ± 1
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
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  // Reset to page 1 when items change
  const safePage = Math.min(page, totalPages);
  const paged = items.slice((safePage - 1) * pageSize, safePage * pageSize);
  return { page: safePage, setPage, totalPages, paged };
}

// ── Nav items ──────────────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: "overview", label: "概览", icon: LayoutDashboard },
  { id: "branches", label: "分支分析", icon: GitBranch },
  { id: "testpoints", label: "测试点", icon: FlaskConical },
  { id: "taint", label: "数据追踪", icon: Network },
] as const;
type NavId = (typeof NAV_ITEMS)[number]["id"];

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

function OverviewView({
  summary,
}: {
  summary: AnalysisSummary | null;
}) {
  const joernHealthy = summary?.tools.joern.healthy ?? false;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Tool status bar */}
      <div className="flex items-center gap-3 rounded-xl border border-outline-variant/10 bg-surface-container-low px-4 py-3">
        <ToolStatusDot healthy={joernHealthy} label="Joern CPG" />
      </div>

      {/* Joern CPG engine card */}
      <div className="relative group rounded-2xl border border-outline-variant/10 bg-surface-container-lowest p-6 overflow-hidden">
        <div className="absolute right-0 top-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] transition-opacity pointer-events-none">
          <GitBranch size={120} />
        </div>
        <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40 mb-5">CPG 智能引擎</h3>
        <div className="grid grid-cols-2 gap-8">
          <div className="space-y-1">
            <span className="text-on-surface-variant/40 text-[10px] font-data uppercase tracking-wider">引擎状态</span>
            <p className={`font-data text-sm font-bold ${joernHealthy ? "text-secondary" : "text-tertiary"}`}>
              {joernHealthy ? "在线" : "离线"}
            </p>
          </div>
          <div className="space-y-1">
            <span className="text-on-surface-variant/40 text-[10px] font-data uppercase tracking-wider">分析能力</span>
            <p className="font-data text-xs text-on-surface-variant/60 leading-relaxed">
              {summary?.tools.joern.capabilities.join(", ") ?? "—"}
            </p>
          </div>
        </div>
        <p className="mt-4 text-[10px] font-ui text-on-surface-variant/30">
          使用「分支分析」查询跨函数控制流 · 使用「数据追踪」追踪异常数据传播
        </p>
      </div>

      {/* Analysis guide */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {([
          { icon: GitBranch, title: "分支分析", desc: "输入函数名，跨函数分析调用链上下文、异常分支、边界值。查看上游调用者如何影响下游分支走向。", hoverBorder: "hover:border-primary/20", iconColor: "text-primary/60" },
          { icon: FlaskConical, title: "测试点生成", desc: "联合 Joern CPG 分析 + AI 生成运行时风险测试点。覆盖边界值、异常输入、极端场景。", hoverBorder: "hover:border-secondary/20", iconColor: "text-secondary/60" },
          { icon: Network, title: "数据追踪", desc: "追踪跨函数异常数据传播。预设模式：数值溢出、空指针、边界越界、资源泄漏。", hoverBorder: "hover:border-primary/20", iconColor: "text-primary/60" },
        ] as const).map(({ icon: Icon, title, desc, hoverBorder, iconColor }) => (
          <div key={title} className={`rounded-xl border border-outline-variant/10 bg-surface-container-low p-5 space-y-3 ${hoverBorder} transition-all`}>
            <div className="flex items-center gap-2">
              <Icon size={14} className={iconColor} />
              <span className="text-[11px] font-data font-bold uppercase tracking-wider text-on-surface">{title}</span>
            </div>
            <p className="text-[12px] font-ui text-on-surface-variant/60 leading-relaxed">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}


// ── Structured renderers for Joern results ───────────────────────────────

function CallContextCards({ items }: { items: JoernCallContext[] }) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      {paged.map((ctx, i) => (
        <div key={`${ctx.caller}-${i}`} className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-3 hover:border-primary/20 transition-all">
          <div className="flex items-center justify-between">
            <span className="font-data text-sm font-bold text-on-surface">{ctx.caller}</span>
            <span className="text-[10px] font-data text-on-surface-variant/30">{shortPath(ctx.callerFile)}:{ctx.callerLine}</span>
          </div>
          {ctx.callSites?.length > 0 && (
            <div className="space-y-1">
              <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40">调用位置</span>
              {ctx.callSites.map((site, j) => (
                <div key={j} className="flex items-center gap-2 text-[11px] font-data">
                  <span className="text-primary/50">L{site.line}</span>
                  <span className="text-on-surface-variant/60">{site.args?.join(", ") || "—"}</span>
                </div>
              ))}
            </div>
          )}
          {ctx.callerBranches?.length > 0 && (
            <div className="space-y-1 pt-1 border-t border-outline-variant/5">
              <span className="text-[9px] font-data uppercase tracking-[0.2em] text-tertiary/50">调用者分支（影响下游走向）</span>
              {ctx.callerBranches.map((br, j) => (
                <div key={j} className="flex items-start gap-2 text-[11px] font-data">
                  <span className="text-tertiary/40 shrink-0 uppercase text-[9px] mt-0.5">{br.type}</span>
                  <code className="text-on-surface-variant/70 break-all">{br.condition}</code>
                  <span className="text-on-surface-variant/30 shrink-0 ml-auto">L{br.line}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function CalleeImpactCards({ items }: { items: JoernCalleeImpact[] }) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      {paged.map((imp, i) => (
        <div key={`${imp.callee}-${i}`} className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-3 hover:border-primary/20 transition-all">
          <div className="flex items-center justify-between">
            <span className="font-data text-sm font-bold text-on-surface">{imp.callee}</span>
            <span className="text-[10px] font-data text-on-surface-variant/30">{shortPath(imp.calleeFile)}:{imp.calleeLine}</span>
          </div>
          {imp.callSitesInTarget?.length > 0 && (
            <div className="space-y-1">
              <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40">调用点</span>
              {imp.callSitesInTarget.map((site, j) => (
                <code key={j} className="block text-[11px] font-data text-on-surface-variant/60 pl-2 border-l border-outline-variant/10">
                  L{site.line}: {site.code}
                </code>
              ))}
            </div>
          )}
          {imp.errorReturns?.length > 0 && (
            <div className="space-y-1 pt-1 border-t border-outline-variant/5">
              <span className="text-[9px] font-data uppercase tracking-[0.2em] text-tertiary/50">被调用方异常返回</span>
              {imp.errorReturns.map((er, j) => (
                <div key={j} className="flex items-start gap-2 text-[11px] font-data">
                  <span className="text-tertiary/40 shrink-0">L{er.line}</span>
                  <code className="text-on-surface-variant/70 break-all">{er.code}</code>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function BranchCards({ items }: { items: JoernMethodBranch[] }) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  const TYPE_LABEL: Record<string, string> = { IfStatement: "IF", ElseStatement: "ELSE", SwitchStatement: "SWITCH", ForStatement: "FOR", WhileStatement: "WHILE", DoStatement: "DO", TryStatement: "TRY" };
  return (
    <div className="space-y-3">
      {paged.map((br, i) => (
        <div key={i} className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-2 hover:border-primary/20 transition-all">
          <div className="flex items-center gap-3">
            <span className="text-[9px] font-data font-bold uppercase tracking-widest px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
              {TYPE_LABEL[br.control_structure_type] ?? br.control_structure_type}
            </span>
            <span className="text-[10px] font-data text-on-surface-variant/30">
              {shortPath(br.filename)}{br.line_number ? `:${br.line_number}` : ""}
            </span>
          </div>
          {br.condition && (
            <code className="block text-[12px] font-data text-on-surface/80 pl-3 border-l-2 border-primary/20">{br.condition}</code>
          )}
          {br.children?.length > 0 && (
            <div className="space-y-1 pt-1">
              {br.children.map((child, j) => (
                <div key={j} className="flex items-start gap-2 text-[11px] font-data pl-3">
                  <span className="text-on-surface-variant/30 shrink-0 uppercase text-[9px] mt-0.5">{child.label}</span>
                  <code className="text-on-surface-variant/60 break-all">{child.code}</code>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function ErrorPathCards({ items }: { items: JoernErrorPath[] }) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  const KIND_STYLE: Record<string, string> = {
    throw: "bg-tertiary/10 text-tertiary border-tertiary/20",
    "try-catch": "bg-amber-400/10 text-amber-400 border-amber-400/20",
    "error-return": "bg-primary/10 text-primary border-primary/20",
  };
  return (
    <div className="space-y-3">
      {paged.map((ep, i) => (
        <div key={i} className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 hover:border-tertiary/20 transition-all">
          <div className="flex items-start gap-3">
            <span className={`text-[9px] font-data font-bold uppercase tracking-widest px-2 py-0.5 rounded-full border shrink-0 ${KIND_STYLE[ep.kind] ?? KIND_STYLE["error-return"]}`}>
              {ep.kind}
            </span>
            <code className="text-[12px] font-data text-on-surface/80 break-all flex-1">{ep.code}</code>
            <span className="text-[10px] font-data text-on-surface-variant/30 shrink-0">
              {shortPath(ep.filename)}{ep.line_number ? `:${ep.line_number}` : ""}
            </span>
          </div>
        </div>
      ))}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

function BoundaryCards({ items }: { items: JoernBoundaryValue[] }) {
  const { page, setPage, totalPages, paged } = usePagination(items);
  return (
    <div className="space-y-3">
      {paged.map((bv, i) => (
        <div key={i} className="group rounded-xl border border-outline-variant/10 bg-surface-container-lowest p-4 space-y-2 hover:border-amber-400/20 transition-all">
          <div className="flex items-center justify-between">
            <code className="text-[12px] font-data text-on-surface/80">{bv.code}</code>
            <span className="text-[10px] font-data text-on-surface-variant/30 shrink-0">
              {shortPath(bv.filename)}{bv.line_number ? `:${bv.line_number}` : ""}
            </span>
          </div>
          {bv.operands?.length > 0 && (
            <div className="flex gap-2 flex-wrap pt-1">
              {bv.operands.map((op, j) => (
                <span key={j} className="text-[10px] font-data px-2 py-0.5 rounded-full bg-amber-400/5 border border-amber-400/10 text-on-surface-variant/60">
                  <span className="text-amber-400/40 uppercase text-[8px] mr-1">{op.type}</span>{op.code}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      <Pagination current={page} total={totalPages} onChange={setPage} />
    </div>
  );
}

// ── Branches sub-view ──────────────────────────────────────────────────────
function BranchesView({ repoId }: { repoId: string }) {
  const [methodName, setMethodName] = useState("");
  const [loading, setLoading] = useState(false);
  const [branches, setBranches] = useState<JoernMethodBranch[]>([]);
  const [errors, setErrors] = useState<JoernErrorPath[]>([]);
  const [boundaries, setBoundaries] = useState<JoernBoundaryValue[]>([]);
  const [callContext, setCallContext] = useState<JoernCallContext[]>([]);
  const [calleeImpact, setCalleeImpact] = useState<JoernCalleeImpact[]>([]);
  const [queried, setQueried] = useState(false);
  const [err, setErr] = useState("");

  const handleQuery = async () => {
    const name = methodName.trim();
    if (!name) return;
    setLoading(true);
    setErr("");
    try {
      const result = await api.repos.analysis.joern.allForMethod(repoId, name);
      setBranches(result.branches ?? []);
      setErrors(result.errors ?? []);
      setBoundaries(result.boundaries ?? []);
      setCallContext(result.callContext ?? []);
      setCalleeImpact(result.calleeImpact ?? []);
      setQueried(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "查询失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Method query input */}
      <div className="rounded-xl border border-outline-variant/15 bg-surface-container p-4">
        <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant/60 mb-3">
          输入函数名，跨函数分析调用链上下文与运行时风险
        </p>
        <div className="flex gap-2">
          <input
            value={methodName}
            onChange={(e) => setMethodName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleQuery(); }}
            placeholder="e.g. handleRequest, validateInput..."
            className="flex-1 rounded-lg bg-surface-container-high border border-outline-variant/20 px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:border-primary/40 font-mono"
          />
          <button
            onClick={handleQuery}
            disabled={loading || !methodName.trim()}
            className="inline-flex items-center gap-2 rounded-lg bg-primary/10 border border-primary/20 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-primary hover:bg-primary/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            查询
          </button>
        </div>
        {err && <p className="mt-2 text-xs text-tertiary">{err}</p>}
      </div>

      {queried && (
        <>
          {/* ── 跨函数上下文 ── */}
          <Section title="调用上下文（谁调用了此函数）" count={callContext.length} accent="primary">
            {callContext.length === 0
              ? <p className="text-xs text-on-surface-variant/40 italic">无调用上下文 — 该函数可能是入口函数</p>
              : <CallContextCards items={callContext} />}
          </Section>
          <Section title="被调用影响（此函数调用了谁）" count={calleeImpact.length} accent="primary">
            {calleeImpact.length === 0
              ? <p className="text-xs text-on-surface-variant/40 italic">无被调用函数 — 该函数是叶子函数</p>
              : <CalleeImpactCards items={calleeImpact} />}
          </Section>

          {/* ── 函数内部分析 ── */}
          <Section title="控制流分支" count={branches.length} accent="primary">
            {branches.length === 0
              ? <p className="text-xs text-on-surface-variant/40 italic">无控制流分支数据</p>
              : <BranchCards items={branches} />}
          </Section>
          <Section title="异常处理路径" count={errors.length} accent="tertiary">
            {errors.length === 0
              ? <p className="text-xs text-on-surface-variant/40 italic">无异常处理路径</p>
              : <ErrorPathCards items={errors} />}
          </Section>
          <Section title="边界值比较" count={boundaries.length} accent="amber">
            {boundaries.length === 0
              ? <p className="text-xs text-on-surface-variant/40 italic">无边界值比较</p>
              : <BoundaryCards items={boundaries} />}
          </Section>
        </>
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
  return (
    <div className="rounded-xl border border-outline-variant/15 bg-surface-container overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 hover:bg-surface-container-high/40 transition-colors"
      >
        {open ? <ChevronDown size={14} className="text-on-surface-variant/50" /> : <ChevronRight size={14} className="text-on-surface-variant/50" />}
        <span className="text-[11px] font-mono font-bold uppercase tracking-widest text-on-surface">{title}</span>
        <span className={`text-[10px] font-mono ml-1 ${accentColor}`}>({count})</span>
      </button>
      {open && <div className="border-t border-outline-variant/10 p-4">{children}</div>}
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

// ── Taint sub-view ─────────────────────────────────────────────────────────
function TaintView({ repoId }: { repoId: string }) {
  const [source, setSource] = useState("");
  const [sink, setSink] = useState("");
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
      const resp = await api.repos.analysis.joern.taint(repoId, s, sk);
      setPaths(resp.paths ?? []);
      setQueried(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "分析失败");
    } finally {
      setLoading(false);
    }
  };

  const PRESETS = [
    { label: "数值溢出", source: "read|recv|input", sink: "add|mul|sub|shift" },
    { label: "空指针", source: "malloc|calloc|alloc", sink: "deref|memcpy|strcpy" },
    { label: "边界越界", source: "read|recv|argc", sink: "array|index|offset" },
    { label: "资源泄漏", source: "open|fopen|socket", sink: "close|fclose|free" },
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
              onClick={() => { setSource(p.source); setSink(p.sink); }}
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
            追踪数据流
          </button>
          {err && <span className="text-xs text-tertiary font-data">{err}</span>}
        </div>
      </div>

      {/* Results */}
      {queried && (
        <div className="space-y-4 animate-in fade-in duration-700">
          <div className="flex items-center gap-2 px-2">
            <span className="text-xl font-display font-bold text-on-surface">{paths.length}</span>
            <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mt-1">条可达路径</span>
          </div>
          {paths.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-on-surface-variant/20 rounded-2xl border border-dashed border-outline-variant/10">
              <CheckCircle2 size={32} className="opacity-30 text-secondary" />
              <p className="text-[10px] font-data uppercase tracking-[0.2em]">未检测到异常传播</p>
            </div>
          ) : (
            <TaintPathCards paths={paths} />
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

// ── Taint Path Cards with pagination ──────────────────────────────────────
function TaintPathCards({ paths }: { paths: TaintPath[] }) {
  const { page, setPage, totalPages, paged } = usePagination(paths);
  return (
    <div className="grid gap-4">
      {paged.map((path, i) => {
        const globalIdx = (page - 1) * PAGE_SIZE + i;
        return (
          <div key={globalIdx} className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30 hover:shadow-xl">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-primary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
            <div className="flex justify-between items-center mb-6">
              <span className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">传播路径 {globalIdx + 1}</span>
              <span className="text-[10px] font-data text-on-surface-variant/20 tracking-widest">{path.elements?.length} 个节点</span>
            </div>
            <div className="space-y-0.5 relative">
              <div className="absolute left-2 top-2 bottom-2 w-px bg-outline-variant/10" />
              {(path.elements ?? []).map((el, j) => {
                const isSource = j === 0;
                const isSink = j === (path.elements.length - 1);
                return (
                  <div key={j} className="flex items-start gap-4 py-2 relative group/node">
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
                      <span className="text-[9px] font-data text-on-surface-variant/30 uppercase tracking-tight">
                        {el.filename ? shortPath(el.filename) : "internal"}
                        {el.line_number ? ` @ Line ${el.line_number}` : ""}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
      <Pagination current={page} total={totalPages} onChange={setPage} />
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
    <div className="flex flex-col h-full min-h-screen bg-surface">
      {/* ── Header ── */}
      <header className="h-12 shrink-0 flex items-center gap-3 px-5 border-b border-outline-variant/10 bg-surface-container-low/80 backdrop-blur-md">
        <Link
          href={`/repos/${repoId}`}
          className="inline-flex items-center gap-1.5 text-[11px] font-mono text-on-surface-variant/60 hover:text-on-surface transition-colors"
        >
          <ArrowLeft size={13} />
          {repoName || "Repo Hub"}
        </Link>
        <div className="h-4 w-px bg-outline-variant/20" />
        <span className="text-[11px] font-mono uppercase tracking-widest text-on-surface-variant/40">分析</span>
        <div className="flex-1" />
        <button
          onClick={handleRebuild}
          disabled={rebuilding}
          className="inline-flex items-center gap-1.5 rounded-full border bg-surface-container-high border-outline-variant/20 text-on-surface-variant hover:border-primary/30 hover:text-primary px-4 py-1.5 text-[11px] font-bold uppercase tracking-widest transition-all disabled:opacity-50"
        >
          {rebuilding ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          {rebuilding ? "重建 CPG..." : "重建分析"}
        </button>
      </header>

      {loadError && (
        <div className="shrink-0 mx-4 mt-2 rounded-lg border border-tertiary/30 bg-tertiary-container/20 px-4 py-2 flex items-center justify-between">
          <p className="text-xs text-tertiary">{loadError}</p>
          <button onClick={handleRebuild} className="text-xs text-primary font-bold uppercase tracking-widest hover:underline">
            重试
          </button>
        </div>
      )}

      {/* ── Body: sidebar + content ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-52 shrink-0 border-r border-outline-variant/10 bg-surface-container-low flex flex-col overflow-y-auto">
          <nav className="p-3 space-y-0.5">
            {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveNav(id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[11px] font-bold uppercase tracking-wider transition-all ${
                  activeNav === id
                    ? "bg-primary/10 text-primary border border-primary/20"
                    : "text-on-surface-variant/60 hover:text-on-surface hover:bg-surface-container"
                }`}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
          </nav>
        </aside>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-4xl">
            {activeNav === "overview" && <OverviewView summary={summary} />}
            {activeNav === "branches" && <BranchesView repoId={repoId} />}
            {activeNav === "testpoints" && <TestPointsView repoId={repoId} />}
            {activeNav === "taint" && <TaintView repoId={repoId} />}
          </div>
        </main>
      </div>
    </div>
  );
}

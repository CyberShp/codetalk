"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type {
  AnalysisSummary,
  SemgrepFinding,
  TestPoint,
  TaintPath,
  SeverityLevel,
} from "@/lib/types";
import {
  ArrowLeft,
  RefreshCw,
  Download,
  ShieldAlert,
  GitBranch,
  FlaskConical,
  Network,
  LayoutDashboard,
  ChevronDown,
  ChevronRight,
  Play,
  Loader2,
  CheckCircle2,
} from "lucide-react";

// ── Nav items ──────────────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "findings", label: "Findings", icon: ShieldAlert },
  { id: "branches", label: "Branches", icon: GitBranch },
  { id: "testpoints", label: "Test Points", icon: FlaskConical },
  { id: "taint", label: "Taint", icon: Network },
] as const;
type NavId = (typeof NAV_ITEMS)[number]["id"];

// ── Severity helpers ───────────────────────────────────────────────────────
const SEV_COLOR: Record<SeverityLevel, string> = {
  ERROR: "text-tertiary bg-tertiary/10 border-tertiary/20",
  WARNING: "text-amber-400 bg-amber-400/10 border-amber-400/20",
  INFO: "text-primary bg-primary/10 border-primary/20",
};
const SEV_DOT: Record<SeverityLevel, string> = {
  ERROR: "bg-tertiary shadow-[0_0_8px_rgba(255,209,205,0.6)]",
  WARNING: "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]",
  INFO: "bg-primary shadow-[0_0_8px_rgba(164,230,255,0.6)]",
};
const SEV_LABEL: Record<SeverityLevel, string> = {
  ERROR: "Critical",
  WARNING: "Warning",
  INFO: "Info",
};

// ── Small utility components ───────────────────────────────────────────────
function SeverityBadge({ level }: { level: SeverityLevel }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-data font-bold uppercase tracking-wider transition-all duration-300 ${SEV_COLOR[level]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${SEV_DOT[level]}`} />
      {SEV_LABEL[level]}
    </span>
  );
}

function MetricCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "primary" | "tertiary" | "secondary" | "amber";
}) {
  const accentClass = {
    primary: "border-primary/20 shadow-[0_0_20px_rgba(164,230,255,0.03)]",
    tertiary: "border-tertiary/20 shadow-[0_0_20px_rgba(255,209,205,0.03)]",
    secondary: "border-secondary/20 shadow-[0_0_20px_rgba(236,255,227,0.03)]",
    amber: "border-amber-400/20 shadow-[0_0_20px_rgba(251,191,36,0.03)]",
  }[accent ?? "primary"];

  const textClass = {
    primary: "text-primary",
    tertiary: "text-tertiary",
    secondary: "text-secondary",
    amber: "text-amber-400",
  }[accent ?? "primary"];

  return (
    <div className={`group rounded-xl border bg-surface-container p-4 flex flex-col gap-1 transition-all duration-500 hover:border-on-surface-variant/30 hover:shadow-lg ${accentClass}`}>
      <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50 group-hover:text-on-surface-variant/80 transition-colors">{label}</span>
      <span className={`text-4xl font-display font-bold tracking-tight ${textClass}`}>{value}</span>
      {sub && <span className="text-[10px] font-ui text-on-surface-variant/40 group-hover:text-on-surface-variant/60 transition-colors">{sub}</span>}
    </div>
  );
}

// ── Overview sub-view ──────────────────────────────────────────────────────
function SeverityRing({ data, total }: { data: Array<{ count: number, color: string }>, total: number }) {
  // Precompute offsets to avoid reassignment during render
  const segments = data.reduce<Array<{ ratio: number; offset: number; color: string }>>((acc, d) => {
    const prevOffset = acc.length > 0 ? acc[acc.length - 1].offset + acc[acc.length - 1].ratio : 0;
    const ratio = (d.count / total) * 100;
    if (ratio > 0) acc.push({ ratio, offset: prevOffset, color: d.color });
    return acc;
  }, []);

  return (
    <div className="relative w-24 h-24 shrink-0">
      <svg className="w-full h-full -rotate-90 transform" viewBox="0 0 32 32">
        <circle cx="16" cy="16" r="14" fill="transparent" stroke="currentColor" strokeWidth="2.5" className="text-surface-container-high" />
        {segments.map((seg, i) => (
          <circle
            key={i}
            cx="16" cy="16" r="14"
            fill="transparent"
            strokeDasharray={`${seg.ratio} ${100 - seg.ratio}`}
            strokeDashoffset={-seg.offset}
            strokeWidth="3.5"
            pathLength="100"
            className={`${seg.color.replace('bg-', 'stroke-')} transition-all duration-1000 ease-out`}
            strokeLinecap="round"
          />
        ))}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <span className="text-[10px] font-data text-on-surface-variant/40 leading-none">TOTAL</span>
        <span className="text-sm font-display font-bold text-on-surface">{total}</span>
      </div>
    </div>
  );
}

function OverviewView({
  summary,
  findings,
  onRescan,
}: {
  summary: AnalysisSummary | null;
  findings: SemgrepFinding[];
  onRescan: () => void;
}) {
  // Compute stats from loaded findings
  const totalFindings = findings.length;
  const criticalCount = findings.filter((f) => f.extra.severity === "ERROR").length;

  const bySeverity: Record<SeverityLevel, number> = { ERROR: 0, WARNING: 0, INFO: 0 };
  const byCategory: Record<string, number> = {};
  for (const f of findings) {
    bySeverity[f.extra.severity] = (bySeverity[f.extra.severity] ?? 0) + 1;
    const cat = f.extra.metadata.category ?? "other";
    byCategory[cat] = (byCategory[cat] ?? 0) + 1;
  }

  // Severity chart data
  const sevData: Array<{ sev: SeverityLevel; count: number; color: string }> = [
    { sev: "ERROR", count: bySeverity.ERROR, color: "bg-tertiary" },
    { sev: "WARNING", count: bySeverity.WARNING, color: "bg-amber-400" },
    { sev: "INFO", count: bySeverity.INFO, color: "bg-primary" },
  ];

  // Category chart data
  const catEntries = Object.entries(byCategory)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);
  const catMax = catEntries[0]?.[1] || 1;

  // Tool health
  const joernHealthy = summary?.tools.joern.healthy ?? false;
  const semgrepHealthy = summary?.tools.semgrep.healthy ?? false;

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Tool status bar */}
      <div className="flex items-center gap-3 rounded-xl border border-outline-variant/10 bg-surface-container-low px-4 py-3">
        <ToolStatusDot healthy={joernHealthy} label="Joern CPG" />
        <div className="h-3 w-px bg-outline-variant/20" />
        <ToolStatusDot healthy={semgrepHealthy} label="Semgrep" />
        <div className="flex-1" />
        {findings.length === 0 && (
          <button
            onClick={onRescan}
            className="group inline-flex items-center gap-2 rounded-full bg-primary/5 border border-primary/20 px-5 py-1.5 text-[11px] font-bold uppercase tracking-[0.2em] text-primary hover:bg-primary/10 transition-all active:scale-95"
          >
            <Play size={11} className="group-hover:translate-x-0.5 transition-transform" />
            Run Scan
          </button>
        )}
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="Findings" value={totalFindings} accent="primary" />
        <MetricCard label="Critical" value={criticalCount} accent="tertiary" sub="Priority Fix" />
        <MetricCard label="Warning" value={bySeverity.WARNING} accent="amber" sub="Review needed" />
        <MetricCard label="Info" value={bySeverity.INFO} accent="secondary" sub="Low priority" />
      </div>

      {totalFindings > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Severity distribution */}
          <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30">
            <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40 mb-6">Severity Matrix</h3>
            <div className="flex items-center gap-8">
              <SeverityRing data={sevData} total={totalFindings} />
              <div className="flex-1 space-y-4">
                {sevData.map(({ sev, count, color }) => (
                  <div key={sev} className="space-y-1.5">
                    <div className="flex justify-between items-baseline text-[11px]">
                      <span className="text-on-surface-variant font-ui">{SEV_LABEL[sev]}</span>
                      <span className="font-data text-on-surface font-bold">{count}</span>
                    </div>
                    <div className="h-1 rounded-full bg-surface-container-high overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-1000 delay-300 ${color}`}
                        style={{ width: `${(count / (totalFindings || 1)) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Category breakdown */}
          <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30">
            <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40 mb-6">Attack Vectors</h3>
            {catEntries.length === 0 ? (
              <div className="flex items-center justify-center h-24">
                <p className="text-[11px] font-ui text-on-surface-variant/30 italic">No patterns detected</p>
              </div>
            ) : (
              <div className="space-y-4">
                {catEntries.map(([cat, count]) => (
                  <div key={cat} className="space-y-1.5">
                    <div className="flex justify-between items-baseline text-[11px]">
                      <span className="text-on-surface-variant font-ui capitalize">{cat.replace(/_/g, " ")}</span>
                      <span className="font-data text-on-surface">{count}</span>
                    </div>
                    <div className="h-1 rounded-full bg-surface-container-high overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary/40 group-hover:bg-primary transition-all duration-1000 delay-500"
                        style={{ width: `${(count / catMax) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Joern CPG engine status */}
      <div className="relative group rounded-2xl border border-outline-variant/10 bg-surface-container-lowest p-6 overflow-hidden">
        <div className="absolute right-0 top-0 p-8 opacity-[0.03] group-hover:opacity-[0.06] transition-opacity pointer-events-none">
          <GitBranch size={120} />
        </div>
        <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40 mb-5">CPG Intelligence Engine</h3>
        <div className="grid grid-cols-2 gap-8">
          <div className="space-y-1">
            <span className="text-on-surface-variant/40 text-[10px] font-data uppercase tracking-wider">Engine Status</span>
            <p className={`font-data text-sm font-bold ${joernHealthy ? "text-secondary" : "text-tertiary"}`}>
              {joernHealthy ? "Online" : "Offline"}
            </p>
          </div>
          <div className="space-y-1">
            <span className="text-on-surface-variant/40 text-[10px] font-data uppercase tracking-wider">Capabilities</span>
            <p className="font-data text-xs text-on-surface-variant/60 leading-relaxed">
              {summary?.tools.joern.capabilities.join(", ") ?? "—"}
            </p>
          </div>
        </div>
        <p className="mt-4 text-[10px] font-ui text-on-surface-variant/30">
          Use Branches tab to query control flow · Use Taint tab to trace data flow
        </p>
      </div>
    </div>
  );
}

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

// ── Findings sub-view ──────────────────────────────────────────────────────
function FindingsView({
  findings,
  loading,
  severityFilter,
  categoryFilter,
}: {
  findings: SemgrepFinding[];
  loading: boolean;
  severityFilter: SeverityLevel | null;
  categoryFilter: string | null;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const filtered = findings.filter((f) => {
    if (severityFilter && f.extra.severity !== severityFilter) return false;
    if (categoryFilter && f.extra.metadata.category !== categoryFilter) return false;
    return true;
  });

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4 animate-pulse">
        <Loader2 size={24} className="animate-spin text-primary/40" />
        <span className="text-[11px] font-data uppercase tracking-widest text-on-surface-variant/40">Synchronizing Findings...</span>
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4 text-on-surface-variant/20">
        <div className="w-16 h-16 rounded-full border-2 border-dashed border-current flex items-center justify-center">
          <CheckCircle2 size={32} />
        </div>
        <p className="text-[11px] font-data uppercase tracking-widest">Environment Secure</p>
      </div>
    );
  }

  return (
    <div className="space-y-2 pb-12 animate-in fade-in slide-in-from-left-2 duration-500">
      <div className="mb-4 flex items-center gap-2">
        <span className="text-2xl font-display font-bold text-on-surface">{filtered.length}</span>
        <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mt-1">Issues Identified</span>
      </div>
      {filtered.map((f, i) => {
        const key = `${f.check_id}-${f.path}-${f.start.line}-${i}`;
        const isOpen = expanded.has(key);
        const ruleShort = f.check_id.split(".").slice(-2).join(".");
        return (
          <div
            key={key}
            className={`group rounded-xl border transition-all duration-300 ${
              isOpen ? "border-outline-variant/40 bg-surface-container shadow-2xl" : "border-outline-variant/10 bg-surface-container-low hover:border-outline-variant/30"
            }`}
          >
            <button
              onClick={() => toggle(key)}
              className="w-full flex items-center gap-4 px-5 py-4 text-left group transition-colors"
            >
              <div className="shrink-0 transition-transform duration-300" style={{ transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                <ChevronRight size={16} className="text-on-surface-variant/30 group-hover:text-on-surface-variant/60" />
              </div>
              <SeverityBadge level={f.extra.severity} />
              <span className="font-data text-[11px] text-primary/70 shrink-0 font-bold tracking-tight">{ruleShort}</span>
              <span className="flex-1 text-[13px] font-ui text-on-surface-variant group-hover:text-on-surface transition-colors truncate">{f.extra.message}</span>
              <span className="text-[10px] font-data text-on-surface-variant/30 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                {f.path.split("/").slice(-1)}:{f.start.line}
              </span>
            </button>

            {isOpen && (
              <div className="border-t border-outline-variant/10 px-6 py-5 space-y-6 bg-surface-container-lowest/30 animate-in fade-in zoom-in-[0.98] duration-300">
                {/* File location */}
                <div className="flex items-center gap-2 text-[11px] font-data">
                  <span className="text-on-surface-variant/40 tracking-wider">LOCATION</span>
                  <span className="text-primary/60 hover:text-primary transition-colors cursor-default underline decoration-primary/20 underline-offset-4">{f.path}</span>
                  <span className="text-on-surface-variant/30">:{f.start.line}–{f.end.line}</span>
                </div>

                {/* Code snippet */}
                {f.extra.lines && (
                  <div className="relative group/code">
                    <div className="absolute -left-3 top-0 bottom-0 w-1 bg-primary/20 rounded-full" />
                    <pre className="rounded-xl bg-surface-container-lowest border border-outline-variant/10 p-4 text-[12px] font-data text-on-surface/90 overflow-x-auto shadow-inner">
                      {f.extra.lines}
                    </pre>
                  </div>
                )}

                {/* Dataflow trace */}
                {f.extra.dataflow_trace && (
                  <div className="space-y-3 relative pl-4">
                    <div className="absolute left-1.5 top-8 bottom-4 w-px bg-outline-variant/20" />
                    <span className="text-[9px] font-data uppercase tracking-[0.3em] text-on-surface-variant/30">Neural Propagation Path</span>
                    <div className="text-[11px] font-data space-y-4">
                      {f.extra.dataflow_trace.taint_source && (
                        <div className="flex items-center gap-3 group/node">
                          <div className="w-3 h-3 rounded-full bg-secondary/20 border border-secondary/40 flex items-center justify-center z-10">
                            <div className="w-1 h-1 rounded-full bg-secondary shadow-[0_0_8px_var(--color-secondary)]" />
                          </div>
                          <span className="text-secondary/70 font-bold uppercase tracking-widest text-[9px]">Source</span>
                          <span className="text-on-surface/60">{f.extra.dataflow_trace.taint_source[0]}</span>
                        </div>
                      )}
                      {(f.extra.dataflow_trace.intermediate_vars ?? []).map(([v, loc], idx) => (
                        <div key={idx} className="flex items-center gap-3 group/node">
                          <div className="w-3 h-3 rounded-full bg-surface-container-high border border-outline-variant/30 flex items-center justify-center z-10">
                            <div className="w-1 h-1 rounded-full bg-on-surface-variant/30" />
                          </div>
                          <span className="text-on-surface-variant/60">{v}</span>
                          <span className="text-on-surface-variant/30 text-[9px]">@{loc.line}</span>
                        </div>
                      ))}
                      {f.extra.dataflow_trace.taint_sink && (
                        <div className="flex items-center gap-3 group/node">
                          <div className="w-3 h-3 rounded-full bg-tertiary/20 border border-tertiary/40 flex items-center justify-center z-10">
                            <div className="w-1 h-1 rounded-full bg-tertiary shadow-[0_0_8px_var(--color-tertiary)]" />
                          </div>
                          <span className="text-tertiary/70 font-bold uppercase tracking-widest text-[9px]">Sink</span>
                          <span className="text-on-surface/60">{f.extra.dataflow_trace.taint_sink[0]}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Fix suggestion */}
                {f.extra.fix && (
                  <div className="space-y-2">
                    <span className="text-[9px] font-data uppercase tracking-[0.3em] text-secondary-fixed-dim/60">Remediation Blueprint</span>
                    <div className="relative group/fix">
                      <div className="absolute -left-3 top-0 bottom-0 w-1 bg-secondary/20 rounded-full" />
                      <pre className="rounded-xl bg-secondary/5 border border-secondary/10 p-4 text-[12px] font-data text-secondary-fixed-dim/80 overflow-x-auto">
                        {f.extra.fix}
                      </pre>
                    </div>
                  </div>
                )}

                {/* Metadata badges */}
                {(f.extra.metadata.cwe?.length || f.extra.metadata.owasp?.length) ? (
                  <div className="flex gap-2 flex-wrap pt-2">
                    {(f.extra.metadata.cwe ?? []).map((c) => (
                      <span key={c} className="text-[9px] font-data px-3 py-1 rounded-full bg-surface-container-high border border-outline-variant/20 text-on-surface-variant/60 hover:text-on-surface hover:border-outline-variant/40 transition-colors">{c}</span>
                    ))}
                    {(f.extra.metadata.owasp ?? []).map((o) => (
                      <span key={o} className="text-[9px] font-data px-3 py-1 rounded-full bg-tertiary/5 border border-tertiary/10 text-tertiary/40 hover:text-tertiary/60 transition-colors">{o}</span>
                    ))}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Branches sub-view ──────────────────────────────────────────────────────
function BranchesView({ repoId }: { repoId: string }) {
  const [methodName, setMethodName] = useState("");
  const [loading, setLoading] = useState(false);
  const [branches, setBranches] = useState<unknown[]>([]);
  const [errors, setErrors] = useState<unknown[]>([]);
  const [boundaries, setBoundaries] = useState<unknown[]>([]);
  const [queried, setQueried] = useState(false);
  const [err, setErr] = useState("");

  const handleQuery = async () => {
    const name = methodName.trim();
    if (!name) return;
    setLoading(true);
    setErr("");
    try {
      // Use batch endpoint: ONE CPG import instead of three
      const result = await api.repos.analysis.joern.allForMethod(repoId, name);
      setBranches(result.branches ?? []);
      setErrors(result.errors ?? []);
      setBoundaries(result.boundaries ?? []);
      setQueried(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "查询失败");
    } finally {
      setLoading(false);
    }
  };

  const renderList = (items: unknown[], emptyMsg: string) => {
    if (!Array.isArray(items) || items.length === 0) {
      return <p className="text-xs text-on-surface-variant/40 italic">{emptyMsg}</p>;
    }
    return (
      <div className="space-y-1">
        {items.map((item, i) => (
          <pre
            key={i}
            className="rounded-lg bg-surface-container-high px-3 py-2 text-[11px] font-mono text-on-surface/80 overflow-x-auto"
          >
            {JSON.stringify(item, null, 2)}
          </pre>
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-5">
      {/* Method query input */}
      <div className="rounded-xl border border-outline-variant/15 bg-surface-container p-4">
        <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant/60 mb-3">
          输入函数名，查询 Joern CPG 分支数据
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
          <Section title="控制流分支" count={Array.isArray(branches) ? branches.length : 0} accent="primary">
            {renderList(branches, "无控制流分支数据")}
          </Section>
          <Section title="异常处理路径" count={Array.isArray(errors) ? errors.length : 0} accent="tertiary">
            {renderList(errors, "无异常处理路径")}
          </Section>
          <Section title="边界值比较" count={Array.isArray(boundaries) ? boundaries.length : 0} accent="amber">
            {renderList(boundaries, "无边界值比较")}
          </Section>
        </>
      )}

      {!queried && (
        <div className="flex flex-col items-center justify-center h-40 gap-2 text-on-surface-variant/30">
          <GitBranch size={32} className="opacity-30" />
          <p className="text-sm">输入函数名后查询该函数的分支结构</p>
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

  const RISK_COLOR: Record<string, string> = {
    high: "text-tertiary bg-tertiary/10 border-tertiary/20 shadow-[0_0_12px_rgba(255,209,205,0.1)]",
    medium: "text-amber-400 bg-amber-400/10 border-amber-400/20 shadow-[0_0_12px_rgba(251,191,36,0.1)]",
    low: "text-secondary bg-secondary/10 border-secondary/20 shadow-[0_0_12px_rgba(236,255,227,0.1)]",
  };

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
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">Joint Analysis Engine</h3>
        </div>
        <div className="flex gap-3">
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Target function (optional: default full analysis)"
            className="flex-1 rounded-xl bg-surface-container border border-outline-variant/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-secondary/40 focus:ring-1 focus:ring-secondary/20 transition-all font-data"
          />
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl bg-secondary/5 border border-secondary/20 px-6 py-3 text-[11px] font-bold uppercase tracking-[0.2em] text-secondary hover:bg-secondary/10 transition-all disabled:opacity-40 active:scale-95"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Forge Points
          </button>
        </div>
        {err && <p className="text-xs text-tertiary font-data">{err}</p>}
      </div>

      {/* Export controls */}
      {generated && testPoints.length > 0 && (
        <div className="flex items-center gap-3 px-2 animate-in fade-in duration-700">
          <span className="text-[10px] font-data text-on-surface-variant/40 tracking-widest">{testPoints.length} DISCOVERY UNITS</span>
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
        <div className="space-y-4">
          {testPoints.map((tp, i) => (
            <div key={tp.id ?? i} className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30 hover:shadow-xl overflow-hidden">
              <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-secondary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0 space-y-4">
                  <div className="flex items-center gap-3">
                    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest ${RISK_COLOR[tp.risk_level] ?? RISK_COLOR.low}`}>
                      {tp.risk_level}
                    </span>
                    <span className="text-[10px] font-data text-on-surface-variant/40 tracking-tighter uppercase">{tp.category}</span>
                    <div className="h-px flex-1 bg-outline-variant/5" />
                    <span className="text-[10px] font-data text-on-surface-variant/20 tracking-widest">TP-{String(i + 1).padStart(2, "0")}</span>
                  </div>
                  <h4 className="text-base font-display font-medium text-on-surface group-hover:text-secondary-fixed-dim transition-colors">{tp.scenario}</h4>
                  
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <div className="w-1 h-1 rounded-full bg-primary/40" />
                        <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">Inputs</span>
                      </div>
                      <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.input_conditions}</p>
                    </div>
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <div className="w-1 h-1 rounded-full bg-secondary/40" />
                        <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">Expectation</span>
                      </div>
                      <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.expected_behavior}</p>
                    </div>
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <div className="w-1 h-1 rounded-full bg-tertiary/40" />
                        <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">Vector</span>
                      </div>
                      <p className="text-[13px] font-ui text-on-surface-variant leading-relaxed pl-2.5 border-l border-outline-variant/10">{tp.risk_scenario}</p>
                    </div>
                    {tp.boundary_values && (
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                          <div className="w-1 h-1 rounded-full bg-amber-400/40" />
                          <span className="text-[9px] font-data uppercase tracking-[0.2em] text-on-surface-variant/50">Boundaries</span>
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
          ))}
        </div>
      ) : (
        !loading && (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/20">
            <FlaskConical size={32} className="opacity-20" />
            <p className="text-[10px] font-data uppercase tracking-[0.2em]">{generated ? "No Test Units Formulated" : "Awaiting Strategy Generation"}</p>
          </div>
        )
      )}
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
    { label: "SQLi", source: "getParameter", sink: "executeQuery" },
    { label: "XSS", source: "getParameter", sink: "write" },
    { label: "Path", source: "getParameter", sink: "readFile" },
    { label: "Command", source: "getParameter", sink: "exec" },
  ];

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Input panel */}
      <div className="group rounded-2xl border border-outline-variant/15 bg-surface-container-low p-6 space-y-6 transition-all hover:border-outline-variant/30">
        <div className="flex items-center gap-2">
          <Network size={14} className="text-primary/60" />
          <h3 className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">Propagation Analysis</h3>
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
            <label className="text-[9px] font-data uppercase tracking-[0.3em] text-secondary/50 ml-1">Source Pattern</label>
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="e.g. getParameter"
              className="w-full rounded-xl bg-surface-container border border-secondary/10 px-4 py-3 text-sm text-on-surface placeholder:text-on-surface-variant/20 focus:outline-none focus:border-secondary/40 focus:ring-1 focus:ring-secondary/10 transition-all font-data shadow-inner"
            />
          </div>
          <div className="space-y-2">
            <label className="text-[9px] font-data uppercase tracking-[0.3em] text-tertiary/50 ml-1">Sink Pattern</label>
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
            Trace Dataflow
          </button>
          {err && <span className="text-xs text-tertiary font-data">{err}</span>}
        </div>
      </div>

      {/* Results */}
      {queried && (
        <div className="space-y-4 animate-in fade-in duration-700">
          <div className="flex items-center gap-2 px-2">
            <span className="text-xl font-display font-bold text-on-surface">{paths.length}</span>
            <span className="text-[10px] font-data uppercase tracking-[0.2em] text-on-surface-variant/40 mt-1">Reachable Paths Found</span>
          </div>
          {paths.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-on-surface-variant/20 rounded-2xl border border-dashed border-outline-variant/10">
              <CheckCircle2 size={32} className="opacity-30 text-secondary" />
              <p className="text-[10px] font-data uppercase tracking-[0.2em]">No Data Leakage Detected</p>
            </div>
          ) : (
            <div className="grid gap-4">
              {paths.map((path, i) => (
                <div key={i} className="group relative rounded-2xl border border-outline-variant/10 bg-surface-container-low p-6 transition-all hover:border-outline-variant/30 hover:shadow-xl">
                  <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-primary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  <div className="flex justify-between items-center mb-6">
                    <span className="text-[10px] font-data uppercase tracking-[0.3em] text-on-surface-variant/40">Propagation Route {i + 1}</span>
                    <span className="text-[10px] font-data text-on-surface-variant/20 tracking-widest">{path.elements?.length} STAGES</span>
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
                              {el.filename ? el.filename.split("/").slice(-1) : "internal"}
                              {el.line_number ? ` @ Line ${el.line_number}` : ""}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!queried && (
        <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/20">
          <Network size={32} className="opacity-20" />
          <p className="text-[10px] font-data uppercase tracking-[0.2em]">Map cross-function information leakage</p>
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
  const [findings, setFindings] = useState<SemgrepFinding[]>([]);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanDone, setScanDone] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [scanError, setScanError] = useState("");

  // Filters
  const [severityFilter, setSeverityFilter] = useState<SeverityLevel | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  const [repoName, setRepoName] = useState("");

  useEffect(() => {
    api.repos.get(repoId).then((d) => setRepoName(d.repo.name)).catch(() => {});
  }, [repoId]);

  useEffect(() => {
    api.repos.analysis.summary(repoId).then(setSummary).catch((e) => {
      setLoadError(e instanceof Error ? e.message : "分析摘要加载失败");
    });
  }, [repoId]);

  // Load findings for both Overview stats and Findings tab; only fetch once
  useEffect(() => {
    if (findings.length > 0) return;
    if (activeNav !== "overview" && activeNav !== "findings") return;
    setFindingsLoading(true);
    api.repos.analysis.semgrep
      .findings(repoId)
      .then((r) => setFindings(r.findings))
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : "扫描结果加载失败");
      })
      .finally(() => setFindingsLoading(false));
  }, [repoId, activeNav]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRescan = useCallback(async () => {
    setScanning(true);
    setScanDone(false);
    setScanError("");
    setFindings([]);
    try {
      const [scanResp, s] = await Promise.all([
        api.repos.analysis.semgrep.scan(repoId),
        api.repos.analysis.summary(repoId),
      ]);
      setSummary(s);
      if (scanResp.findings) setFindings(scanResp.findings);
      setScanDone(true);
      setTimeout(() => setScanDone(false), 4000);
    } catch (e) {
      setScanError(e instanceof Error ? e.message : "扫描失败");
    } finally {
      setScanning(false);
    }
  }, [repoId]);

  // Derive category list from findings
  const categories = Array.from(
    new Set(findings.map((f) => f.extra.metadata.category).filter(Boolean) as string[])
  ).sort();

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
        <span className="text-[11px] font-mono uppercase tracking-widest text-on-surface-variant/40">Analysis</span>
        <div className="flex-1" />
        <button
          onClick={handleRescan}
          disabled={scanning}
          className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-[11px] font-bold uppercase tracking-widest transition-all disabled:opacity-50 ${
            scanDone
              ? "bg-secondary/10 border-secondary/30 text-secondary"
              : "bg-surface-container-high border-outline-variant/20 text-on-surface-variant hover:border-primary/30 hover:text-primary"
          }`}
        >
          {scanning ? <Loader2 size={11} className="animate-spin" /> : scanDone ? <CheckCircle2 size={11} /> : <RefreshCw size={11} />}
          {scanning ? "Scanning..." : scanDone ? "Done" : "Re-scan"}
        </button>
      </header>

      {(loadError || scanError) && (
        <div className="shrink-0 mx-4 mt-2 rounded-lg border border-tertiary/30 bg-tertiary-container/20 px-4 py-2 flex items-center justify-between">
          <p className="text-xs text-tertiary">{scanError || loadError}</p>
          {scanError && (
            <button onClick={handleRescan} className="text-xs text-primary font-bold uppercase tracking-widest hover:underline">
              重试
            </button>
          )}
        </div>
      )}

      {/* ── Body: sidebar + content ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-52 shrink-0 border-r border-outline-variant/10 bg-surface-container-low flex flex-col overflow-y-auto">
          {/* Nav */}
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

          <div className="mx-3 my-2 h-px bg-outline-variant/10" />

          {/* Severity filter */}
          <div className="px-3 pb-2">
            <p className="text-[9px] font-mono uppercase tracking-widest text-on-surface-variant/40 mb-2 px-1">Severity</p>
            {(["ERROR", "WARNING", "INFO"] as SeverityLevel[]).map((sev) => (
              <button
                key={sev}
                onClick={() => setSeverityFilter(prev => prev === sev ? null : sev)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg mb-0.5 text-[11px] font-mono transition-all ${
                  severityFilter === sev ? "bg-surface-container-high" : "hover:bg-surface-container"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${SEV_DOT[sev]} shadow-sm`} />
                <span className={severityFilter === sev ? SEV_COLOR[sev].split(" ")[0] : "text-on-surface-variant/60"}>
                  {SEV_LABEL[sev]}
                </span>
                {severityFilter === sev && (
                  <span className="ml-auto text-[9px] font-mono text-on-surface-variant/40">✕</span>
                )}
              </button>
            ))}
          </div>

          {/* Category filter */}
          {categories.length > 0 && (
            <>
              <div className="mx-3 my-2 h-px bg-outline-variant/10" />
              <div className="px-3 pb-3">
                <p className="text-[9px] font-mono uppercase tracking-widest text-on-surface-variant/40 mb-2 px-1">Category</p>
                {categories.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setCategoryFilter(prev => prev === cat ? null : cat)}
                    className={`w-full flex items-center px-3 py-1.5 rounded-lg mb-0.5 text-[11px] font-mono capitalize transition-all ${
                      categoryFilter === cat
                        ? "bg-surface-container-high text-on-surface"
                        : "text-on-surface-variant/60 hover:bg-surface-container"
                    }`}
                  >
                    {cat.replace(/_/g, " ")}
                    {categoryFilter === cat && (
                      <span className="ml-auto text-[9px] font-mono text-on-surface-variant/40">✕</span>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-4xl">
            {activeNav === "overview" && (
              <OverviewView summary={summary} findings={findings} onRescan={handleRescan} />
            )}
            {activeNav === "findings" && (
              <FindingsView
                findings={findings}
                loading={findingsLoading}
                severityFilter={severityFilter}
                categoryFilter={categoryFilter}
              />
            )}
            {activeNav === "branches" && <BranchesView repoId={repoId} />}
            {activeNav === "testpoints" && <TestPointsView repoId={repoId} />}
            {activeNav === "taint" && <TaintView repoId={repoId} />}
          </div>
        </main>
      </div>
    </div>
  );
}

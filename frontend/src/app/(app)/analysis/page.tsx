"use client";

import { useState, useEffect, useMemo, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AnalysisScope } from "@/lib/types";
import {
  ShieldAlert,
  Loader2,
  AlertTriangle,
  FolderOpen,
  Clock,
  ChevronRight,
} from "lucide-react";

// ── Helpers ────────────────────────────────────────────────────────────────

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return "刚刚";
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function RiskBar({ high, med, total }: { high: number; med: number; total: number }) {
  if (total === 0) return <div className="h-1.5 w-24 rounded-full bg-surface-container-high/40" />;
  const highPct = Math.round((high / total) * 100);
  const medPct = Math.round((med / total) * 100);
  return (
    <div className="flex h-1.5 w-24 rounded-full overflow-hidden bg-surface-container-high/40">
      <div className="bg-tertiary/70" style={{ width: `${highPct}%` }} />
      <div className="bg-amber-400/60" style={{ width: `${medPct}%` }} />
    </div>
  );
}

// ── Scope row ──────────────────────────────────────────────────────────────

function ScopeRow({
  scope,
  depth,
}: {
  scope: AnalysisScope;
  depth: number;
}) {
  const router = useRouter();
  const risk = scope.risk_summary;

  const handleClick = () => {
    const params = new URLSearchParams();
    if (scope.scope_path !== "/") params.set("scope", scope.scope_path);
    const qs = params.toString();
    router.push(`/repos/${scope.repo_id}/analysis${qs ? `?${qs}` : ""}`);
  };

  return (
    <button
      onClick={handleClick}
      className="w-full group flex items-center gap-3 px-4 py-3 rounded-xl hover:bg-surface-container-high/40 transition-all text-left"
      style={{ paddingLeft: `${16 + depth * 20}px` }}
    >
      {/* Depth indent line */}
      {depth > 0 && (
        <span className="text-[10px] font-data text-on-surface-variant/20 shrink-0">⊂</span>
      )}

      {/* Icon + path */}
      <FolderOpen size={13} className="shrink-0 text-primary/50 group-hover:text-primary transition-colors" />
      <span className="font-mono text-[12px] text-on-surface group-hover:text-primary transition-colors truncate min-w-0 flex-1">
        {scope.scope_path}
      </span>

      {/* Tools */}
      <div className="flex gap-1 shrink-0">
        {scope.tools_completed.map((t) => (
          <span
            key={t}
            className="text-[9px] font-data uppercase tracking-wider px-1.5 py-0.5 rounded bg-surface-container-high border border-outline-variant/10 text-on-surface-variant/50"
          >
            {t}
          </span>
        ))}
      </div>

      {/* Risk summary */}
      {risk ? (
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-data text-tertiary/70">高:{risk.high}</span>
          <span className="text-[10px] font-data text-amber-400/70">中:{risk.med}</span>
          <RiskBar high={risk.high} med={risk.med} total={risk.total} />
        </div>
      ) : (
        <span className="text-[10px] font-data text-on-surface-variant/20 shrink-0">—</span>
      )}

      {/* Time */}
      <div className="flex items-center gap-1 shrink-0 text-on-surface-variant/30">
        <Clock size={10} />
        <span className="text-[10px] font-data">{timeAgo(scope.last_analyzed_at)}</span>
      </div>

      <ChevronRight size={12} className="shrink-0 text-on-surface-variant/20 group-hover:text-primary/50 transition-colors" />
    </button>
  );
}

// ── Repo group ─────────────────────────────────────────────────────────────

function RepoGroup({ repoId, repoName, branch, scopes }: {
  repoId: string;
  repoName: string;
  branch: string;
  scopes: AnalysisScope[];
}) {
  // Build depth map: how deep is each scope relative to others in this repo
  const allPaths = scopes.map((s) => s.scope_path);

  function depthOf(scopePath: string): number {
    return allPaths.filter(
      (p) => p !== scopePath && scopePath.startsWith(p),
    ).length;
  }

  const sorted = [...scopes].sort((a, b) => a.scope_path.localeCompare(b.scope_path));

  return (
    <div className="rounded-2xl border border-outline-variant/10 bg-surface-container-low/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-outline-variant/8 bg-surface-container-low">
        <ShieldAlert size={14} className="text-tertiary/60 shrink-0" />
        <Link
          href={`/repos/${repoId}`}
          className="font-display font-bold text-[13px] text-on-surface hover:text-primary transition-colors"
        >
          {repoName}
        </Link>
        <span className="text-[10px] font-data text-on-surface-variant/30 tracking-wide">
          {branch}
        </span>
        <span className="ml-auto text-[10px] font-data text-on-surface-variant/30">
          {scopes.length} 个范围
        </span>
      </div>

      {/* Scope rows */}
      <div className="divide-y divide-outline-variant/5">
        {sorted.map((s) => (
          <ScopeRow key={`${s.repo_id}:${s.scope_path}`} scope={s} depth={depthOf(s.scope_path)} />
        ))}
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

function AnalysisPageInner() {
  const searchParams = useSearchParams();
  const repoFilter = searchParams.get("repo") ?? undefined;

  const [scopes, setScopes] = useState<AnalysisScope[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.analysis
      .scopes()
      .then((res) => setScopes(res.scopes))
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  // Group by repo
  const grouped = useMemo(() => {
    const filtered = repoFilter
      ? scopes.filter((s) => s.repo_id === repoFilter)
      : scopes;

    const map = new Map<string, { repoName: string; branch: string; scopes: AnalysisScope[] }>();
    for (const s of filtered) {
      const existing = map.get(s.repo_id);
      if (existing) {
        existing.scopes.push(s);
      } else {
        map.set(s.repo_id, { repoName: s.repo_name, branch: s.branch, scopes: [s] });
      }
    }
    return [...map.entries()].sort(([, a], [, b]) => a.repoName.localeCompare(b.repoName));
  }, [scopes, repoFilter]);

  // Global summary stats — computed from the filtered set, not the full scopes list
  const globalStats = useMemo(() => {
    const filteredScopes = grouped.flatMap(([, g]) => g.scopes);
    const totalHigh = filteredScopes.reduce((s, sc) => s + (sc.risk_summary?.high ?? 0), 0);
    const totalMed = filteredScopes.reduce((s, sc) => s + (sc.risk_summary?.med ?? 0), 0);
    return {
      repos: grouped.length,
      scopeCount: filteredScopes.length,
      high: totalHigh,
      med: totalMed,
    };
  }, [grouped]);

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-tertiary/10 border border-tertiary/20">
              <ShieldAlert size={18} className="text-tertiary" />
            </div>
            <h1 className="text-2xl font-display font-bold text-on-surface tracking-tight">
              静态分析
            </h1>
          </div>
          <p className="text-sm text-on-surface-variant/50 pl-1">
            跨仓库安全态势总览 · 全局共享
          </p>
        </div>
      </div>

      {/* Global summary bar */}
      {!loading && scopes.length > 0 && (
        <div className="flex items-center gap-6 px-5 py-3.5 rounded-xl border border-outline-variant/10 bg-surface-container-low/60 text-[12px] font-data">
          <span className="text-on-surface-variant/50">
            <span className="text-on-surface font-bold">{globalStats.repos}</span> 仓库
          </span>
          <span className="text-on-surface-variant/20">·</span>
          <span className="text-on-surface-variant/50">
            <span className="text-on-surface font-bold">{globalStats.scopeCount}</span> 分析范围
          </span>
          <span className="text-on-surface-variant/20">·</span>
          <span className="text-tertiary/80">
            高风险 <span className="font-bold">{globalStats.high}</span>
          </span>
          <span className="text-amber-400/80">
            中风险 <span className="font-bold">{globalStats.med}</span>
          </span>
          {repoFilter && (
            <>
              <span className="text-on-surface-variant/20">·</span>
              <Link href="/analysis" className="text-primary/60 hover:text-primary transition-colors">
                清除过滤
              </Link>
            </>
          )}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="flex flex-col items-center justify-center h-40 gap-3 text-on-surface-variant/30">
          <Loader2 size={28} className="animate-spin text-primary/40" />
          <span className="text-[11px] font-data uppercase tracking-widest">加载分析范围...</span>
        </div>
      ) : error ? (
        <div className="flex items-center gap-3 px-5 py-4 rounded-xl bg-tertiary/10 border border-tertiary/20 text-tertiary text-sm">
          <AlertTriangle size={16} className="shrink-0" />
          {error}
        </div>
      ) : grouped.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 gap-4 text-on-surface-variant/30">
          <ShieldAlert size={40} className="opacity-20" />
          <div className="text-center space-y-1">
            <p className="text-sm font-display">暂无已分析的仓库</p>
            <p className="text-xs">
              在{" "}
              <Link href="/assets" className="text-primary/60 hover:text-primary transition-colors">
                仓库页
              </Link>
              {" "}触发首次分析
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {grouped.map(([repoId, { repoName, branch, scopes: repoScopes }]) => (
            <RepoGroup
              key={repoId}
              repoId={repoId}
              repoName={repoName}
              branch={branch}
              scopes={repoScopes}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense>
      <AnalysisPageInner />
    </Suspense>
  );
}

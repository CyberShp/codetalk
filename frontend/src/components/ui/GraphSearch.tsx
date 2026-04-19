"use client";

import { useState, useCallback } from "react";
import { api } from "@/lib/api";

type SearchResult = {
  id: string;
  name: string;
  label: string;
  filePath: string;
  score: number;
  connections?: number;
  cluster?: string;
  processes?: string[];
};

type SearchMode = "hybrid" | "bm25" | "semantic";

const MODE_LABELS: Record<SearchMode, string> = {
  hybrid: "Hybrid",
  bm25: "BM25",
  semantic: "Semantic",
};

const LABEL_COLORS: Record<string, string> = {
  File: "#3B82F6",
  Folder: "#6366F1",
  Class: "#8B5CF6",
  Function: "#10B981",
  Method: "#14B8A6",
  Module: "#F59E0B",
  Route: "#EF4444",
  Process: "#EC4899",
  Community: "#6366F1",
  Tool: "#F97316",
};

interface GraphSearchProps {
  repo?: string;
  onNodeSelect: (nodeId: string) => void;
  selectedNodeId?: string | null;
  /** Override root container class. Defaults to absolute overlay in top-left. */
  className?: string;
}

function SkeletonCard() {
  return (
    <div className="flex items-start gap-3 p-4 border-b border-outline-variant/10 last:border-0 animate-pulse">
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <div className="h-4 w-14 bg-surface-container-high rounded" />
          <div className="h-4 w-28 bg-surface-container-high/60 rounded" />
        </div>
        <div className="h-3 w-40 bg-surface-container-high/40 rounded" />
        <div className="flex gap-2">
          <div className="h-3 w-16 bg-surface-container-high/30 rounded" />
          <div className="h-3 w-12 bg-surface-container-high/30 rounded" />
        </div>
      </div>
    </div>
  );
}

export default function GraphSearch({ repo, onNodeSelect, selectedNodeId, className }: GraphSearchProps) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [lastQuery, setLastQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState("");
  const [hasSearched, setHasSearched] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  const handleSearch = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      const q = query.trim();
      if (!q) return;
      setIsSearching(true);
      setError("");
      setHasSearched(true);
      setIsCollapsed(false);
      try {
        const data = await api.gitnexus.search(q, repo, mode, 15);
        setResults(data.results);
        setLastQuery(q);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
        setResults([]);
      } finally {
        setIsSearching(false);
      }
    },
    [query, repo, mode],
  );

  const showResults = hasSearched && !isSearching && results.length > 0;
  const showEmpty = hasSearched && !isSearching && lastQuery && results.length === 0 && !error;

  return (
    <div className={className ?? "absolute top-4 left-4 z-50 w-[360px] backdrop-blur-md bg-surface/80 border border-outline-variant/15 rounded-xl shadow-2xl shadow-black/40 overflow-hidden"}>
      {/* Header */}
      <div className="px-4 pt-4 pb-3">
        <div className="flex items-center gap-2 mb-3">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-primary/60 shrink-0">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <span className="text-[10px] font-bold text-on-surface-variant/60 uppercase tracking-widest font-display flex-1">
            Symbol Search
          </span>
          {hasSearched && (
            <button
              onClick={() => setIsCollapsed((c) => !c)}
              className="text-[10px] text-on-surface-variant/40 hover:text-on-surface-variant transition-colors px-1"
              title={isCollapsed ? "Expand" : "Collapse"}
            >
              {isCollapsed ? "▲" : "▼"}
            </button>
          )}
          {hasSearched && (
            <button
              onClick={() => { setResults([]); setHasSearched(false); setQuery(""); }}
              className="text-[10px] text-on-surface-variant/30 hover:text-on-surface-variant transition-colors px-1"
            >
              ✕
            </button>
          )}
        </div>

        {/* Search input */}
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative flex-1">
            <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-primary/60">
                <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
            </div>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search symbols, files, nodes..."
              className="w-full h-9 bg-surface-container-low border border-outline-variant/20 rounded-lg pl-8 pr-3 text-sm font-data text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
            />
          </div>
          <button
            type="submit"
            disabled={isSearching || !query.trim()}
            className="h-9 px-3 bg-primary/90 text-on-primary text-[10px] font-bold uppercase tracking-widest rounded-lg hover:bg-primary hover:shadow-lg hover:shadow-primary/20 disabled:opacity-40 transition-all shrink-0"
          >
            {isSearching ? "..." : "Go"}
          </button>
        </form>
      </div>

      {/* Mode switcher */}
      {!isCollapsed && (
        <div className="px-4 pb-3">
          <div className="flex bg-surface-container-lowest p-1 rounded-md border border-outline-variant/10">
            {(["hybrid", "bm25", "semantic"] as SearchMode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`flex-1 text-center text-xs py-1.5 rounded cursor-pointer transition-all font-display ${
                  mode === m
                    ? "bg-surface-container-high text-primary font-bold shadow-sm"
                    : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container/50"
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Error */}
      {error && !isCollapsed && (
        <p className="text-[11px] text-tertiary/80 px-4 pb-3">{error}</p>
      )}

      {/* Loading skeleton */}
      {isSearching && (
        <div className="border-t border-outline-variant/10">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {/* Results */}
      {showResults && !isCollapsed && (
        <div className="border-t border-outline-variant/10">
          {/* Result count */}
          <div className="flex items-center justify-between px-4 py-1.5 border-b border-outline-variant/8">
            <span className="text-[10px] text-on-surface-variant/50 uppercase tracking-wider">
              {results.length} symbols · &ldquo;{lastQuery}&rdquo;
            </span>
          </div>

          {/* Result list */}
          <div className="max-h-[400px] overflow-y-auto scrollbar-thin divide-y divide-outline-variant/5">
            {results.map((r, idx) => {
              const color = LABEL_COLORS[r.label] ?? "#6B7280";
              const isSelected = selectedNodeId === r.id;
              return (
                <div
                  key={`${r.id}-${idx}`}
                  onClick={() => { onNodeSelect(r.id); setIsCollapsed(true); }}
                  className={`group relative flex items-start gap-3 p-4 cursor-pointer transition-colors ${
                    isSelected
                      ? "bg-primary/10"
                      : "hover:bg-surface-container-high/50"
                  }`}
                >
                  {/* Left score indicator bar */}
                  <div
                    className="absolute left-0 top-0 bottom-0 w-[2px] bg-gradient-to-b from-primary to-transparent opacity-0 group-hover:opacity-100 transition-opacity"
                    style={isSelected ? { opacity: 1 } : undefined}
                  />

                  {/* Content */}
                  <div className="flex-1 min-w-0 pl-1">
                    {/* Line 1: TypeBadge + SymbolName + Connections */}
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span
                        className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
                        style={{ background: `${color}25`, color }}
                      >
                        {r.label}
                      </span>
                      <span className="font-data text-sm font-semibold truncate text-on-surface flex-1 min-w-0">
                        {r.name}
                      </span>
                      {r.connections != null && (
                        <span className="ml-auto font-data text-[10px] text-on-surface-variant bg-surface-container px-1.5 py-0.5 rounded border border-outline-variant/10 shrink-0">
                          {r.connections}↔
                        </span>
                      )}
                    </div>

                    {/* Line 2: FilePath */}
                    <div className="font-data text-[11px] text-on-surface-variant/70 mt-1 truncate">
                      {r.filePath}
                    </div>

                    {/* Line 3: Tags */}
                    {(r.cluster || (r.processes && r.processes.length > 0)) && (
                      <div className="flex gap-2 mt-2 flex-wrap">
                        {r.cluster && (
                          <span className="text-[10px] border border-outline-variant/20 px-1.5 py-0.5 rounded text-on-surface-variant/80 hover:border-primary/30 transition-colors truncate max-w-[100px]">
                            Cluster: {r.cluster}
                          </span>
                        )}
                        {r.processes && r.processes.length > 0 && (
                          <span className="text-[10px] border border-outline-variant/20 px-1.5 py-0.5 rounded text-on-surface-variant/80 hover:border-primary/30 transition-colors">
                            {r.processes.length} Process{r.processes.length > 1 ? "es" : ""}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Locate button (hover only) */}
                  <div className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity self-start mt-0.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); onNodeSelect(r.id); setIsCollapsed(true); }}
                      className="text-[9px] px-1.5 py-0.5 rounded bg-primary/20 text-primary hover:bg-primary/30 transition-colors font-bold"
                      title="Jump to node"
                    >
                      Jump
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Empty state */}
      {showEmpty && !isCollapsed && (
        <div className="border-t border-outline-variant/10 px-4 py-8 flex flex-col items-center gap-3 text-center">
          {/* Neural Ghost icon */}
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-on-surface-variant/20">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="8" strokeWidth="2" />
            <path d="M8 14s1.5 2 4 2 4-2 4-2" />
            <line x1="9" y1="11" x2="9" y2="11" strokeWidth="2" />
            <line x1="15" y1="11" x2="15" y2="11" strokeWidth="2" />
          </svg>
          <p className="text-[11px] text-on-surface-variant/40">
            No signals detected for &ldquo;{lastQuery}&rdquo;
          </p>
          <p className="text-[10px] text-on-surface-variant/25">
            Try switching to BM25 or Semantic mode
          </p>
        </div>
      )}
    </div>
  );
}

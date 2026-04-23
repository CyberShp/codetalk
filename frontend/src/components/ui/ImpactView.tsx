"use client";

import { useCallback, useEffect, useState } from "react";
import type { GraphNode } from "@/lib/types";
import GlassPanel from "./GlassPanel";
import { api } from "@/lib/api";

interface ImpactViewProps {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}

type ImpactItem = { name: string; filePath: string; startLine: number; endLine: number };
type DepthLayer = { depth: number; items: ImpactItem[]; total: number; limited: boolean };
type ImpactData = {
  target: string;
  depth: number;
  upstream?: DepthLayer[];
  downstream?: DepthLayer[];
};

type DisplayItem = { id: string; name: string; filePath: string; startLine: number };

const DEPTH_LABELS: Record<number, string> = {
  1: "直接",
  2: "间接（2跳）",
  3: "间接（3跳）",
};

function dedupeLayer(items: ImpactItem[], targetName: string): DisplayItem[] {
  const seen = new Set<string>();
  const out: DisplayItem[] = [];
  for (const item of items) {
    const key = `${item.filePath}:${item.name}`;
    if (item.name === targetName || seen.has(key)) continue;
    seen.add(key);
    out.push({ id: key, name: item.name, filePath: item.filePath, startLine: item.startLine });
  }
  return out;
}

function DepthSection({
  layer,
  targetName,
  nodeMap,
  onNodeClick,
  defaultOpen,
}: {
  layer: DepthLayer;
  targetName: string;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const items = dedupeLayer(layer.items, targetName);
  const label = DEPTH_LABELS[layer.depth] ?? `${layer.depth}跳`;

  return (
    <div className="border-l-2 border-surface-container-high/30 pl-2">
      <button
        className="flex items-center gap-2 w-full text-left py-1 group"
        onClick={() => setOpen(!open)}
      >
        <span
          className="text-[10px] text-on-surface-variant/50 transition-transform inline-block"
          style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)" }}
        >
          ▶
        </span>
        <span className="text-[11px] font-data text-on-surface-variant/70 font-medium">
          {label}
        </span>
        <span className="text-[10px] font-data text-on-surface-variant/40">
          {items.length}{layer.limited ? "+" : ""}
        </span>
      </button>
      {open && (
        <div className="space-y-0.5 ml-1">
          {items.length === 0 ? (
            <p className="text-[11px] text-on-surface-variant/30 py-1 pl-1">无</p>
          ) : (
            items.map((item) => {
              const graphNode = nodeMap.get(item.id);
              const isClickable = !!graphNode;
              return (
                <div
                  key={item.id}
                  className={`flex items-center gap-2 py-1 px-2 rounded-lg ${
                    isClickable
                      ? "cursor-pointer hover:bg-surface-container-high/50 transition-colors"
                      : ""
                  }`}
                  onClick={() => {
                    if (graphNode) onNodeClick(graphNode);
                  }}
                >
                  <span className="text-[9px] font-data font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 bg-[#10B98125] text-[#10B981]">
                    FN
                  </span>
                  <div className="flex flex-col min-w-0">
                    <span className="text-xs font-data text-on-surface truncate">
                      {item.name}
                    </span>
                    <span className="text-[10px] font-data text-on-surface-variant/40 truncate">
                      {item.filePath}:{item.startLine}
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

function DirectionSection({
  title,
  layers,
  targetName,
  nodeMap,
  onNodeClick,
  loadingDeeper,
  onLoadDeeper,
  maxDepthLoaded,
}: {
  title: string;
  layers: DepthLayer[];
  targetName: string;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  loadingDeeper: boolean;
  onLoadDeeper: () => void;
  maxDepthLoaded: number;
}) {
  const totalCount = layers.reduce(
    (sum, l) => sum + dedupeLayer(l.items, targetName).length,
    0,
  );

  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-2">
        {title} ({totalCount})
      </p>
      {layers.length === 0 || totalCount === 0 ? (
        <p className="text-[11px] text-on-surface-variant/40 py-1 pl-1">
          {title.includes("上游") ? "未发现上游调用者" : "未发现下游依赖"}
        </p>
      ) : (
        <div className="space-y-1.5">
          {layers.map((layer) => (
            <DepthSection
              key={layer.depth}
              layer={layer}
              targetName={targetName}
              nodeMap={nodeMap}
              onNodeClick={onNodeClick}
              defaultOpen={layer.depth === 1}
            />
          ))}
        </div>
      )}
      {maxDepthLoaded < 3 && totalCount > 0 && (
        <button
          className="mt-2 text-[10px] font-data text-primary/60 hover:text-primary transition-colors disabled:opacity-40"
          disabled={loadingDeeper}
          onClick={onLoadDeeper}
        >
          {loadingDeeper ? "加载中..." : `展开间接层级 (${maxDepthLoaded + 1}-3跳)`}
        </button>
      )}
    </div>
  );
}

export default function ImpactView({
  node,
  nodeMap,
  onNodeClick,
  repo,
}: ImpactViewProps) {
  const [data, setData] = useState<ImpactData | null | undefined>(undefined);
  const [loadingDeeper, setLoadingDeeper] = useState(false);

  const name = node.properties.name;
  const nodeColor =
    { Function: "#10B981", Method: "#F59E0B", Class: "#8B5CF6" }[node.label] ??
    "#6B7280";
  const loading = data === undefined;
  const hasError = data === null;

  // Load depth 1 immediately (fast — just 2 queries)
  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setData(undefined);
    api.gitnexus
      .impact(name, "both", 1, repo)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [name, repo]);

  const maxDepthLoaded = data
    ? Math.max(
        ...(data.upstream ?? []).map((l) => l.depth),
        ...(data.downstream ?? []).map((l) => l.depth),
        0,
      )
    : 0;

  // Lazy-load deeper layers on user request
  const handleLoadDeeper = useCallback(() => {
    if (!name || !data || loadingDeeper) return;
    setLoadingDeeper(true);
    api.gitnexus
      .impact(name, "both", 3, repo)
      .then((res) => {
        setData(res);
      })
      .catch(() => {
        // Keep existing data on failure
      })
      .finally(() => setLoadingDeeper(false));
  }, [name, repo, data, loadingDeeper]);

  return (
    <GlassPanel>
      {/* Header */}
      <div className="flex flex-col gap-1 mb-4">
        <div className="flex items-center gap-2">
          <span
            className="px-2 py-0.5 rounded text-[10px] font-data font-bold"
            style={{ background: `${nodeColor}18`, color: nodeColor }}
          >
            {node.label}
          </span>
        </div>
        <h4 className="text-sm font-medium text-on-surface mt-1">{name}</h4>
        {node.properties.description && (
          <p className="text-[11px] text-on-surface-variant/60 italic leading-relaxed">
            &ldquo;{node.properties.description}&rdquo;
          </p>
        )}
      </div>

      {/* File location */}
      {node.properties.filePath && (
        <div className="flex gap-3 mb-4 text-[10px] font-data">
          <div className="flex flex-col">
            <span className="text-on-surface-variant/50 uppercase tracking-tighter">
              文件
            </span>
            <span className="text-primary truncate">
              {node.properties.filePath}
              {node.properties.startLine != null &&
                `:${node.properties.startLine}`}
            </span>
          </div>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-2">
          <div className="h-4 w-24 bg-surface-container-high/30 rounded animate-pulse" />
          <div className="h-14 bg-surface-container-high/20 rounded animate-pulse" />
          <div className="h-4 w-24 bg-surface-container-high/30 rounded animate-pulse mt-3" />
          <div className="h-14 bg-surface-container-high/20 rounded animate-pulse" />
        </div>
      )}

      {/* Error state */}
      {hasError && (
        <p className="text-xs text-on-surface-variant/50 py-2">
          影响面数据不可用
        </p>
      )}

      {/* Impact sections — layered by depth */}
      {!loading && !hasError && data && (
        <div className="space-y-5">
          <DirectionSection
            title="上游调用者"
            layers={data.upstream ?? []}
            targetName={name}
            nodeMap={nodeMap}
            onNodeClick={onNodeClick}
            loadingDeeper={loadingDeeper}
            onLoadDeeper={handleLoadDeeper}
            maxDepthLoaded={maxDepthLoaded}
          />
          <DirectionSection
            title="下游被调用"
            layers={data.downstream ?? []}
            targetName={name}
            nodeMap={nodeMap}
            onNodeClick={onNodeClick}
            loadingDeeper={loadingDeeper}
            onLoadDeeper={handleLoadDeeper}
            maxDepthLoaded={maxDepthLoaded}
          />
        </div>
      )}
    </GlassPanel>
  );
}

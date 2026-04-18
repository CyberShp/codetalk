"use client";

import { useEffect, useState } from "react";
import type { GraphNode } from "@/lib/types";
import GlassPanel from "./GlassPanel";
import { api } from "@/lib/api";

interface ImpactViewProps {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}

type PathResult = { nodes: unknown[]; rels: unknown[] };
type ImpactData = { target: string; depth: number; upstream?: PathResult[]; downstream?: PathResult[] } | undefined;

const LABEL_COLORS: Record<string, string> = {
  Function: "#10B981",
  Method: "#F59E0B",
  Class: "#8B5CF6",
};

function extractNodes(
  pathResults: PathResult[],
  targetName: string,
): Array<{ id: string; name: string; label: string }> {
  const seen = new Set<string>();
  const out: Array<{ id: string; name: string; label: string }> = [];
  for (const path of pathResults) {
    for (const raw of path.nodes) {
      const n = raw as { id?: string; name?: string; label?: string };
      const id = n.id ?? "";
      const name = n.name ?? "";
      if (name === targetName || !id || seen.has(id)) continue;
      seen.add(id);
      out.push({ id, name, label: n.label ?? "Symbol" });
    }
  }
  return out;
}

function NodeList({
  items,
  nodeMap,
  onNodeClick,
  emptyLabel,
}: {
  items: Array<{ id: string; name: string; label: string }>;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  emptyLabel: string;
}) {
  if (items.length === 0) {
    return (
      <p className="text-[11px] text-on-surface-variant/40 py-1 pl-1">{emptyLabel}</p>
    );
  }
  return (
    <div className="space-y-0.5">
      {items.map((item) => {
        const graphNode = nodeMap.get(item.id);
        const isClickable = !!graphNode;
        const color = LABEL_COLORS[item.label] ?? "#6B7280";
        return (
          <div
            key={item.id}
            className={`flex items-center gap-2 py-1.5 px-2 rounded-lg ${
              isClickable
                ? "cursor-pointer hover:bg-surface-container-high/50 transition-colors"
                : ""
            }`}
            onClick={() => {
              if (graphNode) onNodeClick(graphNode);
            }}
          >
            <span
              className="text-[9px] font-data font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
              style={{ background: `${color}25`, color }}
            >
              {item.label}
            </span>
            <span className="text-xs font-data text-on-surface truncate">{item.name}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function ImpactView({ node, nodeMap, onNodeClick, repo }: ImpactViewProps) {
  const [data, setData] = useState<ImpactData>(undefined);

  const name = node.properties.name;
  const color = LABEL_COLORS[node.label] ?? "#6B7280";
  const loading = data === undefined;
  const hasError = data === null;

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    api.gitnexus
      .impact(name, "both", 3, repo)
      .then((res) => {
        if (!cancelled) setData(res as ImpactData);
      })
      .catch(() => {
        if (!cancelled) setData(null as unknown as ImpactData);
      });
    return () => { cancelled = true; };
  }, [name, repo]);

  const upstream = data?.upstream ? extractNodes(data.upstream, name) : [];
  const downstream = data?.downstream ? extractNodes(data.downstream, name) : [];

  return (
    <GlassPanel>
      {/* Header */}
      <div className="flex flex-col gap-1 mb-4">
        <div className="flex items-center gap-2">
          <span
            className="px-2 py-0.5 rounded text-[10px] font-data font-bold"
            style={{ background: `${color}18`, color }}
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
            <span className="text-on-surface-variant/50 uppercase tracking-tighter">文件</span>
            <span className="text-primary truncate">
              {node.properties.filePath}
              {node.properties.startLine != null && `:${node.properties.startLine}`}
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
        <p className="text-xs text-on-surface-variant/50 py-2">影响面数据不可用</p>
      )}

      {/* Impact sections */}
      {!loading && !hasError && (
        <div className="space-y-4">
          {/* Upstream */}
          <div>
            <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-1.5">
              上游调用者 ({upstream.length})
            </p>
            <NodeList
              items={upstream}
              nodeMap={nodeMap}
              onNodeClick={onNodeClick}
              emptyLabel="未发现上游调用者"
            />
          </div>

          {/* Downstream */}
          <div>
            <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-1.5">
              下游被调用 ({downstream.length})
            </p>
            <NodeList
              items={downstream}
              nodeMap={nodeMap}
              onNodeClick={onNodeClick}
              emptyLabel="未发现下游依赖"
            />
          </div>
        </div>
      )}
    </GlassPanel>
  );
}

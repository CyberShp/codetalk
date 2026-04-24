"use client";

import { useMemo, useEffect, useState } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";
import GlassPanel from "./GlassPanel";
import { api } from "@/lib/api";
import ImpactView from "./ImpactView";

interface Props {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  edges: GraphEdge[];
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}

export default function IntelligencePanel({ node, nodeMap, edges, onNodeClick, repo }: Props) {
  if (node.label === "Process") {
    return <ProcessView key={node.id} node={node} nodeMap={nodeMap} onNodeClick={onNodeClick} repo={repo} />;
  }
  if (node.label === "Community") {
    return <CommunityView key={node.id} node={node} nodeMap={nodeMap} edges={edges} onNodeClick={onNodeClick} repo={repo} />;
  }
  if (node.label === "Function" || node.label === "Method" || node.label === "Class") {
    return <ImpactView key={node.id} node={node} nodeMap={nodeMap} onNodeClick={onNodeClick} repo={repo} />;
  }
  return null;
}

function ProcessView({
  node,
  nodeMap,
  onNodeClick,
  repo,
}: {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}) {
  const isCross = node.properties.processType === "cross_community";

  // API-enriched steps: undefined = loading/initial, empty array = no data/failed, array = data
  const [apiSteps, setApiSteps] = useState<unknown[] | undefined>(undefined);

  const name = node.properties.name as string | undefined;

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    api.gitnexus
      .process(name, repo)
      .then((data) => {
        if (!cancelled) {
          setApiSteps(
            Array.isArray(data.steps) && data.steps.length > 0 ? data.steps : []
          );
        }
      })
      .catch(() => {
        if (!cancelled) setApiSteps([]); /* silent fallback */
      });
    return () => { cancelled = true; };
  }, [name, repo]);

  const loadingSteps = !!name && apiSteps === undefined;

  // Resolve steps to full nodes — prefer API data, fall back to graph
  // API steps: { step, name, filePath }; graph steps: { symbolId, step }
  const resolvedSteps = useMemo(() => {
    if (apiSteps === undefined) return []; // Still loading
    const rawSteps = (apiSteps.length > 0 ? apiSteps : (node.steps ?? [])) as
      { symbolId?: string; name?: string; filePath?: string }[];
    return rawSteps.map((s) => ({
      ...s,
      displayName: s.name ?? s.symbolId ?? "",
      displayPath: s.filePath,
      node: nodeMap.get(s.symbolId ?? "") ?? null,
    }));
  }, [apiSteps, node.steps, nodeMap]);

  return (
    <GlassPanel>
      {/* Header */}
      <div className="flex flex-col gap-1 mb-4">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded text-[10px] font-data bg-[#EC4899]/10 text-[#EC4899]">
            Process
          </span>
          {isCross && (
            <span className="px-2 py-0.5 rounded text-[10px] font-data bg-tertiary/10 text-tertiary border border-tertiary/20">
              跨社区
            </span>
          )}
        </div>
        <h4 className="text-sm font-medium text-on-surface mt-1">
          {node.properties.name}
        </h4>
      </div>

      {node.properties.description && (
        <div className="mb-4 p-3 bg-secondary-container/10 border-l-2 border-secondary/50 rounded-r">
          <p className="text-xs text-on-surface-variant leading-relaxed italic">
            &ldquo;{node.properties.description}&rdquo;
          </p>
        </div>
      )}

      {/* Stats row */}
      <div className="flex gap-4 mb-4 text-[10px] font-data">
        <div className="flex flex-col">
          <span className="text-on-surface-variant/50 uppercase tracking-tighter">步骤数</span>
          <span className="text-primary">{node.properties.stepCount ?? (node.steps ?? []).length}</span>
        </div>
        {node.properties.processType && (
          <div className="flex flex-col">
            <span className="text-on-surface-variant/50 uppercase tracking-tighter">类型</span>
            <span className="text-primary">{node.properties.processType}</span>
          </div>
        )}
      </div>

      {/* Steps chain */}
      {loadingSteps && (
        <div className="h-16 rounded-lg bg-surface-container-high/30 animate-pulse" />
      )}
      {!loadingSteps && resolvedSteps.length > 0 && (
        <div className="space-y-0">
          <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-2">
            执行步骤链
          </p>
          <div className="space-y-0 relative">
            {/* Vertical connector line */}
            <div className="absolute left-[9px] top-3 bottom-3 w-px bg-outline-variant/20" />

            {resolvedSteps.map((s, i) => {
              const resolved = s.node;
              const isClickable = resolved && resolved.properties.filePath;
              return (
                <div
                  key={i}
                  className={`relative flex items-start gap-3 py-2 pl-0 ${
                    isClickable
                      ? "cursor-pointer hover:bg-surface-container-high/50 rounded-lg transition-colors"
                      : ""
                  }`}
                  onClick={() => {
                    if (resolved && isClickable) onNodeClick(resolved);
                  }}
                >
                  {/* Step number circle */}
                  <div className="w-[18px] h-[18px] rounded-full bg-surface-container-high border border-outline-variant/30 flex items-center justify-center shrink-0 z-10">
                    <span className="text-[8px] font-data text-on-surface-variant/70">
                      {i + 1}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1">
                    <p className={`text-xs font-medium truncate ${isClickable ? "text-on-surface hover:text-primary" : "text-on-surface-variant"}`}>
                      {resolved?.properties.name ?? s.displayName}
                    </p>
                    {(resolved?.properties.filePath || s.displayPath) && (
                      <p className="text-[10px] text-on-surface-variant/50 font-data truncate">
                        {resolved?.properties.filePath ?? s.displayPath}
                        {resolved?.properties.startLine != null && `:${resolved.properties.startLine}`}
                      </p>
                    )}
                    {resolved && (
                      <span className="text-[9px] font-data text-primary/50">
                        {resolved.label}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!loadingSteps && resolvedSteps.length === 0 && (
        <p className="text-xs text-on-surface-variant/50 py-2">
          该流程无步骤数据。
        </p>
      )}
    </GlassPanel>
  );
}

function CommunityView({
  node,
  nodeMap,
  edges,
  onNodeClick,
  repo,
}: {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  edges: GraphEdge[];
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}) {
  // API-enriched cluster data
  const [apiData, setApiData] = useState<{
    members?: unknown[];
    cohesion?: number;
    description?: string;
  } | null>(null);

  useEffect(() => {
    const name = node.properties.name as string | undefined;
    if (!name) return;
    let cancelled = false;
    api.gitnexus
      .cluster(name, repo)
      .then((data) => {
        if (!cancelled) setApiData(data as typeof apiData);
      })
      .catch(() => {
        /* silent fallback — apiData stays null */
      });
    return () => { cancelled = true; };
  }, [node.properties.name, repo]);

  // Find members via MEMBER_OF edges pointing to this community
  const graphMembers = useMemo(() => {
    const memberIds = edges
      .filter((e) => e.type === "MEMBER_OF" && e.targetId === node.id)
      .map((e) => e.sourceId);
    return memberIds
      .map((id) => nodeMap.get(id))
      .filter((n): n is GraphNode => n != null);
  }, [node.id, edges, nodeMap]);

  // Prefer API member list (may include members outside current viewport), fall back to graph
  const members = useMemo(() => {
    if (!apiData?.members || !Array.isArray(apiData.members) || apiData.members.length === 0) {
      return graphMembers;
    }
    const apiMs = apiData.members as { symbolId?: string; name?: string; label?: string }[];
    return apiMs.map((am) => {
      const id = (am.symbolId ?? am.name ?? "") as string;
      return (
        nodeMap.get(id) ??
        ({ id, label: am.label ?? "Symbol", properties: { name: am.name ?? id } } as GraphNode)
      );
    });
  }, [apiData, graphMembers, nodeMap]);
  const cohesion = (apiData?.cohesion ?? node.properties.cohesion) as number | undefined;
  const description = (apiData?.description as string | undefined) ?? (node.properties.description as string | undefined);

  return (
    <GlassPanel>
      {/* Header */}
      <div className="flex flex-col gap-1 mb-4">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded text-[10px] font-data bg-[#6366F1]/10 text-[#6366F1]">
            Community
          </span>
        </div>
        <h4 className="text-sm font-medium text-on-surface mt-1">
          {node.properties.name}
        </h4>
      </div>

      {description && (
        <div className="mb-4 p-3 bg-secondary-container/10 border-l-2 border-secondary/50 rounded-r">
          <p className="text-xs text-on-surface-variant leading-relaxed italic">
            &ldquo;{description}&rdquo;
          </p>
        </div>
      )}

      {/* Stats row */}
      <div className="flex gap-4 mb-4 text-[10px] font-data">
        <div className="flex flex-col">
          <span className="text-on-surface-variant/50 uppercase tracking-tighter">成员</span>
          <span className="text-secondary">{node.properties.memberCount ?? members.length}</span>
        </div>
        {cohesion != null && (
          <div className="flex flex-col">
            <span className="text-on-surface-variant/50 uppercase tracking-tighter">内聚度</span>
            <span className={cohesion >= 0.5 ? "text-secondary" : "text-tertiary"}>
              {(cohesion * 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>

      {/* Member list */}
      {members.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-2">
            成员 ({members.length})
          </p>
          <div className="space-y-1 max-h-[400px] overflow-y-auto">
            {members.map((m) => {
              const isClickable = !!m.properties.filePath;
              return (
                <div
                  key={m.id}
                  className={`flex items-center gap-2 py-1.5 px-2 rounded-lg ${
                    isClickable
                      ? "cursor-pointer hover:bg-surface-container-high/50 transition-colors"
                      : ""
                  }`}
                  onClick={() => {
                    if (isClickable) onNodeClick(m);
                  }}
                >
                  <span className="text-[9px] font-data text-on-surface-variant/50 w-12 shrink-0">
                    {m.label}
                  </span>
                  <span className="text-xs text-on-surface truncate">
                    {m.properties.name}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {members.length === 0 && (
        <p className="text-xs text-on-surface-variant/50 py-2">
          无成员数据。
        </p>
      )}
    </GlassPanel>
  );
}

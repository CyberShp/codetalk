"use client";

import { useMemo } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";
import GlassPanel from "./GlassPanel";

interface Props {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  edges: GraphEdge[];
  onNodeClick: (node: GraphNode) => void;
}

export default function IntelligencePanel({ node, nodeMap, edges, onNodeClick }: Props) {
  if (node.label === "Process") {
    return <ProcessView node={node} nodeMap={nodeMap} onNodeClick={onNodeClick} />;
  }
  if (node.label === "Community") {
    return <CommunityView node={node} nodeMap={nodeMap} edges={edges} onNodeClick={onNodeClick} />;
  }
  return null;
}

function ProcessView({
  node,
  nodeMap,
  onNodeClick,
}: {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
}) {
  const steps = node.steps ?? [];
  const isCross = node.properties.processType === "cross_community";

  // Resolve steps to full nodes
  const resolvedSteps = useMemo(
    () =>
      steps.map((s) => ({
        ...s,
        node: nodeMap.get(s.symbolId) ?? null,
      })),
    [steps, nodeMap],
  );

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
          <span className="text-primary">{node.properties.stepCount ?? steps.length}</span>
        </div>
        {node.properties.processType && (
          <div className="flex flex-col">
            <span className="text-on-surface-variant/50 uppercase tracking-tighter">类型</span>
            <span className="text-primary">{node.properties.processType}</span>
          </div>
        )}
      </div>

      {/* Steps chain */}
      {resolvedSteps.length > 0 && (
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
                      {resolved?.properties.name ?? s.symbolId.slice(0, 16)}
                    </p>
                    {resolved?.properties.filePath && (
                      <p className="text-[10px] text-on-surface-variant/50 font-data truncate">
                        {resolved.properties.filePath}
                        {resolved.properties.startLine != null && `:${resolved.properties.startLine}`}
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

      {resolvedSteps.length === 0 && (
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
}: {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  edges: GraphEdge[];
  onNodeClick: (node: GraphNode) => void;
}) {
  // Find members via MEMBER_OF edges pointing to this community
  const members = useMemo(() => {
    const memberIds = edges
      .filter((e) => e.type === "MEMBER_OF" && e.targetId === node.id)
      .map((e) => e.sourceId);
    return memberIds
      .map((id) => nodeMap.get(id))
      .filter((n): n is GraphNode => n != null);
  }, [node.id, edges, nodeMap]);

  const cohesion = node.properties.cohesion as number | undefined;

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

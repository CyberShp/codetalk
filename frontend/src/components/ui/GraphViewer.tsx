"use client";

import { useMemo, useState, useCallback } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";

/* ── Color map per node type ── */
const NODE_COLORS: Record<string, string> = {
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

const NODE_W = 160;
const NODE_H = 36;
const COL_GAP = 220;
const ROW_GAP = 52;
const PADDING = 40;

/* Filter to code-level nodes (have startLine/endLine) */
const CODE_LABELS = new Set(["Function", "Method", "Class", "Module", "Route", "Tool"]);

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId: string | null;
  onNodeClick: (node: GraphNode) => void;
}

interface LayoutNode {
  node: GraphNode;
  x: number;
  y: number;
}

export default function GraphViewer({ nodes, edges, selectedNodeId, onNodeClick }: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  /* Group nodes by label, lay out in columns */
  const { layout, width, height, edgeLines } = useMemo(() => {
    // Filter out Folder nodes (too many, clutters graph)
    const filtered = nodes.filter((n) => n.label !== "Folder");
    const groups = new Map<string, GraphNode[]>();
    for (const n of filtered) {
      const arr = groups.get(n.label) || [];
      arr.push(n);
      groups.set(n.label, arr);
    }

    // Limit per column to prevent massive SVGs
    const MAX_PER_COL = 30;

    const laid: LayoutNode[] = [];
    const posMap = new Map<string, { x: number; y: number }>();
    let col = 0;
    let maxRow = 0;

    for (const [, group] of groups) {
      const capped = group.slice(0, MAX_PER_COL);
      for (let row = 0; row < capped.length; row++) {
        const x = PADDING + col * COL_GAP;
        const y = PADDING + row * ROW_GAP;
        laid.push({ node: capped[row], x, y });
        posMap.set(capped[row].id, { x, y });
        if (row > maxRow) maxRow = row;
      }
      col++;
    }

    // Build edge lines (only where both endpoints exist in layout)
    const lines = edges
      .map((e) => {
        const s = posMap.get(e.sourceId);
        const t = posMap.get(e.targetId);
        if (!s || !t) return null;
        return {
          id: e.id,
          type: e.type,
          x1: s.x + NODE_W / 2,
          y1: s.y + NODE_H / 2,
          x2: t.x + NODE_W / 2,
          y2: t.y + NODE_H / 2,
        };
      })
      .filter(Boolean) as Array<{
        id: string;
        type: string;
        x1: number;
        y1: number;
        x2: number;
        y2: number;
      }>;

    return {
      layout: laid,
      width: PADDING * 2 + col * COL_GAP,
      height: PADDING * 2 + (maxRow + 1) * ROW_GAP,
      edgeLines: lines,
    };
  }, [nodes, edges]);

  const handleClick = useCallback(
    (node: GraphNode) => {
      onNodeClick(node);
    },
    [onNodeClick],
  );

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-on-surface-variant/50 text-sm">
        No graph data available.
      </div>
    );
  }

  return (
    <div className="overflow-auto rounded-lg bg-surface-container-lowest/30 border border-outline-variant/10">
      <svg
        width={Math.max(width, 600)}
        height={Math.max(height, 300)}
        className="select-none"
      >
        {/* Edges */}
        <g>
          {edgeLines.map((e) => (
            <line
              key={e.id}
              x1={e.x1}
              y1={e.y1}
              x2={e.x2}
              y2={e.y2}
              stroke="#A4E6FF"
              strokeOpacity={0.15}
              strokeWidth={1}
            />
          ))}
        </g>

        {/* Nodes */}
        <g>
          {layout.map(({ node, x, y }) => {
            const isSelected = node.id === selectedNodeId;
            const isHovered = node.id === hoveredId;
            const isCodeNode = CODE_LABELS.has(node.label);
            const color = NODE_COLORS[node.label] || "#6B7280";

            return (
              <g
                key={node.id}
                transform={`translate(${x}, ${y})`}
                onClick={() => handleClick(node)}
                onMouseEnter={() => setHoveredId(node.id)}
                onMouseLeave={() => setHoveredId(null)}
                className={isCodeNode ? "cursor-pointer" : "cursor-default"}
              >
                {/* Node background */}
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill={isSelected ? color : "#1A1E24"}
                  stroke={isSelected ? color : isHovered ? color : "#2A2E34"}
                  strokeWidth={isSelected ? 2 : 1}
                  opacity={isSelected || isHovered ? 1 : 0.85}
                />
                {/* Type indicator */}
                <rect
                  width={4}
                  height={NODE_H - 8}
                  x={4}
                  y={4}
                  rx={2}
                  fill={color}
                  opacity={0.8}
                />
                {/* Node label */}
                <text
                  x={14}
                  y={14}
                  fill="#A4E6FF"
                  fontSize={9}
                  fontFamily="JetBrains Mono, monospace"
                  opacity={0.6}
                >
                  {node.label}
                </text>
                {/* Node name */}
                <text
                  x={14}
                  y={28}
                  fill={isSelected ? "#FFFFFF" : "#E0E0E0"}
                  fontSize={11}
                  fontFamily="Inter, sans-serif"
                  fontWeight={isSelected ? 600 : 400}
                >
                  {(node.properties.name || "").length > 18
                    ? node.properties.name.slice(0, 17) + "…"
                    : node.properties.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

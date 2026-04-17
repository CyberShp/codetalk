"use client";

import { useMemo, useState, useRef, useEffect } from "react";
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

/* ── Color map per edge type (Neural Graph) ── */
const EDGE_COLORS: Record<string, string> = {
  CALLS: "#A4E6FF",
  IMPORTS: "#10B981",
  EXTENDS: "#8B5CF6",
  DEFINES: "#F59E0B",
  MEMBER_OF: "#6366F1",
};

const getEdgeColor = (type: string) => EDGE_COLORS[type.toUpperCase()] || "#A4E6FF";

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
  onNodeClick: (node: GraphNode | null) => void;
}

interface LayoutNode {
  node: GraphNode;
  x: number;
  y: number;
}

export default function GraphViewer({ nodes, edges, selectedNodeId, onNodeClick }: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  /* Group nodes by label, lay out in columns */
  const { layout, width, height, edgeLines, posMap, connectedNodeIds } = useMemo(() => {
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

    // Identify focus nodes to ensure they are always in layout
    const focusNodeIds = new Set<string>();
    if (selectedNodeId) {
      focusNodeIds.add(selectedNodeId);
      const selNode = nodes.find(n => n.id === selectedNodeId);
      if (selNode?.label === "Process" && selNode.steps) {
        selNode.steps.forEach(s => focusNodeIds.add(s.symbolId));
      }
    }

    const laid: LayoutNode[] = [];
    const posMap = new Map<string, { x: number; y: number }>();
    let col = 0;
    let maxRow = 0;

    for (const [, group] of groups) {
      // Ensure focus nodes in this group are prioritized in the rendered set
      const focusInGroup = group.filter(n => focusNodeIds.has(n.id));
      const othersInGroup = group.filter(n => !focusNodeIds.has(n.id));
      
      let capped: GraphNode[];
      if (focusInGroup.length >= MAX_PER_COL) {
        capped = focusInGroup.slice(0, MAX_PER_COL);
      } else {
        capped = [...focusInGroup, ...othersInGroup.slice(0, MAX_PER_COL - focusInGroup.length)];
      }

      for (let row = 0; row < capped.length; row++) {
        const x = PADDING + col * COL_GAP;
        const y = PADDING + row * ROW_GAP;
        laid.push({ node: capped[row], x, y });
        posMap.set(capped[row].id, { x, y });
        if (row > maxRow) maxRow = row;
      }
      col++;
    }

    // Compute connected node IDs for dim effect
    const connectedNodeIds = new Set<string>();
    if (selectedNodeId) {
      connectedNodeIds.add(selectedNodeId);
      // If selected node is a Process, include all its step nodes so the full path is lit
      const selNode = nodes.find((n) => n.id === selectedNodeId);
      if (selNode?.label === "Process" && selNode.steps) {
        for (const s of selNode.steps) connectedNodeIds.add(s.symbolId);
      }
      // Also include 1-hop edge neighbors
      for (const e of edges) {
        if (e.sourceId === selectedNodeId) connectedNodeIds.add(e.targetId);
        if (e.targetId === selectedNodeId) connectedNodeIds.add(e.sourceId);
      }
    }

    // Build edge lines (only where both endpoints exist in layout)
    const lines = edges
      .map((e) => {
        const s = posMap.get(e.sourceId);
        const t = posMap.get(e.targetId);
        if (!s || !t) return null;
        
        // Neural Graph logic: confidence-based width and type-based coloring
        const color = getEdgeColor(e.type);
        const strokeWidth = e.confidence ? 1 + e.confidence * 2 : 1;

        return {
          id: e.id,
          sourceId: e.sourceId,
          targetId: e.targetId,
          type: e.type,
          x1: s.x + NODE_W / 2,
          y1: s.y + NODE_H / 2,
          x2: t.x + NODE_W / 2,
          y2: t.y + NODE_H / 2,
          color,
          strokeWidth,
          confidence: e.confidence || 0.5,
        };
      })
      .filter(Boolean) as Array<{
        id: string;
        type: string;
        sourceId: string;
        targetId: string;
        x1: number;
        y1: number;
        x2: number;
        y2: number;
        color: string;
        strokeWidth: number;
        confidence: number;
      }>;

    return {
      layout: laid,
      width: PADDING * 2 + col * COL_GAP,
      height: PADDING * 2 + (maxRow + 1) * ROW_GAP,
      edgeLines: lines,
      posMap,
      connectedNodeIds,
    };
  }, [nodes, edges, selectedNodeId]);

  // Auto-center on selected node
  useEffect(() => {
    if (!selectedNodeId || !containerRef.current) return;
    const pos = posMap.get(selectedNodeId);
    if (!pos) return;
    const el = containerRef.current;
    const centerX = pos.x + NODE_W / 2 - el.clientWidth / 2;
    const centerY = pos.y + NODE_H / 2 - el.clientHeight / 2;
    el.scrollTo({ left: centerX, top: centerY, behavior: "smooth" });
  }, [selectedNodeId, posMap]);

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-on-surface-variant/50 text-sm">
        暂无图谱数据。
      </div>
    );
  }

  const svgWidth = Math.max(width, 600);
  const svgHeight = Math.max(height, 300);

  return (
    <div ref={containerRef} className="overflow-auto rounded-lg bg-surface-container-lowest/30 border border-outline-variant/10">
      <svg
        width={svgWidth}
        height={svgHeight}
        className="select-none"
      >
        {/* Background Click Handler */}
        <rect
          width={svgWidth}
          height={svgHeight}
          fill="transparent"
          onClick={() => onNodeClick(null)}
        />

        {/* Edges */}
        <g>
          {edgeLines.map((e) => {
            // Edge is "connected" if either endpoint is in the full connected set (includes process steps)
            const isConnected = (!!selectedNodeId && (connectedNodeIds.has(e.sourceId) || connectedNodeIds.has(e.targetId))) ||
                              e.sourceId === hoveredId || e.targetId === hoveredId;
            const baseOpacity = e.confidence ? 0.1 + e.confidence * 0.2 : 0.15;
            const edgeOpacity = selectedNodeId && !isConnected ? 0.04 : (isConnected ? 0.85 : baseOpacity);

            return (
              <line
                key={e.id}
                x1={e.x1}
                y1={e.y1}
                x2={e.x2}
                y2={e.y2}
                stroke={e.color}
                strokeOpacity={edgeOpacity}
                strokeWidth={isConnected ? e.strokeWidth + 1.5 : e.strokeWidth}
                className="transition-all duration-300"
              />
            );
          })}
        </g>

        {/* Nodes */}
        <g>
          {layout.map(({ node, x, y }) => {
            const isSelected = node.id === selectedNodeId;
            const isHovered = node.id === hoveredId;
            const isConnected = connectedNodeIds.has(node.id);
            const isDimmed = !!selectedNodeId && !isConnected;
            const isCodeNode = CODE_LABELS.has(node.label);
            const color = NODE_COLORS[node.label] || "#6B7280";

            return (
              <g
                key={node.id}
                transform={`translate(${x}, ${y})`}
                onClick={(e) => {
                  e.stopPropagation();
                  onNodeClick(node);
                }}
                onMouseEnter={() => setHoveredId(node.id)}
                onMouseLeave={() => setHoveredId(null)}
                style={{ opacity: isDimmed ? 0.15 : 1, transition: "opacity 300ms" }}
                className={(isCodeNode || node.label === "Process" || node.label === "Community") ? "cursor-pointer" : "cursor-default"}
              >
                {/* Pulse ring on selected node */}
                {isSelected && (
                  <rect width={NODE_W + 14} height={NODE_H + 14} x={-7} y={-7} rx={9} fill="none" stroke={color} strokeWidth={2}>
                    <animate attributeName="opacity" values="0.6;0;0.6" dur="2s" repeatCount="indefinite" />
                    <animate attributeName="stroke-width" values="2;5;2" dur="2s" repeatCount="indefinite" />
                  </rect>
                )}
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

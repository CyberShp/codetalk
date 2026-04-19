"use client";

import { useMemo, useState, useRef, useEffect, useCallback } from "react";
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
  Struct: "#06B6D4",
  Enum: "#D946EF",
  Macro: "#F97316",
  Typedef: "#84CC16",
  Union: "#FB923C",
  Property: "#64748B",
  Tool: "#F97316",
};

/* ── Color map per edge type ── */
const EDGE_COLORS: Record<string, string> = {
  CALLS: "#A4E6FF",
  IMPORTS: "#10B981",
  EXTENDS: "#8B5CF6",
  DEFINES: "#F59E0B",
  MEMBER_OF: "#6366F1",
  CONTAINS: "#64748B",
  STEP_IN_PROCESS: "#EC4899",
};

const getEdgeColor = (type: string) => EDGE_COLORS[type.toUpperCase()] || "#A4E6FF";

const NODE_W = 160;
const NODE_H = 36;
const COL_GAP = 220;
const ROW_GAP = 52;
const PADDING = 40;

/* Labels that represent code-level symbols (clickable for code panel) */
const CODE_LABELS = new Set(["Function", "Method", "Class", "Module", "Route", "Tool", "Struct", "Enum", "Macro"]);

/* Labels available as filter options */
const FILTER_LABELS = ["Function", "Struct", "File", "Process", "Macro", "Enum", "Class", "Community"] as const;

/* Edge types that express a call/dependency direction (used for tree layout) */
const TREE_EDGE_TYPES = new Set(["CALLS", "IMPORTS", "DEFINES", "CONTAINS"]);

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

/* ── Tree constants ── */
const TREE_NODE_W = 140;
const TREE_NODE_H = 32;
const TREE_H_GAP = 12;   // horizontal gap between siblings
const TREE_V_GAP = 40;   // vertical gap between depth levels
const TREE_PAD = 24;
const TREE_MAX_CHILDREN_PER_ROW = 5; // wrap children into rows of N
const TREE_MAX_ROOTS = 15;           // limit root trees to prevent sprawl
const TREE_MAX_DEPTH = 4;            // limit tree depth

/* ── Tree layout algorithm ──
   Compact vertical tree: roots at top, children below.
   Children wrap into rows of TREE_MAX_CHILDREN_PER_ROW to limit width.
   Each tree is stacked vertically (not side-by-side) to avoid horizontal sprawl.
*/
function buildTreeLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
): { layout: LayoutNode[]; width: number; height: number; treeEdgePairs: Set<string> } {
  const nodeSet = new Set(nodes.map((n) => n.id));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // Build adjacency: source → [targets]
  const children = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const e of edges) {
    if (!nodeSet.has(e.sourceId) || !nodeSet.has(e.targetId)) continue;
    if (!TREE_EDGE_TYPES.has(e.type.toUpperCase())) continue;
    if (e.sourceId === e.targetId) continue;
    const list = children.get(e.sourceId) || [];
    list.push(e.targetId);
    children.set(e.sourceId, list);
    hasParent.add(e.targetId);
  }

  // Roots: nodes with no incoming edges
  const roots = nodes.filter((n) => !hasParent.has(n.id));

  // Estimate subtree size for sorting
  function subtreeSize(nodeId: string, visited: Set<string>): number {
    if (visited.has(nodeId)) return 0;
    visited.add(nodeId);
    let size = 1;
    for (const kid of children.get(nodeId) || []) size += subtreeSize(kid, visited);
    return size;
  }
  const rootSizes = roots.map((r) => ({ node: r, size: subtreeSize(r.id, new Set<string>()) }));
  rootSizes.sort((a, b) => b.size - a.size);
  const rootNodes = rootSizes.slice(0, TREE_MAX_ROOTS).map((r) => r.node);

  const laid: LayoutNode[] = [];
  const placed = new Set<string>();
  const treeEdgePairs = new Set<string>();

  // Place subtree at given origin, returns { width, height } consumed
  function placeSubtree(
    nodeId: string,
    originX: number,
    originY: number,
    parentId?: string,
    depth: number = 0,
  ): { w: number; h: number } {
    if (placed.has(nodeId)) return { w: 0, h: 0 };
    placed.add(nodeId);
    if (parentId) treeEdgePairs.add(`${parentId}->${nodeId}`);
    const node = nodeById.get(nodeId);
    if (!node) return { w: 0, h: 0 };

    // Depth limit: treat deep nodes as leaves
    const kids = depth >= TREE_MAX_DEPTH
      ? []
      : (children.get(nodeId) || []).filter((id) => !placed.has(id));

    if (kids.length === 0) {
      // Leaf node
      laid.push({ node, x: originX, y: originY });
      return { w: TREE_NODE_W + TREE_H_GAP, h: TREE_NODE_H };
    }

    // Split children into rows
    const rows: string[][] = [];
    for (let i = 0; i < kids.length; i += TREE_MAX_CHILDREN_PER_ROW) {
      rows.push(kids.slice(i, i + TREE_MAX_CHILDREN_PER_ROW));
    }

    const childY = originY + TREE_NODE_H + TREE_V_GAP;
    let totalH = 0;
    let maxRowW = 0;
    const allChildPositions: { x: number; y: number }[] = [];

    for (const row of rows) {
      let rowX = originX;
      let rowW = 0;
      let rowH = 0;
      for (const kid of row) {
        const sub = placeSubtree(kid, rowX, childY + totalH, nodeId, depth + 1);
        if (sub.w > 0) {
          allChildPositions.push({ x: rowX, y: childY + totalH });
          rowX += sub.w;
          rowW += sub.w;
          rowH = Math.max(rowH, sub.h);
        }
      }
      maxRowW = Math.max(maxRowW, rowW);
      totalH += rowH + TREE_V_GAP;
    }

    // Center parent above all children
    const parentW = Math.max(maxRowW, TREE_NODE_W + TREE_H_GAP);
    const centerX = allChildPositions.length > 0
      ? allChildPositions.reduce((sum, p) => sum + p.x, 0) / allChildPositions.length
      : originX;
    const parentX = Math.max(originX, Math.min(centerX, originX + parentW - TREE_NODE_W));
    laid.push({ node, x: parentX, y: originY });

    return { w: parentW, h: TREE_NODE_H + TREE_V_GAP + totalH };
  }

  // Stack trees vertically instead of side-by-side
  let cursorY = TREE_PAD;
  let globalMaxW = 0;

  for (const root of rootNodes) {
    const { w, h } = placeSubtree(root.id, TREE_PAD, cursorY, undefined);
    if (h > 0) {
      globalMaxW = Math.max(globalMaxW, w);
      cursorY += h + TREE_V_GAP * 1.5;
    }
  }

  // Place remaining unplaced nodes in a compact grid at the bottom
  const unplaced = nodes.filter((n) => !placed.has(n.id));
  if (unplaced.length > 0) {
    cursorY += TREE_V_GAP;
    let ux = TREE_PAD;
    const gridCols = Math.max(1, Math.floor((globalMaxW || 800) / (TREE_NODE_W + TREE_H_GAP)));
    for (let i = 0; i < Math.min(unplaced.length, 80); i++) {
      laid.push({ node: unplaced[i], x: ux, y: cursorY });
      ux += TREE_NODE_W + TREE_H_GAP;
      if ((i + 1) % gridCols === 0) {
        ux = TREE_PAD;
        cursorY += TREE_NODE_H + TREE_H_GAP;
      }
    }
    cursorY += TREE_NODE_H;
  }

  const maxX = laid.reduce((max, l) => Math.max(max, l.x + TREE_NODE_W), 600);
  const maxY = Math.max(cursorY + TREE_PAD, 300);
  return { layout: laid, width: maxX + TREE_PAD, height: maxY, treeEdgePairs };
}

/* ── Column layout (original) ── */
function buildColumnLayout(
  nodes: GraphNode[],
  selectedNodeId: string | null,
): { layout: LayoutNode[]; width: number; height: number } {
  const groups = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const arr = groups.get(n.label) || [];
    arr.push(n);
    groups.set(n.label, arr);
  }

  const MAX_PER_COL = 30;
  const focusNodeIds = new Set<string>();
  if (selectedNodeId) {
    focusNodeIds.add(selectedNodeId);
    const selNode = nodes.find((n) => n.id === selectedNodeId);
    if (selNode?.label === "Process" && selNode.steps) {
      selNode.steps.forEach((s) => focusNodeIds.add(s.symbolId));
    }
  }

  const laid: LayoutNode[] = [];
  let col = 0;
  let maxRow = 0;

  for (const [, group] of groups) {
    const focusInGroup = group.filter((n) => focusNodeIds.has(n.id));
    const othersInGroup = group.filter((n) => !focusNodeIds.has(n.id));
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
      if (row > maxRow) maxRow = row;
    }
    col++;
  }

  return {
    layout: laid,
    width: PADDING * 2 + Math.max(col, 1) * COL_GAP,
    height: PADDING * 2 + (maxRow + 1) * ROW_GAP,
  };
}

export default function GraphViewer({ nodes, edges, selectedNodeId, onNodeClick }: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-clear filter when a NEW node is selected that wouldn't be visible
  // (React-endorsed render-time state adjustment — not in useEffect)
  const [prevSelectedId, setPrevSelectedId] = useState<string | null>(null);
  if (selectedNodeId !== prevSelectedId) {
    setPrevSelectedId(selectedNodeId);
    if (selectedNodeId && activeFilter) {
      const sel = nodes.find((n) => n.id === selectedNodeId);
      if (sel && (sel.label === "Folder" || sel.label !== activeFilter)) {
        setActiveFilter(null);
      }
    }
  }

  // Compute available labels from the data
  const availableLabels = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of nodes) {
      counts.set(n.label, (counts.get(n.label) || 0) + 1);
    }
    return FILTER_LABELS.filter((l) => (counts.get(l) || 0) > 0).map((l) => ({
      label: l,
      count: counts.get(l) || 0,
    }));
  }, [nodes]);

  // Filter nodes and compute layout
  const { layout, width: svgWidth, height: svgHeight, edgeLines, posMap, connectedNodeIds } = useMemo(() => {
    // Filter out Folder nodes always, plus apply type filter
    const filtered = nodes.filter((n) => {
      if (n.label === "Folder") return false;
      if (activeFilter && n.label !== activeFilter) return false;
      return true;
    });

    // Choose layout strategy
    const useTree = activeFilter !== null && TREE_EDGE_TYPES.size > 0;
    let treeEdgePairs: Set<string> | null = null;
    let layout: LayoutNode[];
    let width: number;
    let height: number;
    if (useTree) {
      const tree = buildTreeLayout(filtered, edges);
      layout = tree.layout;
      width = tree.width;
      height = tree.height;
      treeEdgePairs = tree.treeEdgePairs;
    } else {
      const col = buildColumnLayout(filtered, selectedNodeId);
      layout = col.layout;
      width = col.width;
      height = col.height;
    }

    const posMap = new Map<string, { x: number; y: number }>();
    for (const l of layout) posMap.set(l.node.id, { x: l.x, y: l.y });

    // Connected node IDs for dim effect
    const connectedNodeIds = new Set<string>();
    if (selectedNodeId) {
      connectedNodeIds.add(selectedNodeId);
      const selNode = nodes.find((n) => n.id === selectedNodeId);
      if (selNode?.label === "Process" && selNode.steps) {
        for (const s of selNode.steps) connectedNodeIds.add(s.symbolId);
      }
      for (const e of edges) {
        if (e.sourceId === selectedNodeId) connectedNodeIds.add(e.targetId);
        if (e.targetId === selectedNodeId) connectedNodeIds.add(e.sourceId);
      }
    }

    // Edge lines — in tree mode, only show edges that form the tree structure
    const edgeLines = edges
      .map((e) => {
        const s = posMap.get(e.sourceId);
        const t = posMap.get(e.targetId);
        if (!s || !t) return null;
        // In tree mode, skip edges that aren't part of the placed tree
        if (treeEdgePairs && !treeEdgePairs.has(`${e.sourceId}->${e.targetId}`)) return null;
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
      layout,
      width: Math.max(width, 600),
      height: Math.max(height, 300),
      edgeLines,
      posMap,
      connectedNodeIds,
    };
  }, [nodes, edges, selectedNodeId, activeFilter]);

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

  // Auto-scroll to the first root node when filter (tree mode) changes
  // Skip if a node is selected and visible in the layout (auto-center handles it)
  useEffect(() => {
    if (!activeFilter || !containerRef.current || layout.length === 0) return;
    if (selectedNodeId && posMap.has(selectedNodeId)) return;
    const topNode = layout.reduce((min, l) => (l.y < min.y ? l : min), layout[0]);
    const el = containerRef.current;
    const centerX = topNode.x + NODE_W / 2 - el.clientWidth / 2;
    el.scrollTo({ left: Math.max(0, centerX), top: 0, behavior: "smooth" });
  }, [activeFilter, layout, selectedNodeId, posMap]);

  const handleFilterClick = useCallback((label: string) => {
    setActiveFilter((prev) => (prev === label ? null : label));
  }, []);

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-on-surface-variant/50 text-sm">
        暂无图谱数据。
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* ── Filter bar ── */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-outline-variant/10 bg-surface-container-low/50 flex-shrink-0 overflow-x-auto">
        <button
          onClick={() => setActiveFilter(null)}
          className={`px-3 py-1 text-xs rounded-full transition-colors whitespace-nowrap ${
            activeFilter === null
              ? "bg-primary-container text-primary"
              : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
          }`}
        >
          全部
        </button>
        {availableLabels.map(({ label, count }) => {
          const color = NODE_COLORS[label] || "#6B7280";
          const isActive = activeFilter === label;
          return (
            <button
              key={label}
              onClick={() => handleFilterClick(label)}
              className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded-full transition-colors whitespace-nowrap ${
                isActive
                  ? "text-white"
                  : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
              }`}
              style={isActive ? { backgroundColor: color } : undefined}
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: color }}
              />
              {label}
              <span className="opacity-50">{count}</span>
            </button>
          );
        })}
      </div>

      {/* ── Graph SVG ── */}
      <div ref={containerRef} className="overflow-auto flex-1 rounded-b-lg bg-surface-container-lowest/30">
        <svg width={svgWidth} height={svgHeight} className="select-none">
          <rect
            width={svgWidth}
            height={svgHeight}
            fill="transparent"
            onClick={() => onNodeClick(null)}
          />

          {/* Edge arrows (tree mode only) */}
          {activeFilter && (
            <defs>
              <marker id="arrow" viewBox="0 0 10 6" refX="10" refY="3" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 3 L 0 6 z" fill="#A4E6FF" opacity="0.4" />
              </marker>
            </defs>
          )}

          {/* Edges */}
          <g>
            {edgeLines.map((e) => {
              const isConnected =
                (!!selectedNodeId &&
                  (connectedNodeIds.has(e.sourceId) || connectedNodeIds.has(e.targetId))) ||
                e.sourceId === hoveredId ||
                e.targetId === hoveredId;

              if (activeFilter) {
                // Tree mode: curved paths, very subtle by default
                const treeOpacity = isConnected ? 0.6 : 0.08;
                const treeWidth = isConnected ? 1.5 : 0.8;
                const midY = (e.y1 + e.y2) / 2;
                const d = `M ${e.x1} ${e.y1} C ${e.x1} ${midY}, ${e.x2} ${midY}, ${e.x2} ${e.y2}`;
                return (
                  <path
                    key={e.id}
                    d={d}
                    fill="none"
                    stroke={e.color}
                    strokeOpacity={treeOpacity}
                    strokeWidth={treeWidth}
                    markerEnd="url(#arrow)"
                    className="transition-all duration-300"
                  />
                );
              }

              // Column mode: straight lines
              const baseOpacity = e.confidence ? 0.1 + e.confidence * 0.2 : 0.15;
              const edgeOpacity = selectedNodeId && !isConnected ? 0.04 : isConnected ? 0.85 : baseOpacity;

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
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onNodeClick(node);
                  }}
                  onMouseEnter={() => setHoveredId(node.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  style={{ opacity: isDimmed ? 0.15 : 1, transition: "opacity 300ms" }}
                  className={isCodeNode || node.label === "Process" || node.label === "Community" ? "cursor-pointer" : "cursor-default"}
                >
                  {isSelected && (
                    <rect
                      width={NODE_W + 14}
                      height={NODE_H + 14}
                      x={-7}
                      y={-7}
                      rx={9}
                      fill="none"
                      stroke={color}
                      strokeWidth={2}
                    >
                      <animate attributeName="opacity" values="0.6;0;0.6" dur="2s" repeatCount="indefinite" />
                      <animate attributeName="stroke-width" values="2;5;2" dur="2s" repeatCount="indefinite" />
                    </rect>
                  )}
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={6}
                    fill={isSelected ? color : "#1A1E24"}
                    stroke={isSelected ? color : isHovered ? color : "#2A2E34"}
                    strokeWidth={isSelected ? 2 : 1}
                    opacity={isSelected || isHovered ? 1 : 0.85}
                  />
                  <rect width={4} height={NODE_H - 8} x={4} y={4} rx={2} fill={color} opacity={0.8} />
                  <text x={14} y={14} fill="#A4E6FF" fontSize={9} fontFamily="JetBrains Mono, monospace" opacity={0.6}>
                    {node.label}
                  </text>
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
    </div>
  );
}

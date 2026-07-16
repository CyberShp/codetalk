"use client";

import {
  Focus,
  Minus,
  Plus,
  Redo2,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { WorkflowEditorAction, WorkflowEditorState } from "../state/workflow-editor-reducer";
import {
  connectionEdgeKind,
  createNode,
  connectionLabel,
  edge as createEdge,
  inputPortDefinitions,
  nodeKindLabel,
  outputPortDefinitions,
  validateConnection,
} from "../workflow-graph";
import type {
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowNodeKind,
} from "@/lib/types/workflow";

const NODE_WIDTH = 260;
const NODE_PORT_Y = 84;
const NODE_PORT_GAP = 30;
const BOARD_WIDTH = 2400;
const BOARD_HEIGHT = 1400;

const paletteKinds: WorkflowNodeKind[] = [
  "input",
  "agent",
  "semantic_retrieve",
  "memory_retrieve",
  "local_scope_discover",
  "evidence_validate",
  "report_render",
  "artifact_export",
  "output",
];

interface Props {
  state: WorkflowEditorState;
  dispatch: Dispatch<WorkflowEditorAction>;
  onSelectionChange?: (nodeId: string | null) => void;
}

interface ConnectionDraft {
  sourceNodeId: string;
  sourcePortId: string;
  x: number;
  y: number;
}

export function WorkflowCanvas({ state, dispatch, onSelectionChange }: Props) {
  const boardRef = useRef<HTMLDivElement>(null);
  const edgeSequence = useRef(0);
  const [view, setView] = useState({ x: 36, y: 42, zoom: 1 });
  const [connection, setConnection] = useState<ConnectionDraft | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const nodesById = useMemo(
    () => new Map(state.present.nodes.map((node) => [node.id, node])),
    [state.present.nodes],
  );

  const selectNode = useCallback(
    (nodeId: string | null) => {
      dispatch({ type: "select-node", nodeId });
      onSelectionChange?.(nodeId);
    },
    [dispatch, onSelectionChange],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable=true]")) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        dispatch({ type: event.shiftKey ? "redo" : "undo" });
        return;
      }
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      if (state.selectedNodeId) {
        event.preventDefault();
        dispatch({ type: "remove-node", nodeId: state.selectedNodeId });
        selectNode(null);
      } else if (state.selectedEdgeId) {
        event.preventDefault();
        dispatch({ type: "remove-edge", edgeId: state.selectedEdgeId });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [dispatch, selectNode, state.selectedEdgeId, state.selectedNodeId]);

  useEffect(() => {
    if (!connection) return;
    const move = (event: PointerEvent) => {
      const point = clientToCanvas(event.clientX, event.clientY, boardRef.current, view);
      setConnection((current) => (current ? { ...current, x: point.x, y: point.y } : null));
    };
    const cancel = () => setConnection(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", cancel);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", cancel);
    };
  }, [connection, view]);

  const addNode = (kind: WorkflowNodeKind, x?: number, y?: number) => {
    const node = createNode(
      kind,
      Math.max(20, x ?? 180 + state.present.nodes.length * 28),
      Math.max(20, y ?? 120 + state.present.nodes.length * 34),
    );
    dispatch({ type: "add-node", node });
    selectNode(node.id);
  };

  const fitCanvas = () => {
    if (!state.present.nodes.length || !boardRef.current) {
      setView({ x: 36, y: 42, zoom: 1 });
      return;
    }
    const rect = boardRef.current.getBoundingClientRect();
    const minX = Math.min(...state.present.nodes.map((node) => node.position.x));
    const maxX = Math.max(...state.present.nodes.map((node) => node.position.x + NODE_WIDTH));
    const minY = Math.min(...state.present.nodes.map((node) => node.position.y));
    const maxY = Math.max(...state.present.nodes.map((node) => node.position.y + nodeHeight(node)));
    const zoom = Math.max(0.55, Math.min(1.2, Math.min((rect.width - 96) / (maxX - minX), (rect.height - 96) / (maxY - minY))));
    setView({
      zoom,
      x: (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom,
    });
  };

  const startPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const target = event.target instanceof Element ? event.target : null;
    if (
      event.button !== 0 ||
      !event.isPrimary ||
      target?.closest(".ct-v2-workflow-node, .ct-v2-edge-hit")
    ) return;
    event.preventDefault();
    selectNode(null);
    dispatch({ type: "select-edge", edgeId: null });
    const start = { x: event.clientX, y: event.clientY, viewX: view.x, viewY: view.y };
    capturePointerMovement(event.currentTarget, event.pointerId, (next) => {
      setView((current) => ({
        ...current,
        x: start.viewX + next.clientX - start.x,
        y: start.viewY + next.clientY - start.y,
      }));
    });
  };

  const finishConnection = (targetNodeId: string, targetPortId: string) => {
    if (!connection || connection.sourceNodeId === targetNodeId) return;
    const validation = validateConnection(
      state.present,
      connection.sourceNodeId,
      connection.sourcePortId,
      targetNodeId,
      targetPortId,
    );
    if (!validation.ok) {
      setConnectionError(validation.message);
      setConnection(null);
      return;
    }
    setConnectionError("");
    edgeSequence.current += 1;
    const id = `edge-${connection.sourceNodeId}-${targetNodeId}-${edgeSequence.current}`;
    const sourceNode = nodesById.get(connection.sourceNodeId);
    const targetNode = nodesById.get(targetNodeId);
    if (!sourceNode || !targetNode) return;
    dispatch({
      type: "add-edge",
      edge: createEdge(
        id,
        connectionEdgeKind(sourceNode, connection.sourcePortId, targetNode, targetPortId),
        connection.sourceNodeId,
        connection.sourcePortId,
        targetNodeId,
        targetPortId,
      ),
    });
    setConnection(null);
  };

  return (
    <div className="ct-v2-canvas-shell">
      <aside className="ct-v2-node-palette" aria-label="节点库">
        <div className="ct-v2-pane-heading">
          <strong>节点库</strong>
          <span>拖到画布，或双击添加</span>
        </div>
        <div className="ct-v2-palette-list">
          {paletteKinds.map((kind) => (
            <button
              key={kind}
              type="button"
              draggable
              onDragStart={(event) => event.dataTransfer.setData("application/x-codetalk-node", kind)}
              onDoubleClick={() => addNode(kind)}
              className="ct-v2-palette-item"
              title={`添加${nodeKindLabel(kind)}节点`}
            >
              <span className="ct-v2-kind-mark" aria-hidden="true" />
              <span>
                <strong>{nodeKindLabel(kind)}</strong>
                <small>{nodeKindDescription(kind)}</small>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="ct-v2-canvas-stage" aria-label="工作流画布">
        {connectionError && (
          <div className="ct-v2-connection-error" role="alert">
            {connectionError}
          </div>
        )}
        <div className="ct-v2-canvas-toolbar" aria-label="画布工具栏">
          <button type="button" onClick={() => dispatch({ type: "undo" })} disabled={!state.past.length} title="撤销">
            <Undo2 size={15} />
          </button>
          <button type="button" onClick={() => dispatch({ type: "redo" })} disabled={!state.future.length} title="重做">
            <Redo2 size={15} />
          </button>
          <span />
          <button type="button" onClick={() => setView((current) => ({ ...current, zoom: Math.max(0.5, current.zoom - 0.1) }))} title="缩小">
            <Minus size={15} />
          </button>
          <output>{Math.round(view.zoom * 100)}%</output>
          <button type="button" onClick={() => setView((current) => ({ ...current, zoom: Math.min(1.5, current.zoom + 0.1) }))} title="放大">
            <Plus size={15} />
          </button>
          <button type="button" onClick={fitCanvas} title="适应画布">
            <Focus size={15} />
          </button>
          {(state.selectedNodeId || state.selectedEdgeId) && (
            <button
              type="button"
              className="is-danger"
              onClick={() => {
                if (state.selectedNodeId) dispatch({ type: "remove-node", nodeId: state.selectedNodeId });
                if (state.selectedEdgeId) dispatch({ type: "remove-edge", edgeId: state.selectedEdgeId });
                selectNode(null);
              }}
              title="删除所选"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
        <div
          ref={boardRef}
          className="ct-v2-canvas-board"
          tabIndex={0}
          onPointerDown={startPan}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const kind = event.dataTransfer.getData("application/x-codetalk-node") as WorkflowNodeKind;
            if (!paletteKinds.includes(kind)) return;
            const point = clientToCanvas(event.clientX, event.clientY, boardRef.current, view);
            addNode(kind, point.x, point.y);
          }}
        >
          <div
            className="ct-v2-canvas-world"
            style={{
              width: BOARD_WIDTH,
              height: BOARD_HEIGHT,
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
            }}
          >
            <svg className="ct-v2-edge-layer" width={BOARD_WIDTH} height={BOARD_HEIGHT} aria-hidden="true">
              {state.present.edges.map((item) => (
                <WorkflowEdgePath
                  key={item.id}
                  edge={item}
                  nodesById={nodesById}
                  selected={item.id === state.selectedEdgeId}
                  onSelect={() => dispatch({ type: "select-edge", edgeId: item.id })}
                />
              ))}
              {connection && (
                <path
                  className="ct-v2-edge is-draft"
                  d={edgePath(
                    portPoint(nodesById.get(connection.sourceNodeId), connection.sourcePortId, "out"),
                    { x: connection.x, y: connection.y },
                  )}
                />
              )}
            </svg>
            {state.present.nodes.map((node) => (
              <WorkflowNodeCard
                key={node.id}
                node={node}
                selected={node.id === state.selectedNodeId}
                dispatch={dispatch}
                selectNode={selectNode}
                onStartConnection={(portId, point) =>
                  setConnection({ sourceNodeId: node.id, sourcePortId: portId, ...point })
                }
                onFinishConnection={(portId) => finishConnection(node.id, portId)}
              />
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function WorkflowNodeCard({
  node,
  selected,
  dispatch,
  selectNode,
  onStartConnection,
  onFinishConnection,
}: {
  node: WorkflowGraphNode;
  selected: boolean;
  dispatch: Dispatch<WorkflowEditorAction>;
  selectNode: (id: string | null) => void;
  onStartConnection: (portId: string, point: { x: number; y: number }) => void;
  onFinishConnection: (portId: string) => void;
}) {
  const outputs = outputPortDefinitions(node);
  const inputs = inputPortDefinitions(node);
  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    event.stopPropagation();
    selectNode(node.id);
    const start = { clientX: event.clientX, clientY: event.clientY, x: node.position.x, y: node.position.y };
    capturePointerMovement(event.currentTarget, event.pointerId, (next) => {
      dispatch({
        type: "move-node",
        nodeId: node.id,
        x: Math.max(0, start.x + next.clientX - start.clientX),
        y: Math.max(0, start.y + next.clientY - start.clientY),
      });
    });
  };
  return (
    <article
      className={`ct-v2-workflow-node ${selected ? "is-selected" : ""}`}
      style={{
        left: node.position.x,
        top: node.position.y,
        width: NODE_WIDTH,
        minHeight: nodeHeight(node),
      }}
      tabIndex={0}
      aria-label={`${node.label} ${nodeKindLabel(node.kind)}节点`}
      onFocus={() => selectNode(node.id)}
      onClick={(event) => {
        event.stopPropagation();
        selectNode(node.id);
      }}
    >
      <div className="ct-v2-node-drag" onPointerDown={startDrag}>
        <span className="ct-v2-kind-mark" aria-hidden="true" />
        <div>
          <strong>{node.label}</strong>
          <small>{nodeKindLabel(node.kind)}</small>
        </div>
      </div>
      <div className="ct-v2-node-meta">
        <span>{String(node.config.provider || node.config.type || node.kind)}</span>
        {node.config.required && <em>必需</em>}
      </div>
      {inputs.map((port, index) => (
        <div
          key={`in-${port.id}`}
          className="ct-v2-port-row is-input"
          style={{ top: NODE_PORT_Y + index * NODE_PORT_GAP }}
        >
          <button
            type="button"
            className="ct-v2-port is-input"
            aria-label={`${node.label} 输入端口 ${port.id} 类型 ${port.type}`}
            title={`输入：${port.id} · ${port.type}`}
            onPointerUp={(event) => {
              event.stopPropagation();
              onFinishConnection(port.id);
            }}
          />
          <span>{port.id}<small>{port.type}</small></span>
        </div>
      ))}
      {outputs.map((port, index) => (
        <div
          key={`out-${port.id}`}
          className="ct-v2-port-row is-output"
          style={{ top: NODE_PORT_Y + index * NODE_PORT_GAP }}
        >
          <span>{port.id}<small>{port.type}</small></span>
          <button
            type="button"
            className="ct-v2-port is-output"
            aria-label={`${node.label} 输出端口 ${port.id} 类型 ${port.type}`}
            title={`输出：${port.id} · ${port.type}`}
            onPointerDown={(event) => {
              event.stopPropagation();
              const point = portPoint(node, port.id, "out");
              onStartConnection(port.id, point);
            }}
          />
        </div>
      ))}
    </article>
  );
}

function WorkflowEdgePath({
  edge,
  nodesById,
  selected,
  onSelect,
}: {
  edge: WorkflowGraphEdge;
  nodesById: Map<string, WorkflowGraphNode>;
  selected: boolean;
  onSelect: () => void;
}) {
  const source = portPoint(nodesById.get(edge.source.node_id), edge.source.port_id, "out");
  const target = portPoint(nodesById.get(edge.target.node_id), edge.target.port_id, "in");
  const path = edgePath(source, target);
  const label = connectionLabel(edge, nodesById);
  const labelPoint = { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 - 7 };
  return (
    <g className={selected ? "is-selected" : ""}>
      <path className="ct-v2-edge-hit" d={path} onPointerDown={(event) => { event.stopPropagation(); onSelect(); }} />
      <path className={`ct-v2-edge ${edge.kind === "dependency" ? "is-dependency" : ""}`} d={path} />
      {edge.kind === "data" && label && (
        <text className="ct-v2-edge-label" x={labelPoint.x} y={labelPoint.y} textAnchor="middle">
          {label}
        </text>
      )}
    </g>
  );
}

function portPoint(
  node: WorkflowGraphNode | undefined,
  portId: string,
  side: "in" | "out",
) {
  if (!node) return { x: 0, y: 0 };
  const ports = side === "in" ? inputPortDefinitions(node) : outputPortDefinitions(node);
  const index = Math.max(0, ports.findIndex((port) => port.id === portId));
  return {
    x: node.position.x + (side === "out" ? NODE_WIDTH : 0),
    y: node.position.y + NODE_PORT_Y + index * NODE_PORT_GAP + 9,
  };
}

function nodeHeight(node: WorkflowGraphNode): number {
  return NODE_PORT_Y + Math.max(
    inputPortDefinitions(node).length,
    outputPortDefinitions(node).length,
    1,
  ) * NODE_PORT_GAP + 12;
}

function nodeKindDescription(kind: WorkflowNodeKind): string {
  return {
    input: "文件、目录、链接或文字",
    output: "报告与交付文件",
    agent: "模型分析与文件生成",
    semantic_retrieve: "检索历史测试知识",
    memory_retrieve: "检索已保存证据",
    local_scope_discover: "定位源码与测试范围",
    evidence_validate: "校验文件、符号与行号",
    report_render: "整理结构化报告",
    artifact_export: "导出工作流产物",
  }[kind];
}

function edgePath(source: { x: number; y: number }, target: { x: number; y: number }) {
  const bend = Math.max(70, Math.abs(target.x - source.x) * 0.42);
  return `M ${source.x} ${source.y} C ${source.x + bend} ${source.y}, ${target.x - bend} ${target.y}, ${target.x} ${target.y}`;
}

function clientToCanvas(
  clientX: number,
  clientY: number,
  board: HTMLDivElement | null,
  view: { x: number; y: number; zoom: number },
) {
  const rect = board?.getBoundingClientRect() ?? { left: 0, top: 0 };
  return {
    x: (clientX - rect.left - view.x) / view.zoom,
    y: (clientY - rect.top - view.y) / view.zoom,
  };
}

function capturePointerMovement(
  element: HTMLElement,
  pointerId: number,
  onMove: (event: PointerEvent) => void,
) {
  const move = (event: PointerEvent) => {
    if (event.pointerId === pointerId) onMove(event);
  };
  const cleanup = () => {
    element.removeEventListener("pointermove", move);
    element.removeEventListener("pointerup", stop);
    element.removeEventListener("pointercancel", stop);
    element.removeEventListener("lostpointercapture", stop);
  };
  const stop = (event: PointerEvent) => {
    if (event.pointerId !== pointerId) return;
    cleanup();
    if (element.hasPointerCapture(pointerId)) element.releasePointerCapture(pointerId);
  };

  element.setPointerCapture(pointerId);
  element.addEventListener("pointermove", move);
  element.addEventListener("pointerup", stop);
  element.addEventListener("pointercancel", stop);
  element.addEventListener("lostpointercapture", stop);
}

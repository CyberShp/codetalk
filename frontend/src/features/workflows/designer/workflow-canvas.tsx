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
  createNode,
  edge as createEdge,
  inputPortIds,
  nodeKindLabel,
  outputPortIds,
} from "../workflow-graph";
import type {
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowNodeKind,
} from "@/lib/types/workflow";

const NODE_WIDTH = 188;
const NODE_HEADER_Y = 44;
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
    const maxY = Math.max(...state.present.nodes.map((node) => node.position.y + 104));
    const zoom = Math.max(0.55, Math.min(1.2, Math.min((rect.width - 96) / (maxX - minX), (rect.height - 96) / (maxY - minY))));
    setView({
      zoom,
      x: (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom,
    });
  };

  const startPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    selectNode(null);
    dispatch({ type: "select-edge", edgeId: null });
    const start = { x: event.clientX, y: event.clientY, viewX: view.x, viewY: view.y };
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (next: ReactPointerEvent<HTMLDivElement>) => {
      setView((current) => ({
        ...current,
        x: start.viewX + next.clientX - start.x,
        y: start.viewY + next.clientY - start.y,
      }));
    };
    const stop = (next: ReactPointerEvent<HTMLDivElement>) => {
      next.currentTarget.releasePointerCapture(next.pointerId);
      next.currentTarget.removeEventListener("pointermove", move as never);
    };
    event.currentTarget.addEventListener("pointermove", move as never);
    event.currentTarget.addEventListener("pointerup", stop as never, { once: true });
  };

  const finishConnection = (targetNodeId: string, targetPortId: string) => {
    if (!connection || connection.sourceNodeId === targetNodeId) return;
    const duplicate = state.present.edges.some(
      (item) =>
        item.source.node_id === connection.sourceNodeId &&
        item.source.port_id === connection.sourcePortId &&
        item.target.node_id === targetNodeId &&
        item.target.port_id === targetPortId,
    );
    if (!duplicate) {
      edgeSequence.current += 1;
      const id = `edge-${connection.sourceNodeId}-${targetNodeId}-${edgeSequence.current}`;
      dispatch({
        type: "add-edge",
        edge: createEdge(
          id,
          connection.sourcePortId === "done" || targetPortId === "start"
            ? "dependency"
            : "data",
          connection.sourceNodeId,
          connection.sourcePortId,
          targetNodeId,
          targetPortId,
        ),
      });
    }
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
                <small>{kind === "agent" ? "模型分析与文件生成" : kind}</small>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="ct-v2-canvas-stage" aria-label="工作流画布">
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
  const inputs = inputPortIds(node);
  const outputs = outputPortIds(node);
  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    selectNode(node.id);
    const start = { clientX: event.clientX, clientY: event.clientY, x: node.position.x, y: node.position.y };
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (next: ReactPointerEvent<HTMLDivElement>) => {
      dispatch({
        type: "move-node",
        nodeId: node.id,
        x: Math.max(0, start.x + next.clientX - start.clientX),
        y: Math.max(0, start.y + next.clientY - start.clientY),
      });
    };
    const stop = (next: ReactPointerEvent<HTMLDivElement>) => {
      next.currentTarget.releasePointerCapture(next.pointerId);
      next.currentTarget.removeEventListener("pointermove", move as never);
    };
    event.currentTarget.addEventListener("pointermove", move as never);
    event.currentTarget.addEventListener("pointerup", stop as never, { once: true });
  };
  return (
    <article
      className={`ct-v2-workflow-node ${selected ? "is-selected" : ""}`}
      style={{ left: node.position.x, top: node.position.y, width: NODE_WIDTH }}
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
      {inputs.map((portId, index) => (
        <button
          key={`in-${portId}`}
          type="button"
          className="ct-v2-port is-input"
          style={{ top: NODE_HEADER_Y + index * 22 }}
          aria-label={`${node.label} 输入端口 ${portId}`}
          title={`输入：${portId}`}
          onPointerUp={(event) => {
            event.stopPropagation();
            onFinishConnection(portId);
          }}
        />
      ))}
      {outputs.map((portId, index) => (
        <button
          key={`out-${portId}`}
          type="button"
          className="ct-v2-port is-output"
          style={{ top: NODE_HEADER_Y + index * 22 }}
          aria-label={`${node.label} 输出端口 ${portId}`}
          title={`输出：${portId}`}
          onPointerDown={(event) => {
            event.stopPropagation();
            const point = portPoint(node, portId, "out");
            onStartConnection(portId, point);
          }}
        />
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
  return (
    <g className={selected ? "is-selected" : ""}>
      <path className="ct-v2-edge-hit" d={path} onPointerDown={(event) => { event.stopPropagation(); onSelect(); }} />
      <path className={`ct-v2-edge ${edge.kind === "dependency" ? "is-dependency" : ""}`} d={path} />
    </g>
  );
}

function portPoint(
  node: WorkflowGraphNode | undefined,
  portId: string,
  side: "in" | "out",
) {
  if (!node) return { x: 0, y: 0 };
  const ports = side === "in" ? inputPortIds(node) : outputPortIds(node);
  const index = Math.max(0, ports.indexOf(portId));
  return {
    x: node.position.x + (side === "out" ? NODE_WIDTH : 0),
    y: node.position.y + NODE_HEADER_Y + index * 22 + 7,
  };
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

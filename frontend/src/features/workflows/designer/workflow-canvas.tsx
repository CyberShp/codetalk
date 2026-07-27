"use client";

import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useEdgesState,
  useNodesState,
  useReactFlow,
  getBezierPath,
  type Connection,
  type Edge,
  type EdgeProps,
  type NodeProps,
  type OnConnect,
  type OnConnectEnd,
} from "@xyflow/react";
import { Focus, Redo2, Trash2, Undo2 } from "lucide-react";
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
} from "react";

import type {
  AuthoringGraph,
  WorkflowGraphNode,
  WorkflowNodeRegistry,
  WorkflowNodeRegistryEntry,
} from "@/lib/types/workflow";
import type { WorkflowEditorAction, WorkflowEditorState } from "../state/workflow-editor-reducer";
import {
  connectionEdgeKind,
  createNodeFromRegistry,
  inputPortDefinitions,
  nodeKindLabel,
  outputPortDefinitions,
  portDisplayLabel,
  portDisplayType,
  validateConnection,
} from "../workflow-graph";
import {
  authoringGraphToFlow,
  type WorkflowFlowEdge,
  type WorkflowFlowNode,
} from "../workflow-xyflow";
import {
  releaseCanvasNodePosition,
  reserveCanvasNodePosition,
  type CanvasPositionReservation,
} from "./node-position-reservation";

interface Props {
  state: WorkflowEditorState;
  dispatch: Dispatch<WorkflowEditorAction>;
  registry: WorkflowNodeRegistry;
  onSelectionChange?: (nodeId: string | null) => void;
  onCreateNode?: (kind: string, position: { x: number; y: number }) => Promise<AuthoringGraph>;
  onCreateEdge?: (payload: { source: { node_id: string; port_id: string }; target: { node_id: string; port_id: string } }) => Promise<AuthoringGraph>;
}

export function WorkflowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvasSurface {...props} />
    </ReactFlowProvider>
  );
}

function WorkflowCanvasSurface({ state, dispatch, registry, onSelectionChange, onCreateNode, onCreateEdge }: Props) {
  const nodeTypes = useMemo(() => ({ workflowNode: WorkflowNodeCard }), []);
  const edgeTypes = useMemo(() => ({ workflowEdge: WorkflowEdge }), []);
  const edgeSequence = useRef(0);
  const committedNodesRef = useRef<WorkflowGraphNode[]>(state.present.nodes);
  const positionReservationsRef = useRef<CanvasPositionReservation[]>([]);
  const positionReservationSequenceRef = useRef(0);
  const fitAfterServerInsertRef = useRef<string | null>(null);
  const compactInitialFitRef = useRef(false);
  // Server-issued identities make mutation order observable. Keep browser
  // gestures in one queue so an older draft response can never overwrite a
  // newer node or edge that the user just created.
  const serverMutationQueueRef = useRef<Promise<void>>(Promise.resolve());
  const { screenToFlowPosition, fitView } = useReactFlow<WorkflowFlowNode, WorkflowFlowEdge>();
  const [connectionError, setConnectionError] = useState("");
  const [compactViewport, setCompactViewport] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");

  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setCompactViewport(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const enqueueServerMutation = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const result = serverMutationQueueRef.current.then(operation, operation);
    serverMutationQueueRef.current = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }, []);

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const sourcePortId = handlePortId(connection.sourceHandle, "out:");
    const targetPortId = handlePortId(connection.targetHandle, "in:");
    if (!connection.source || !connection.target || !sourcePortId || !targetPortId) return false;
    const validation = validateConnection(
      state.present,
      connection.source,
      sourcePortId,
      connection.target,
      targetPortId,
    );
    setConnectionError(validation.ok ? "" : validation.message);
    return validation.ok;
  }, [state.present]);

  // Keep xyflow's measured node internals stable while selection lives in the
  // editor state. Replacing controlled nodes for every selection loses those
  // measurements and breaks the next drag gesture.
  const flow = useMemo(() => {
    const adapted = authoringGraphToFlow(state.present);
    return {
      ...adapted,
      nodes: adapted.nodes.map((node) => ({
        ...node,
        data: { ...node.data, canConnect: isValidConnection },
      })),
    };
  }, [isValidConnection, state.present]);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowFlowNode>(flow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<WorkflowFlowEdge>(flow.edges);
  const paletteGroups = useMemo(() => {
    const query = paletteQuery.trim().toLocaleLowerCase();
    const visible = registry.nodes.filter((definition) =>
      !query || `${definition.ui.label} ${definition.ui.palette_label} ${definition.ui.description} ${definition.kind}`
        .toLocaleLowerCase()
        .includes(query),
    );
    return [
      { label: "输入与交付", nodes: visible.filter((definition) => definition.kind === "input" || definition.kind === "output") },
      { label: "执行与控制", nodes: visible.filter((definition) => definition.kind !== "input" && definition.kind !== "output") },
    ].filter((group) => group.nodes.length > 0);
  }, [paletteQuery, registry.nodes]);

  useEffect(() => {
    setNodes(flow.nodes);
    setEdges(flow.edges);
  }, [flow, setEdges, setNodes]);

  useEffect(() => {
    if (!compactViewport) {
      compactInitialFitRef.current = false;
      return;
    }
    if (compactInitialFitRef.current || flow.nodes.length === 0) return;
    compactInitialFitRef.current = true;
    window.requestAnimationFrame(() => void fitView({ padding: 0.1, minZoom: 0.4, maxZoom: 0.62, duration: 0 }));
  }, [compactViewport, fitView, flow.nodes.length]);

  useEffect(() => {
    const insertedNodeId = fitAfterServerInsertRef.current;
    if (!insertedNodeId) return;
    fitAfterServerInsertRef.current = null;
    if (!compactViewport) {
      void fitView({ padding: 0.2, duration: 180 });
      return;
    }
    const inserted = flow.nodes.find((node) => node.id === insertedNodeId);
    const companion = flow.nodes.find((node) => node.data.node.kind === "agent");
    const focusNodes = (flow.nodes.length <= 6 ? flow.nodes : [inserted, companion])
      .filter((node) => Boolean(node))
      .map((node) => ({ id: node!.id }));
    void fitView({
      nodes: focusNodes.length > 1 ? focusNodes : undefined,
      padding: 0.1,
      minZoom: 0.4,
      maxZoom: 0.68,
      duration: 180,
    });
  }, [compactViewport, fitView, flow.nodes]);

  useEffect(() => {
    committedNodesRef.current = state.present.nodes;
  }, [state.present.nodes]);

  const addNode = useCallback(async (definition: WorkflowNodeRegistryEntry, position?: { x: number; y: number }) => {
    positionReservationSequenceRef.current += 1;
    const reservationId = `pending-node-${positionReservationSequenceRef.current}`;
    const allocation = reserveCanvasNodePosition(
      committedNodesRef.current,
      positionReservationsRef.current,
      reservationId,
      position,
    );
    positionReservationsRef.current = allocation.reservations;

    try {
      if (onCreateNode) {
        const graph = await enqueueServerMutation(() => onCreateNode(definition.kind, allocation.reservation.position));
        committedNodesRef.current = graph.nodes;
        dispatch({ type: "replace", graph, markSaved: true });
        const added = graph.nodes.at(-1);
        if (added) {
          dispatch({ type: "select-node", nodeId: added.id });
          onSelectionChange?.(added.id);
          fitAfterServerInsertRef.current = added.id;
        }
        setConnectionError("");
        document.querySelector<HTMLElement>(".ct-v2-node-palette")?.classList.remove("is-mobile-open");
        return;
      }

      const next = createNodeFromRegistry(
        definition,
        allocation.reservation.position.x,
        allocation.reservation.position.y,
      );
      committedNodesRef.current = [...committedNodesRef.current, next];
      dispatch({ type: "add-node", node: next });
      dispatch({ type: "select-node", nodeId: next.id });
      onSelectionChange?.(next.id);
      window.requestAnimationFrame(() => void fitView({ padding: 0.2, duration: 180 }));
    } catch (cause) {
      setConnectionError(cause instanceof Error ? cause.message : "创建节点失败");
    } finally {
      positionReservationsRef.current = releaseCanvasNodePosition(
        positionReservationsRef.current,
        reservationId,
      );
    }
  }, [dispatch, enqueueServerMutation, fitView, onCreateNode, onSelectionChange]);

  const onConnect: OnConnect = useCallback((connection) => {
    const sourcePortId = handlePortId(connection.sourceHandle, "out:");
    const targetPortId = handlePortId(connection.targetHandle, "in:");
    if (!connection.source || !connection.target || !sourcePortId || !targetPortId) return;
    const source = state.present.nodes.find((node) => node.id === connection.source);
    const target = state.present.nodes.find((node) => node.id === connection.target);
    if (!source || !target) return;
    if (onCreateEdge) {
      void enqueueServerMutation(() => onCreateEdge({
          source: { node_id: source.id, port_id: sourcePortId },
          target: { node_id: target.id, port_id: targetPortId },
        })).then((graph) => {
        dispatch({ type: "replace", graph, markSaved: true });
        setConnectionError("");
      }).catch((cause) => {
        setConnectionError(cause instanceof Error ? cause.message : "创建连线失败");
      });
      return;
    }
    edgeSequence.current += 1;
    dispatch({
      type: "add-edge",
      edge: {
        id: `edge-${source.id}-${target.id}-${edgeSequence.current}`,
        kind: connectionEdgeKind(source, sourcePortId, target, targetPortId),
        source: { node_id: source.id, port_id: sourcePortId },
        target: { node_id: target.id, port_id: targetPortId },
      },
    });
    setConnectionError("");
  }, [dispatch, enqueueServerMutation, onCreateEdge, state.present.nodes]);

  const handleConnectEnd: OnConnectEnd = useCallback((_, connectionState) => {
    if (connectionState.isValid || !connectionState.fromNode || !connectionState.toNode) return;
    const sourcePortId = handlePortId(connectionState.fromHandle?.id, "out:");
    const targetPortId = handlePortId(connectionState.toHandle?.id, "in:");
    if (!sourcePortId || !targetPortId) return;
    const validation = validateConnection(
      state.present,
      connectionState.fromNode.id,
      sourcePortId,
      connectionState.toNode.id,
      targetPortId,
    );
    setConnectionError(validation.ok ? "该连接不可用" : validation.message);
  }, [state.present]);

  const handleSelectionChange = useCallback(({ nodes: selectedNodes, edges: selectedEdges }: {
    nodes: WorkflowFlowNode[];
    edges: WorkflowFlowEdge[];
  }) => {
    // Updating a node through the inspector briefly clears xyflow's rendered
    // selection. Pane clicks are handled explicitly below, so do not let this
    // renderer-only empty event close the inspector mid-edit.
    if (selectedNodes.length === 0 && selectedEdges.length === 0 && state.selectedNodeId) return;
    if (selectedEdges[0]) {
      if (state.selectedEdgeId === selectedEdges[0].id && state.selectedNodeIds.length === 0) return;
      dispatch({ type: "select-edge", edgeId: selectedEdges[0].id });
      onSelectionChange?.(null);
      return;
    }
    const nodeIds = selectedNodes.map((node) => node.id);
    const unchanged =
      state.selectedEdgeId === null &&
      nodeIds.length === state.selectedNodeIds.length &&
      nodeIds.every((nodeId, index) => nodeId === state.selectedNodeIds[index]);
    if (unchanged) return;
    dispatch({ type: "select-nodes", nodeIds });
    onSelectionChange?.(nodeIds[0] ?? null);
  }, [dispatch, onSelectionChange, state.selectedEdgeId, state.selectedNodeId, state.selectedNodeIds]);

  return (
    <div className="ct-v2-canvas-shell" role="region" aria-label="工作流画布">
      <button type="button" className="ct-v2-mobile-palette-toggle" data-testid="workflow-mobile-palette-toggle" aria-label="打开节点库" onClick={() => document.querySelector<HTMLElement>(".ct-v2-node-palette")?.classList.toggle("is-mobile-open")}>工具</button>
      <aside className="ct-v2-node-palette" aria-label="节点库">
        <div className="ct-v2-pane-heading">
          <strong>节点库</strong>
          <span>拖到画布，或双击添加</span>
        </div>
        <input
          className="ct-v2-palette-search"
          aria-label="搜索节点"
          value={paletteQuery}
          onChange={(event) => setPaletteQuery(event.target.value)}
          placeholder="搜索节点"
        />
        <div className="ct-v2-palette-list">
          {paletteGroups.map((group) => (
            <section key={group.label} className="ct-v2-palette-group" aria-label={group.label}>
              <h3>{group.label}</h3>
              {group.nodes.map((definition) => (
                <button
                  key={definition.kind}
                  type="button"
                  draggable
                  data-testid={`workflow-palette-${definition.kind}`}
                  onDragStart={(event) => {
                    event.dataTransfer.setData("application/x-codetalk-node", definition.kind);
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  onDoubleClick={() => void addNode(definition)}
                  className="ct-v2-palette-item"
                  title={`添加${definition.ui.label}节点`}
                >
                  <span className="ct-v2-kind-mark" aria-hidden="true" />
                  <span>
                    <strong>{definition.ui.palette_label}</strong>
                    <small>{definition.ui.description}</small>
                  </span>
                </button>
              ))}
            </section>
          ))}
          {!paletteGroups.length && <p className="ct-v2-palette-empty">没有匹配的节点</p>}
        </div>
      </aside>

      <section className="ct-v2-canvas-stage" aria-label="画布编辑区">
        {connectionError && <div className="ct-v2-connection-error" role="alert">{connectionError}</div>}
        <div className="ct-v2-canvas-toolbar" aria-label="画布工具栏">
          <button type="button" onClick={() => dispatch({ type: "undo" })} disabled={!state.past.length} title="撤销">
            <Undo2 size={15} />
          </button>
          <button type="button" onClick={() => dispatch({ type: "redo" })} disabled={!state.future.length} title="重做">
            <Redo2 size={15} />
          </button>
          <button type="button" onClick={() => void fitView({ padding: 0.2, duration: 180 })} title="适应画布">
            <Focus size={15} />
          </button>
          {(state.selectedNodeIds.length > 0 || state.selectedEdgeId) && (
            <button
              type="button"
              className="is-danger"
              onClick={() => {
                state.selectedNodeIds.forEach((nodeId) => dispatch({ type: "remove-node", nodeId }));
                if (state.selectedEdgeId) dispatch({ type: "remove-edge", edgeId: state.selectedEdgeId });
                dispatch({ type: "select-node", nodeId: null });
                onSelectionChange?.(null);
              }}
              title="删除所选"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
        <ReactFlow<WorkflowFlowNode, WorkflowFlowEdge>
          className="ct-v2-xyflow"
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeDragStop={(_, node) => {
            const dragged = state.present.nodes.find((item) => item.id === node.id);
            const selectedIds = state.selectedNodeIds.includes(node.id) && state.selectedNodeIds.length > 1
              ? state.selectedNodeIds
              : [node.id];
            if (!dragged || selectedIds.length === 1) {
              dispatch({ type: "move-node", nodeId: node.id, x: node.position.x, y: node.position.y });
              return;
            }
            const deltaX = node.position.x - dragged.position.x;
            const deltaY = node.position.y - dragged.position.y;
            dispatch({
              type: "move-nodes",
              positions: state.present.nodes
                .filter((item) => selectedIds.includes(item.id))
                .map((item) => ({
                  nodeId: item.id,
                  x: item.position.x + deltaX,
                  y: item.position.y + deltaY,
                })),
            });
          }}
          onNodesDelete={(deleted) => deleted.forEach((node) => dispatch({ type: "remove-node", nodeId: node.id }))}
          onEdgesDelete={(deleted) => deleted.forEach((edge) => dispatch({ type: "remove-edge", edgeId: edge.id }))}
          onEdgeClick={(_, edge) => dispatch({ type: "select-edge", edgeId: edge.id })}
          onPaneClick={() => {
            dispatch({ type: "select-node", nodeId: null });
            onSelectionChange?.(null);
          }}
          onSelectionChange={handleSelectionChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onConnectStart={() => setConnectionError("")}
          onConnectEnd={handleConnectEnd}
          onDrop={(event) => {
            event.preventDefault();
            const kind = event.dataTransfer.getData("application/x-codetalk-node");
            const definition = registry.nodes.find((item) => item.kind === kind);
            if (!definition) return;
            void addNode(definition, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onNodeDrag={(_, node) => {
            const dragged = state.present.nodes.find((item) => item.id === node.id);
            const selectedIds = state.selectedNodeIds.includes(node.id) && state.selectedNodeIds.length > 1
              ? state.selectedNodeIds
              : [node.id];
            if (!dragged || selectedIds.length === 1) {
              setNodes((current) => current.map((item) => item.id === node.id ? { ...item, position: node.position } : item));
              return;
            }
            const deltaX = node.position.x - dragged.position.x;
            const deltaY = node.position.y - dragged.position.y;
            setNodes((current) => current.map((item) => {
              const original = state.present.nodes.find((candidate) => candidate.id === item.id);
              if (!original || !selectedIds.includes(item.id)) return item;
              return {
                ...item,
                position: { x: original.position.x + deltaX, y: original.position.y + deltaY },
              };
            }));
          }}
          multiSelectionKeyCode={["Meta", "Control"]}
          selectionKeyCode="Shift"
          selectionMode={SelectionMode.Partial}
          panOnDrag
          panOnScroll
          zoomOnScroll
          deleteKeyCode={["Backspace", "Delete"]}
          fitView
          minZoom={compactViewport ? 0.4 : 0.35}
          maxZoom={1.7}
          defaultEdgeOptions={{ type: "workflowEdge" }}
        >
          <Background gap={20} size={1} color="#cbd5df" />
          <Controls showInteractive={false} />
          {nodes.length > 8 && <MiniMap pannable zoomable nodeColor="#0e7490" />}
        </ReactFlow>
      </section>
    </div>
  );
}

const WorkflowNodeCard = memo(({ data, selected }: NodeProps<WorkflowFlowNode>) => {
  const node = data.node;
  const inputs = inputPortDefinitions(node);
  const outputs = outputPortDefinitions(node);
  return (
    <article
      className={`ct-v2-workflow-node ${selected ? "is-selected" : ""}`}
      aria-label={`${node.label} ${nodeKindLabel(node.kind)}节点`}
      data-testid={`workflow-node-${node.id}`}
      tabIndex={0}
    >
      <div className="ct-v2-node-drag">
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
      <div className="ct-v2-flow-ports">
        <div>{inputs.map((port, index) => (
          <div className="ct-v2-flow-port is-input" key={`in-${port.id}`}>
            <Handle className="ct-v2-port is-input" type="target" position={Position.Left} id={`in:${port.id}`} isValidConnection={data.canConnect} aria-label={`输入端口 ${portDisplayLabel(node, port, "input", index)}，类型 ${portDisplayType(port)}`} title={`输入：${portDisplayLabel(node, port, "input", index)} · ${portDisplayType(port)}`} />
            <span>{portDisplayLabel(node, port, "input", index)}<small>{portDisplayType(port)}</small></span>
          </div>
        ))}</div>
        <div>{outputs.map((port, index) => (
          <div className="ct-v2-flow-port is-output" key={`out-${port.id}`}>
            <span>{portDisplayLabel(node, port, "output", index)}<small>{portDisplayType(port)}</small></span>
            <Handle className="ct-v2-port is-output" type="source" position={Position.Right} id={`out:${port.id}`} aria-label={`输出端口 ${portDisplayLabel(node, port, "output", index)}，类型 ${portDisplayType(port)}`} title={`输出：${portDisplayLabel(node, port, "output", index)} · ${portDisplayType(port)}`} />
          </div>
        ))}</div>
      </div>
    </article>
  );
});
WorkflowNodeCard.displayName = "WorkflowNodeCard";

const WorkflowEdge = memo((props: EdgeProps<WorkflowFlowEdge>) => {
  const [path, labelX, labelY] = getBezierPath(props);
  const label = props.data?.label;
  return (
    <>
      <BaseEdge id={props.id} path={path} className={`ct-v2-edge ${props.selected ? "is-selected" : ""}`} />
      {label && (
        <EdgeLabelRenderer>
          <span
            className="ct-v2-edge-label nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY - 7}px)` }}
          >
            {label}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
});
WorkflowEdge.displayName = "WorkflowEdge";

function handlePortId(value: string | null | undefined, prefix: string): string {
  return value?.startsWith(prefix) ? value.slice(prefix.length) : "";
}

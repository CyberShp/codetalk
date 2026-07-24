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
  validateConnection,
} from "../workflow-graph";
import {
  applyFlowPositions,
  authoringGraphToFlow,
  type WorkflowFlowEdge,
  type WorkflowFlowNode,
} from "../workflow-xyflow";

interface Props {
  state: WorkflowEditorState;
  dispatch: Dispatch<WorkflowEditorAction>;
  registry: WorkflowNodeRegistry;
  onSelectionChange?: (nodeId: string | null) => void;
}

export function WorkflowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvasSurface {...props} />
    </ReactFlowProvider>
  );
}

function WorkflowCanvasSurface({ state, dispatch, registry, onSelectionChange }: Props) {
  const nodeTypes = useMemo(() => ({ workflowNode: WorkflowNodeCard }), []);
  const edgeTypes = useMemo(() => ({ workflowEdge: WorkflowEdge }), []);
  const edgeSequence = useRef(0);
  const allocatedNodesRef = useRef<WorkflowGraphNode[]>(state.present.nodes);
  const { screenToFlowPosition, fitView } = useReactFlow<WorkflowFlowNode, WorkflowFlowEdge>();
  const [connectionError, setConnectionError] = useState("");

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

  useEffect(() => {
    setNodes(flow.nodes);
    setEdges(flow.edges);
  }, [flow, setEdges, setNodes]);

  // Palette double-clicks can arrive before React has committed the first
  // insertion. Reserve the position synchronously so two quick adds never
  // stack at the same coordinates and make the lower node unreachable.
  useEffect(() => {
    allocatedNodesRef.current = state.present.nodes;
  }, [state.present.nodes]);

  const addNode = useCallback((definition: WorkflowNodeRegistryEntry, position?: { x: number; y: number }) => {
    const suggestedPosition = position ?? nextAvailableNodePosition(allocatedNodesRef.current);
    const next = createNodeFromRegistry(
      definition,
      suggestedPosition.x,
      suggestedPosition.y,
    );
    allocatedNodesRef.current = [...allocatedNodesRef.current, next];
    dispatch({ type: "add-node", node: next });
    dispatch({ type: "select-node", nodeId: next.id });
    onSelectionChange?.(next.id);
    window.requestAnimationFrame(() => void fitView({ padding: 0.2, duration: 180 }));
  }, [dispatch, fitView, onSelectionChange]);

  const onConnect: OnConnect = useCallback((connection) => {
    const sourcePortId = handlePortId(connection.sourceHandle, "out:");
    const targetPortId = handlePortId(connection.targetHandle, "in:");
    if (!connection.source || !connection.target || !sourcePortId || !targetPortId) return;
    const source = state.present.nodes.find((node) => node.id === connection.source);
    const target = state.present.nodes.find((node) => node.id === connection.target);
    if (!source || !target) return;
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
  }, [dispatch, state.present.nodes]);

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
  }, [dispatch, onSelectionChange, state.selectedEdgeId, state.selectedNodeIds]);

  return (
    <div className="ct-v2-canvas-shell">
      <aside className="ct-v2-node-palette" aria-label="节点库">
        <div className="ct-v2-pane-heading">
          <strong>节点库</strong>
          <span>拖到画布，或双击添加</span>
        </div>
        <div className="ct-v2-palette-list">
          {registry.nodes.map((definition) => (
            <button
              key={definition.kind}
              type="button"
              draggable
              data-testid={`workflow-palette-${definition.kind}`}
              onDragStart={(event) => {
                event.dataTransfer.setData("application/x-codetalk-node", definition.kind);
                event.dataTransfer.effectAllowed = "move";
              }}
              onDoubleClick={() => addNode(definition)}
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
        </div>
      </aside>

      <section className="ct-v2-canvas-stage" aria-label="工作流画布">
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
          onNodeDragStop={(_, node) => dispatch({ type: "move-node", nodeId: node.id, x: node.position.x, y: node.position.y })}
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
            addNode(definition, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onNodeDrag={(_, node) => {
            setNodes((current) => current.map((item) => item.id === node.id ? { ...item, position: node.position } : item));
          }}
          multiSelectionKeyCode={["Meta", "Control"]}
          selectionKeyCode="Shift"
          selectionMode={SelectionMode.Partial}
          panOnDrag
          panOnScroll
          zoomOnScroll
          deleteKeyCode={["Backspace", "Delete"]}
          fitView
          minZoom={0.35}
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
        <div>{inputs.map((port) => (
          <div className="ct-v2-flow-port is-input" key={`in-${port.id}`}>
            <Handle className="ct-v2-port is-input" type="target" position={Position.Left} id={`in:${port.id}`} isValidConnection={data.canConnect} aria-label={`输入端口 ${port.id}，类型 ${port.type}`} title={`输入：${port.id} · ${port.type}`} />
            <span>{port.id}<small>{port.type}</small></span>
          </div>
        ))}</div>
        <div>{outputs.map((port) => (
          <div className="ct-v2-flow-port is-output" key={`out-${port.id}`}>
            <span>{port.id}<small>{port.type}</small></span>
            <Handle className="ct-v2-port is-output" type="source" position={Position.Right} id={`out:${port.id}`} aria-label={`输出端口 ${port.id}，类型 ${port.type}`} title={`输出：${port.id} · ${port.type}`} />
          </div>
        ))}</div>
      </div>
    </article>
  );
});
WorkflowNodeCard.displayName = "WorkflowNodeCard";

function nextAvailableNodePosition(nodes: WorkflowGraphNode[]): { x: number; y: number } {
  // New cards must not land beneath an existing card. The grid keeps the
  // insertion predictable while the flow remains pannable for larger graphs.
  const columns = [80, 400, 720];
  const rows = [140, 390, 640, 890, 1140];
  for (const y of rows) {
    for (const x of columns) {
      const isFree = nodes.every((node) =>
        Math.abs(node.position.x - x) >= 250 || Math.abs(node.position.y - y) >= 180,
      );
      if (isFree) return { x, y };
    }
  }
  return { x: 80 + nodes.length * 40, y: 140 + nodes.length * 210 };
}

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

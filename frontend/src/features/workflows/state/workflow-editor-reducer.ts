import type {
  AuthoringGraphV2,
  WorkflowGraphEdge,
  WorkflowGraphNode,
} from "@/lib/types/workflow";

export interface WorkflowEditorState {
  past: AuthoringGraphV2[];
  present: AuthoringGraphV2;
  future: AuthoringGraphV2[];
  selectedNodeId: string | null;
  selectedNodeIds: string[];
  selectedEdgeId: string | null;
  revision: number;
  savedRevision: number;
}

export type WorkflowEditorAction =
  | { type: "replace"; graph: AuthoringGraphV2; markSaved?: boolean }
  | { type: "update-node"; node: WorkflowGraphNode }
  | { type: "update-node-with-edges"; node: WorkflowGraphNode; edges: WorkflowGraphEdge[] }
  | { type: "move-node"; nodeId: string; x: number; y: number }
  | { type: "move-nodes"; positions: Array<{ nodeId: string; x: number; y: number }> }
  | { type: "add-node"; node: WorkflowGraphNode }
  | { type: "remove-node"; nodeId: string }
  | { type: "add-edge"; edge: WorkflowGraphEdge }
  | { type: "remove-edge"; edgeId: string }
  | { type: "select-node"; nodeId: string | null }
  | { type: "select-nodes"; nodeIds: string[] }
  | { type: "select-edge"; edgeId: string | null }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "mark-saved"; revision: number };

export function createEditorState(graph: AuthoringGraphV2): WorkflowEditorState {
  return {
    past: [],
    present: graph,
    future: [],
    selectedNodeId: null,
    selectedNodeIds: [],
    selectedEdgeId: null,
    revision: 0,
    savedRevision: 0,
  };
}

export function workflowEditorReducer(
  state: WorkflowEditorState,
  action: WorkflowEditorAction,
): WorkflowEditorState {
  if (action.type === "select-node") {
    return {
      ...state,
      selectedNodeId: action.nodeId,
      selectedNodeIds: action.nodeId ? [action.nodeId] : [],
      selectedEdgeId: null,
    };
  }
  if (action.type === "select-nodes") {
    const nodeIds = Array.from(new Set(action.nodeIds));
    return {
      ...state,
      selectedNodeId: nodeIds[0] ?? null,
      selectedNodeIds: nodeIds,
      selectedEdgeId: null,
    };
  }
  if (action.type === "select-edge") {
    return {
      ...state,
      selectedNodeId: null,
      selectedNodeIds: [],
      selectedEdgeId: action.edgeId,
    };
  }
  if (action.type === "mark-saved") {
    return action.revision > state.revision
      ? state
      : { ...state, savedRevision: action.revision };
  }
  if (action.type === "undo") {
    if (!state.past.length) return state;
    const previous = state.past[state.past.length - 1];
    return {
      ...state,
      past: state.past.slice(0, -1),
      present: previous,
      future: [state.present, ...state.future].slice(0, 50),
      revision: state.revision + 1,
    };
  }
  if (action.type === "redo") {
    if (!state.future.length) return state;
    return {
      ...state,
      past: [...state.past, state.present].slice(-50),
      present: state.future[0],
      future: state.future.slice(1),
      revision: state.revision + 1,
    };
  }
  if (action.type === "replace") {
    return {
      ...createEditorState(action.graph),
      revision: state.revision + 1,
      savedRevision: action.markSaved ? state.revision + 1 : state.savedRevision,
    };
  }

  let next = state.present;
  if (action.type === "update-node") {
    next = {
      ...next,
      nodes: next.nodes.map((node) => (node.id === action.node.id ? action.node : node)),
    };
  } else if (action.type === "update-node-with-edges") {
    next = {
      ...next,
      nodes: next.nodes.map((node) => (node.id === action.node.id ? action.node : node)),
      edges: action.edges,
    };
  } else if (action.type === "move-node") {
    next = {
      ...next,
      nodes: next.nodes.map((node) =>
        node.id === action.nodeId
          ? { ...node, position: { x: action.x, y: action.y } }
          : node,
      ),
    };
  } else if (action.type === "move-nodes") {
    const positions = new Map(action.positions.map((position) => [position.nodeId, position]));
    next = {
      ...next,
      nodes: next.nodes.map((node) => {
        const position = positions.get(node.id);
        return position ? { ...node, position: { x: position.x, y: position.y } } : node;
      }),
    };
  } else if (action.type === "add-node") {
    next = { ...next, nodes: [...next.nodes, action.node] };
  } else if (action.type === "remove-node") {
    next = {
      ...next,
      nodes: next.nodes.filter((node) => node.id !== action.nodeId),
      edges: next.edges.filter(
        (edge) =>
          edge.source.node_id !== action.nodeId && edge.target.node_id !== action.nodeId,
      ),
    };
  } else if (action.type === "add-edge") {
    next = { ...next, edges: [...next.edges, action.edge] };
  } else if (action.type === "remove-edge") {
    next = { ...next, edges: next.edges.filter((edge) => edge.id !== action.edgeId) };
  }
  if (next === state.present) return state;
  return {
    ...state,
    past: [...state.past, state.present].slice(-50),
    present: next,
    future: [],
    selectedNodeId: action.type === "remove-node" ? null : state.selectedNodeId,
    selectedNodeIds: action.type === "remove-node"
      ? state.selectedNodeIds.filter((nodeId) => nodeId !== action.nodeId)
      : state.selectedNodeIds,
    selectedEdgeId: action.type === "remove-edge" ? null : state.selectedEdgeId,
    revision: state.revision + 1,
  };
}

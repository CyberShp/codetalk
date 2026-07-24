import type { Connection, Edge, Node } from "@xyflow/react";

import type {
  AuthoringGraphV2,
  WorkflowGraphEdge,
  WorkflowGraphNode,
} from "@/lib/types/workflow";
import { connectionLabel } from "./workflow-graph.ts";

export type WorkflowFlowNodeData = {
  node: WorkflowGraphNode;
  canConnect?: (connection: Connection | Edge) => boolean;
};

export type WorkflowFlowEdgeData = {
  workflowEdge: WorkflowGraphEdge;
  label: string;
};

export type WorkflowFlowNode = Node<WorkflowFlowNodeData, "workflowNode">;
export type WorkflowFlowEdge = Edge<WorkflowFlowEdgeData, "workflowEdge">;

export function authoringGraphToFlow(
  graph: AuthoringGraphV2,
  selection: { nodeIds?: string[]; edgeId?: string | null } = {},
): {
  nodes: WorkflowFlowNode[];
  edges: WorkflowFlowEdge[];
} {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const selectedNodeIds = new Set(selection.nodeIds ?? []);
  return {
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode",
      position: { ...node.position },
      selected: selectedNodeIds.has(node.id),
      data: { node },
    })),
    edges: graph.edges.map((workflowEdge) => ({
      id: workflowEdge.id,
      type: "workflowEdge",
      source: workflowEdge.source.node_id,
      target: workflowEdge.target.node_id,
      sourceHandle: `out:${workflowEdge.source.port_id}`,
      targetHandle: `in:${workflowEdge.target.port_id}`,
      selectable: true,
      selected: workflowEdge.id === selection.edgeId,
      data: {
        workflowEdge,
        label: connectionLabel(workflowEdge, nodesById),
      },
    })),
  };
}

export function applyFlowPositions(
  graph: AuthoringGraphV2,
  nodes: ReadonlyArray<Pick<WorkflowFlowNode, "id" | "position">>,
): AuthoringGraphV2 {
  const positions = new Map(nodes.map((node) => [node.id, node.position]));
  return {
    ...graph,
    nodes: graph.nodes.map((node) => {
      const position = positions.get(node.id);
      return position
        ? { ...node, position: { x: Math.max(0, position.x), y: Math.max(0, position.y) } }
        : node;
    }),
  };
}

import assert from "node:assert/strict";
import test from "node:test";

import { createStarterGraph } from "../workflow-graph.ts";
import {
  createEditorState,
  workflowEditorReducer,
} from "./workflow-editor-reducer.ts";

test("selection keeps every xyflow-selected node while exposing the first node to the inspector", () => {
  const state = createEditorState(createStarterGraph("selection", "Selection"));
  const next = workflowEditorReducer(state, {
    type: "select-nodes",
    nodeIds: ["repo", "analysis_target", "analyze"],
  });

  assert.deepEqual(next.selectedNodeIds, ["repo", "analysis_target", "analyze"]);
  assert.equal(next.selectedNodeId, "repo");
});

test("undo restores a deleted node together with every connected edge", () => {
  const initial = createEditorState(createStarterGraph("undo", "Undo"));
  const removed = workflowEditorReducer(initial, { type: "remove-node", nodeId: "analyze" });
  const restored = workflowEditorReducer(removed, { type: "undo" });

  assert.equal(removed.present.nodes.some((node) => node.id === "analyze"), false);
  assert.equal(removed.present.edges.some((edge) => edge.target.node_id === "analyze"), false);
  assert.equal(restored.present.nodes.some((node) => node.id === "analyze"), true);
  assert.equal(restored.present.edges.some((edge) => edge.target.node_id === "analyze"), true);
  assert.equal(restored.present.edges.some((edge) => edge.source.node_id === "analyze"), true);
});

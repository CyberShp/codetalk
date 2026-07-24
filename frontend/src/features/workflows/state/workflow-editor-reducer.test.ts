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

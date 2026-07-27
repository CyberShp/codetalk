"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FlaskConical,
  Loader2,
  Save,
  Send,
} from "lucide-react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api";
import { workflowsApi } from "@/lib/api/workflows";
import type {
  AuthoringGraph,
  CompiledWorkflowPlan,
  WorkflowCapabilities,
  WorkflowGraphNode,
  WorkflowNodeRegistry,
  WorkflowProviderCapability,
  WorkflowResourceMeta,
  WorkflowValidationResult,
  WorkflowVersion,
} from "@/lib/types/workflow";
import { NodeInspector, type PortMutation } from "./node-inspector";
import { WorkflowCanvas } from "./workflow-canvas";
import { TrialRunPanel } from "../trial-run-panel";
import {
  createEditorState,
  workflowEditorReducer,
} from "../state/workflow-editor-reducer";

type BottomTab = "problems" | "plan" | "trial";
type ResourceName = "capabilities" | "providers" | "registry";
type ResourceState = {
  state: "loading" | "ready" | "failed";
  error?: Error;
  meta?: WorkflowResourceMeta;
};

const emptyRegistry: WorkflowNodeRegistry = { schema_version: 3, nodes: [] };

export function WorkflowDesigner({ workflowId }: { workflowId: string }) {
  const router = useRouter();
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [capabilities, setCapabilities] = useState<WorkflowCapabilities | null>(null);
  const [providers, setProviders] = useState<WorkflowProviderCapability[]>([]);
  const [nodeRegistry, setNodeRegistry] = useState<WorkflowNodeRegistry | null>(null);
  const [needsDraft, setNeedsDraft] = useState<string | null>(null);
  const [copySourceId, setCopySourceId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [resources, setResources] = useState<Record<ResourceName, ResourceState>>({
    capabilities: { state: "loading" },
    providers: { state: "loading" },
    registry: { state: "loading" },
  });

  const loadWorkflow = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const detail = await workflowsApi.get(workflowId);
      const draftId = detail.v2?.current_draft_version_id;
      if (!draftId) {
        const publishedId = detail.v2?.published_version_id;
        if (publishedId) {
          const published = await workflowsApi.version(workflowId, publishedId);
          if (
            published.editor_mode === "read_only_legacy" ||
            published.authoring_graph.schema_version === 1
          ) {
            router.replace(
              `/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(publishedId)}`,
            );
            return;
          }
        }
        setNeedsDraft(detail.v2?.published_version_id ?? "published");
        setCopySourceId(detail.v2 ? null : workflowId);
        setVersion(null);
        return;
      }
      const loaded = await workflowsApi.version(workflowId, draftId);
      if (loaded.editor_mode === "legacy" || loaded.authoring_graph.schema_version === 2) {
        router.replace(`/workflows/${encodeURIComponent(workflowId)}/legacy?workflow=${encodeURIComponent(workflowId)}&version=${encodeURIComponent(draftId)}&step=5`);
        return;
      }
      if (loaded.editor_mode === "read_only_legacy" || loaded.authoring_graph.schema_version === 1) {
        router.replace(`/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(draftId)}`);
        return;
      }
      setVersion(loaded);
      setNeedsDraft(null);
      setCopySourceId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "工作流加载失败");
    } finally {
      setLoading(false);
    }
  }, [router, workflowId]);

  const loadResource = useCallback(async (resource: ResourceName) => {
    setResources((current) => ({ ...current, [resource]: { state: "loading" } }));
    try {
      let meta: WorkflowResourceMeta | undefined;
      if (resource === "capabilities") {
        const payload = await workflowsApi.capabilities();
        setCapabilities(payload);
        meta = payload.meta;
      }
      if (resource === "providers") {
        const payload = await workflowsApi.providers();
        setProviders(payload.providers);
        meta = payload.meta;
      }
      if (resource === "registry") {
        const payload = await workflowsApi.nodeRegistry();
        setNodeRegistry(payload);
        meta = payload.meta;
      }
      setResources((current) => ({ ...current, [resource]: { state: "ready", meta } }));
    } catch (cause) {
      setResources((current) => ({ ...current, [resource]: { state: "failed", error: cause instanceof Error ? cause : new Error("资源加载失败") } }));
    }
  }, []);

  useEffect(() => { void loadWorkflow(); }, [loadWorkflow]);
  useEffect(() => {
    (Object.keys(resources) as ResourceName[]).forEach((resource) => void loadResource(resource));
    // Resource queries are intentionally independent. A registry fault must not block the canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadResource]);

  if (loading) return <WorkbenchLoading label="正在载入工作流草稿" />;
  if (error) return <WorkbenchError message={error} onRetry={loadWorkflow} />;
  if (needsDraft) {
    return (
      <div className="ct-v2-empty-state">
        <FlaskConical size={28} />
        <h1>{copySourceId ? "内置工作流不可直接修改" : "已发布版本不可直接修改"}</h1>
        <p>{copySourceId ? "另存为自定义工作流后，将创建独立的 V3 画布副本，原始发布版本保持只读。" : "创建一个基于当前发布版本的新草稿，再进入设计器。"}</p>
        <div>
          <Link href="/workflows">返回工作流库</Link>
          <button
            type="button"
            onClick={async () => {
              setLoading(true);
              try {
                if (copySourceId) {
                  const versions = await workflowsApi.versions(copySourceId);
                  const source = versions.items.find((item) => item.state === "published");
                  if (!source) throw new Error("该内置工作流缺少可复制的发布版本");
                  const copied = await workflowsApi.copyVersionToV3(copySourceId, source.version_id);
                  router.replace(copied.designer_url);
                  return;
                }
                await workflowsApi.createDraft(
                  workflowId,
                  needsDraft === "published" ? undefined : needsDraft,
                );
                await loadWorkflow();
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : copySourceId ? "另存为自定义工作流失败" : "创建草稿失败");
              }
            }}
          >
            {copySourceId ? "另存为自定义工作流" : "创建新草稿"}
          </button>
        </div>
      </div>
    );
  }
  if (!version) {
    return <WorkbenchError message="该版本是只读旧工作流，请先复制为草稿。" onRetry={loadWorkflow} />;
  }
  return (
    <LoadedWorkflowDesigner
      key={version.version_id}
      workflowId={workflowId}
      version={version as WorkflowVersion & { authoring_graph: AuthoringGraph }}
      capabilities={capabilities}
      providers={providers}
      nodeRegistry={nodeRegistry ?? emptyRegistry}
      resources={resources}
      onRetryResource={loadResource}
    />
  );
}

function LoadedWorkflowDesigner({
  workflowId,
  version,
  capabilities,
  providers,
  nodeRegistry,
  resources,
  onRetryResource,
}: {
  workflowId: string;
  version: WorkflowVersion & { authoring_graph: AuthoringGraph };
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  nodeRegistry: WorkflowNodeRegistry;
  resources: Record<ResourceName, ResourceState>;
  onRetryResource: (resource: ResourceName) => Promise<void>;
}) {
  const router = useRouter();
  const [state, dispatch] = useReducer(
    workflowEditorReducer,
    version.authoring_graph,
    createEditorState,
  );
  const latestRevision = useRef(state.revision);
  const latestSavedRevision = useRef(state.savedRevision);
  const latestGraph = useRef(state.present);
  const serverDraftRevision = useRef(version.draft_revision);
  const authoringMutationQueue = useRef<Promise<void>>(Promise.resolve());
  const [saveState, setSaveState] = useState<"saved" | "saving" | "failed">("saved");
  const [validation, setValidation] = useState<WorkflowValidationResult | null>(version.validation);
  const [plan, setPlan] = useState<CompiledWorkflowPlan | null>(version.compiled_plan);
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [action, setAction] = useState<"validate" | "compile" | "publish" | null>(null);
  const [message, setMessage] = useState("");
  const isV3 = state.present.schema_version === 3;

  const requireServerDraftRevision = () => {
    const revision = serverDraftRevision.current;
    if (revision === undefined || revision < 1) {
      throw new Error("草稿版本信息缺失，请刷新页面后重试");
    }
    return revision;
  };

  latestRevision.current = state.revision;
  latestSavedRevision.current = state.savedRevision;
  latestGraph.current = state.present;

  const persistLatestDraft = useCallback(async (force = false) => {
    const revision = latestRevision.current;
    if (!force && revision === latestSavedRevision.current) return;
    const graph = latestGraph.current;
    setSaveState("saving");
    try {
      const saved = await workflowsApi.updateDraft(
        workflowId,
        version.version_id,
        graph,
        isV3 ? serverDraftRevision.current : undefined,
      );
      if (isV3) serverDraftRevision.current = saved.draft_revision;
      latestSavedRevision.current = Math.max(latestSavedRevision.current, revision);
      dispatch({ type: "mark-saved", revision });
      if (latestRevision.current === revision) setSaveState("saved");
    } catch (cause) {
      setSaveState("failed");
      setMessage(cause instanceof Error ? cause.message : "保存失败，请刷新后重试");
      throw cause;
    }
  }, [isV3, version.version_id, workflowId]);

  const enqueueAuthoringMutation = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const result = authoringMutationQueue.current.then(operation, operation);
    authoringMutationQueue.current = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }, []);

  const recordServerGraph = useCallback((graph: AuthoringGraph, draftRevision?: number) => {
    latestGraph.current = graph;
    if (isV3) serverDraftRevision.current = draftRevision;
    latestRevision.current += 1;
    latestSavedRevision.current = latestRevision.current;
    setSaveState("saved");
  }, [isV3]);

  const applyServerGraph = useCallback((graph: AuthoringGraph, selectedNodeId?: string, draftRevision?: number) => {
    recordServerGraph(graph, draftRevision);
    dispatch({ type: "replace", graph, markSaved: true });
    if (selectedNodeId) dispatch({ type: "select-node", nodeId: selectedNodeId });
  }, [recordServerGraph]);

  useEffect(() => {
    if (state.revision === state.savedRevision) return;
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      void enqueueAuthoringMutation(() => persistLatestDraft()).catch(() => undefined);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [enqueueAuthoringMutation, persistLatestDraft, state.present, state.revision, state.savedRevision]);

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (state.revision === state.savedRevision) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [state.revision, state.savedRevision]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      dispatch({ type: event.shiftKey ? "redo" : "undo" });
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const selectedNode = state.present.nodes.find((node) => node.id === state.selectedNodeId) ?? null;
  const updateNode = (node: WorkflowGraphNode, portMutation?: PortMutation) => {
    const currentGraph = latestGraph.current;
    const edges = portMutation
      ? currentGraph.edges.flatMap((item) => {
          const endpoint = portMutation.direction === "input" ? item.target : item.source;
          if (endpoint.node_id !== node.id || endpoint.port_id !== portMutation.oldId) return [item];
          if (portMutation.kind === "delete") return [];
          return [portMutation.direction === "input"
            ? { ...item, target: { ...item.target, port_id: portMutation.newId } }
            : { ...item, source: { ...item.source, port_id: portMutation.newId } }];
        })
      : currentGraph.edges;
    latestGraph.current = {
      ...currentGraph,
      nodes: currentGraph.nodes.map((item) => item.id === node.id ? node : item),
      edges,
    };
    latestRevision.current += 1;
    dispatch(portMutation
      ? { type: "update-node-with-edges", node, edges }
      : { type: "update-node", node });
    if (node.kind !== "output") return;
    const sourceId = String(node.config.source_node_id ?? "");
    const source = currentGraph.nodes.find((item) => item.id === sourceId && item.kind === "agent");
    const artifact = String(node.config.artifact ?? "").trim();
    if (source && artifact) {
      dispatch({
        type: "update-node",
        node: {
          ...source,
          config: {
            ...source.config,
            required_artifacts: Array.from(new Set([...(source.config.required_artifacts ?? []), artifact])),
          },
        },
      });
    }
  };

  const createNode = async (kind: string, position: { x: number; y: number }) => {
    if (!isV3) throw new Error("旧工作流使用兼容编辑器，请保存当前草稿。");
    return enqueueAuthoringMutation(async () => {
      await persistLatestDraft();
      const existingInputs = latestGraph.current.nodes.filter((node) => node.kind === "input").length;
      const preset = kind === "input"
        ? existingInputs === 0
          ? { label: "源码工作区", config: { type: "directory", required: true, resolver: "workspace" } }
          : { label: "输入材料", config: { type: "file", required: false, resolver: "local" } }
        : kind === "agent"
          ? { label: "源码分析" }
          : kind === "output"
            ? { label: "分析报告", config: { artifact: "report.md", media_type: "text/markdown" } }
            : {};
      const updated = await workflowsApi.addNode(workflowId, version.version_id, { kind, position, ...preset }, requireServerDraftRevision());
      const graph = updated.draft.authoring_graph as AuthoringGraph;
      recordServerGraph(graph, updated.draft.draft_revision);
      return graph;
    });
  };

  const createPort = async (nodeId: string, direction: "input" | "output") => {
    if (!isV3) throw new Error("旧工作流的端口由兼容编辑器维护。");
    return enqueueAuthoringMutation(async () => {
      await persistLatestDraft();
      const updated = await workflowsApi.addPort(workflowId, version.version_id, nodeId, {
        direction,
        label: `${direction === "input" ? "输入" : "输出"}端口`,
        type: "file",
        required: false,
        collection: false,
      }, requireServerDraftRevision());
      const graph = updated.draft.authoring_graph as AuthoringGraph;
      const node = graph.nodes.find((item) => item.id === nodeId);
      if (!node) throw new Error("后端未返回新增端口所在节点");
      applyServerGraph(graph, nodeId, updated.draft.draft_revision);
      return node;
    });
  };

  const updatePort = async (
    nodeId: string,
    portId: string,
    patch: { label?: string; type?: string; required?: boolean; collection?: boolean },
  ) => {
    if (!isV3) throw new Error("旧工作流的端口由兼容编辑器维护。");
    return enqueueAuthoringMutation(async () => {
      await persistLatestDraft();
      const updated = await workflowsApi.updatePort(
        workflowId,
        version.version_id,
        nodeId,
        portId,
        patch,
        requireServerDraftRevision(),
      );
      const graph = updated.draft.authoring_graph as AuthoringGraph;
      const node = graph.nodes.find((item) => item.id === nodeId);
      if (!node) throw new Error("后端未返回更新端口所在节点");
      applyServerGraph(graph, nodeId, updated.draft.draft_revision);
      return node;
    });
  };

  const deletePort = async (nodeId: string, portId: string) => {
    if (!isV3) throw new Error("旧工作流的端口由兼容编辑器维护。");
    return enqueueAuthoringMutation(async () => {
      await persistLatestDraft();
      const updated = await workflowsApi.deletePort(
        workflowId,
        version.version_id,
        nodeId,
        portId,
        requireServerDraftRevision(),
      );
      const graph = updated.draft.authoring_graph as AuthoringGraph;
      const node = graph.nodes.find((item) => item.id === nodeId);
      if (!node) throw new Error("后端未返回删除端口后的节点");
      applyServerGraph(graph, nodeId, updated.draft.draft_revision);
      return node;
    });
  };

  const createEdge = async (payload: { source: { node_id: string; port_id: string }; target: { node_id: string; port_id: string } }) => {
    if (!isV3) throw new Error("旧工作流的连线由兼容编辑器维护。");
    return enqueueAuthoringMutation(async () => {
      await persistLatestDraft();
      const updated = await workflowsApi.addEdge(workflowId, version.version_id, payload, requireServerDraftRevision());
      const graph = updated.draft.authoring_graph as AuthoringGraph;
      recordServerGraph(graph, updated.draft.draft_revision);
      return graph;
    });
  };

  const saveNow = async () => {
    await enqueueAuthoringMutation(() => persistLatestDraft(true));
    return isV3 ? requireServerDraftRevision() : undefined;
  };

  const publishWorkflow = async () => {
    setAction("publish");
    setMessage("");
    try {
      await saveNow();
      const validationResult = await workflowsApi.validate(
        workflowId,
        version.version_id,
        isV3 ? requireServerDraftRevision() : undefined,
      );
      if (validationResult.draft_revision !== undefined) serverDraftRevision.current = validationResult.draft_revision;
      setValidation(validationResult);
      if (!validationResult.valid) {
        setBottomTab("problems");
        setMessage(`发现 ${validationResult.errors.length} 个阻断问题`);
        return;
      }
      const compiled = await workflowsApi.compile(
        workflowId,
        version.version_id,
        isV3 ? requireServerDraftRevision() : undefined,
      );
      if (compiled.draft_revision !== undefined) serverDraftRevision.current = compiled.draft_revision;
      setPlan(compiled.compiled_plan);
      const published = await workflowsApi.publish(
        workflowId,
        version.version_id,
        isV3 ? requireServerDraftRevision() : undefined,
      );
      setMessage(`V${published.version_number} 已发布`);
      router.push(`/workflows/${encodeURIComponent(workflowId)}/versions`);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "操作失败");
      setBottomTab("problems");
    } finally {
      setAction(null);
    }
  };

  return (
    <div className="ct-v2-designer-page">
      <header className="ct-v2-designer-header">
        <div className="ct-v2-designer-title">
          <Link href="/workflows" aria-label="返回工作流库" title="返回工作流库"><ArrowLeft size={17} /></Link>
          <div>
            <p>工作流设计</p>
            <h1>{state.present.name}</h1>
          </div>
          <span className="ct-v2-version-chip">草稿 V{version.version_number}</span>
          <span className={`ct-v2-save-state is-${saveState}`}>
            {saveState === "saving" ? <Loader2 size={13} className="animate-spin" /> : saveState === "saved" ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            <span data-testid="workflow-save-status">{saveState === "saving" ? "保存中" : saveState === "saved" ? "已保存" : "保存失败"}</span>
          </span>
        </div>
        <div className="ct-v2-designer-actions">
          <button type="button" className="is-save" onClick={() => void saveNow()} title="保存草稿"><Save size={15} />保存</button>
          <button type="button" className="is-trial" onClick={() => setBottomTab("trial")} disabled={Boolean(action)}><FlaskConical size={15} />试运行</button>
          <button type="button" className="is-primary" onClick={() => void publishWorkflow()} disabled={Boolean(action)}>{action === "publish" ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}发布</button>
        </div>
      </header>

      {message && <div className="ct-v2-inline-message" role="status">{message}</div>}

      <ResourceStates resources={resources} onRetry={onRetryResource} />
      <div className={`ct-v2-designer-grid ${selectedNode ? "has-inspector" : ""}`}>
          <WorkflowCanvas state={state} dispatch={dispatch} registry={nodeRegistry} onCreateNode={isV3 ? createNode : undefined} onCreateEdge={isV3 ? createEdge : undefined} />
        {selectedNode && (
          <NodeInspector
            node={selectedNode}
            schemaVersion={state.present.schema_version}
            capabilities={capabilities}
            providers={providers}
            registry={nodeRegistry}
            onChange={updateNode}
            onCreatePort={isV3 ? createPort : undefined}
            onUpdatePort={isV3 ? updatePort : undefined}
            onDeletePort={isV3 ? deletePort : undefined}
            onClose={() => dispatch({ type: "select-node", nodeId: null })}
          />
        )}
      </div>

      <section className="ct-v2-bottom-panel">
        <div className="ct-v2-bottom-tabs" role="tablist">
          <button type="button" className={bottomTab === "problems" ? "is-active" : ""} onClick={() => setBottomTab("problems")}>问题 {validation ? validation.errors.length + validation.warnings.length : 0}</button>
          <button type="button" className={bottomTab === "plan" ? "is-active" : ""} onClick={() => setBottomTab("plan")}>执行计划</button>
          <button type="button" className={bottomTab === "trial" ? "is-active" : ""} onClick={() => setBottomTab("trial")}>试运行</button>
        </div>
        <div className="ct-v2-bottom-content">
          {bottomTab === "problems" && (
            <ProblemList validation={validation} onFocus={(nodeId) => dispatch({ type: "select-node", nodeId })} />
          )}
          {bottomTab === "plan" && <PlanPreview plan={plan} graph={state.present} />}
          {bottomTab === "trial" && <TrialRunPanel workflowId={workflowId} versionId={version.version_id} graph={state.present} onBeforeRun={saveNow} onDraftRevision={(revision) => { serverDraftRevision.current = revision; }} />}
        </div>
      </section>
    </div>
  );
}

function ProblemList({ validation, onFocus }: { validation: WorkflowValidationResult | null; onFocus: (nodeId: string) => void }) {
  if (!validation) return <p className="ct-v2-bottom-empty">发布时会检查端口、执行器、MCP、Skills 和输出契约。</p>;
  if (!validation.errors.length && !validation.warnings.length) return <p className="ct-v2-bottom-empty is-success"><CheckCircle2 size={16} />没有阻断问题，可以发布。</p>;
  return <div className="ct-v2-problem-list">{[...validation.errors, ...validation.warnings].map((item, index) => (
    <button key={`${item.code}-${index}`} type="button" onClick={() => item.node_id && onFocus(item.node_id)}>
      <AlertTriangle size={14} /><span><strong>{issueTitle(item.code)}</strong>{issueMessage(item.code, item.message)}</span><em>{item.node_id ?? "工作流"}</em>
    </button>
  ))}</div>;
}

function ResourceStates({ resources, onRetry }: {
  resources: Record<ResourceName, ResourceState>;
  onRetry: (resource: ResourceName) => Promise<void>;
}) {
  return <div className="ct-v2-resource-states">{(Object.keys(resources) as ResourceName[]).map((resource) => {
    const item = resources[resource];
    const diagnostic = item.error instanceof ApiRequestError ? item.error : null;
    const endpoint = diagnostic?.endpoint ?? `/api/workbench/${resource === "capabilities" ? "workflow-capabilities" : resource === "providers" ? "provider-capabilities" : "node-registry"}`;
    const status = diagnostic?.status ?? "-";
    const backendCommit = diagnostic?.backendCommitSha ?? item.meta?.backend_commit_sha ?? "unknown";
    const frontendCommit = process.env.NEXT_PUBLIC_GIT_SHA ?? item.meta?.frontend_commit_sha ?? "unknown";
    return <section key={resource} data-testid={`workflow-resource-${resource}`} className={`ct-v2-resource-state is-${item.state}`}>
      <strong>{resource === "capabilities" ? "工作流能力" : resource === "providers" ? "执行器能力" : "节点库"}</strong>
      {item.state === "ready" && <><span>已就绪</span><small>Backend: {backendCommit} · Frontend: {frontendCommit}</small></>}
      {item.state === "loading" && <span>正在载入</span>}
      {item.state === "failed" && <><span>暂时不可用</span><small>Endpoint: {endpoint} · HTTP {status} · Backend: {backendCommit} · Frontend: {frontendCommit}</small><button type="button" onClick={() => void onRetry(resource)}>重试</button></>}
    </section>;
  })}</div>;
}

function issueTitle(code: string): string {
  return {
    multiple_edges_to_single_input: "输入重复绑定",
    port_type_mismatch: "端口类型不匹配",
    required_input_unbound: "必填输入未连接",
    required_artifacts_output_mismatch: "产物契约不一致",
    agent_goal_missing: "缺少分析目标",
    provider_unknown: "执行器不可用",
    mcp_incompatible: "MCP 不兼容",
    skill_unknown: "Skill 不可用",
    graph_cycle: "工作流存在循环",
    orphan_node: "节点尚未连接",
    unsafe_artifact: "文件名不安全",
  }[code] ?? "工作流配置问题";
}

function issueMessage(code: string, message: string): string {
  if (/[㐀-鿿]/.test(message)) return message;
  return {
    multiple_edges_to_single_input: "该输入已绑定，请删除原连线后再连接。",
    port_type_mismatch: "来源数据类型与目标端口类型不一致。",
    required_input_unbound: "必填输入端口尚未连接来源节点。",
    required_artifacts_output_mismatch: "Agent 必须生成的文件应与已连接输出节点的文件保持一致。",
    agent_goal_missing: "请填写该节点需要完成的分析目标。",
    provider_unknown: "所选执行器不存在或当前不可用。",
    mcp_incompatible: "所选执行器不支持当前 MCP 配置。",
    skill_unknown: "所选 Skill 当前不可用。",
    graph_cycle: "节点连线形成循环，请删除其中一条依赖。",
    orphan_node: "该节点未连接到工作流，请连线或删除节点。",
    unsafe_artifact: "输出文件名包含不安全路径，请使用工作目录内的相对文件名。",
  }[code] ?? "工作流配置未通过验证，请检查对应节点。";
}

function PlanPreview({ plan, graph }: { plan: CompiledWorkflowPlan | null; graph: AuthoringGraph }) {
  if (!plan) return <p className="ct-v2-bottom-empty">发布或试运行完成编译后，这里会展示后端实际执行顺序。</p>;
  const planNodeById = new Map(plan.nodes.map((node) => [node.node_id, node]));
  const graphLabelById = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const visibleLabel = (planNodeId: string) => {
    const planNode = planNodeById.get(planNodeId);
    return graphLabelById.get(planNode?.graph_node_id ?? "") ?? "未命名节点";
  };
  return <div className="ct-v2-plan-preview">{plan.topological_order.map((nodeId, index) => {
    const node = planNodeById.get(nodeId);
    const dependencies = node?.depends_on.map(visibleLabel) ?? [];
    return <div key={nodeId}><span>{index + 1}</span><strong>{visibleLabel(nodeId)}</strong><small>{node?.provider || node?.type}</small><em>{dependencies.length ? `依赖 ${dependencies.join("、")}` : "无前置依赖"}</em></div>;
  })}</div>;
}

function WorkbenchLoading({ label }: { label: string }) {
  return <div className="ct-v2-page-loading"><Loader2 className="animate-spin" size={20} /><span>{label}</span></div>;
}

function WorkbenchError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="ct-v2-empty-state is-error"><AlertTriangle size={28} /><h1>无法打开工作流</h1><p>{message}</p><button type="button" onClick={onRetry}>重试</button></div>;
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FlaskConical,
  Loader2,
  Play,
  Save,
  Send,
} from "lucide-react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type {
  AuthoringGraphV2,
  CompiledWorkflowPlan,
  WorkflowCapabilities,
  WorkflowGraphNode,
  WorkflowProviderCapability,
  WorkflowValidationResult,
  WorkflowVersion,
} from "@/lib/types/workflow";
import { NodeInspector, type InputPortMutation } from "./node-inspector";
import { WorkflowCanvas } from "./workflow-canvas";
import { TrialRunPanel } from "../trial-run-panel";
import {
  createEditorState,
  workflowEditorReducer,
} from "../state/workflow-editor-reducer";

type BottomTab = "problems" | "plan" | "trial";

export function WorkflowDesigner({ workflowId }: { workflowId: string }) {
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [capabilities, setCapabilities] = useState<WorkflowCapabilities | null>(null);
  const [providers, setProviders] = useState<WorkflowProviderCapability[]>([]);
  const [needsDraft, setNeedsDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [detail, capabilityResult, providerResult] = await Promise.all([
        workflowsApi.get(workflowId),
        workflowsApi.capabilities(),
        workflowsApi.providers(),
      ]);
      setCapabilities(capabilityResult);
      setProviders(providerResult.providers);
      const draftId = detail.v2?.current_draft_version_id;
      if (!draftId) {
        setNeedsDraft(detail.v2?.published_version_id ?? "published");
        setVersion(null);
        return;
      }
      const loaded = await workflowsApi.version(workflowId, draftId);
      setVersion(loaded);
      setNeedsDraft(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "工作流加载失败");
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <WorkbenchLoading label="正在载入工作流草稿" />;
  if (error) return <WorkbenchError message={error} onRetry={load} />;
  if (needsDraft) {
    return (
      <div className="ct-v2-empty-state">
        <FlaskConical size={28} />
        <h1>已发布版本不可直接修改</h1>
        <p>创建一个基于当前发布版本的新草稿，再进入设计器。</p>
        <div>
          <Link href="/workflows">返回工作流库</Link>
          <button
            type="button"
            onClick={async () => {
              setLoading(true);
              try {
                await workflowsApi.createDraft(workflowId, needsDraft === "published" ? undefined : needsDraft);
                await load();
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : "创建草稿失败");
              }
            }}
          >
            创建新草稿
          </button>
        </div>
      </div>
    );
  }
  if (!version || version.authoring_graph.schema_version !== 2) {
    return <WorkbenchError message="该版本是只读旧工作流，请先复制为 V2 草稿。" onRetry={load} />;
  }
  return (
    <LoadedWorkflowDesigner
      key={version.version_id}
      workflowId={workflowId}
      version={version as WorkflowVersion & { authoring_graph: AuthoringGraphV2 }}
      capabilities={capabilities}
      providers={providers}
    />
  );
}

function LoadedWorkflowDesigner({
  workflowId,
  version,
  capabilities,
  providers,
}: {
  workflowId: string;
  version: WorkflowVersion & { authoring_graph: AuthoringGraphV2 };
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
}) {
  const router = useRouter();
  const [state, dispatch] = useReducer(
    workflowEditorReducer,
    version.authoring_graph,
    createEditorState,
  );
  const latestRevision = useRef(state.revision);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "failed">("saved");
  const [validation, setValidation] = useState<WorkflowValidationResult | null>(version.validation);
  const [plan, setPlan] = useState<CompiledWorkflowPlan | null>(version.compiled_plan);
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [action, setAction] = useState<"validate" | "compile" | "publish" | null>(null);
  const [message, setMessage] = useState("");

  latestRevision.current = state.revision;
  useEffect(() => {
    if (state.revision === state.savedRevision) return;
    setSaveState("saving");
    const revision = state.revision;
    const timer = window.setTimeout(async () => {
      try {
        await workflowsApi.updateDraft(workflowId, version.version_id, state.present);
        dispatch({ type: "mark-saved", revision });
        if (latestRevision.current === revision) setSaveState("saved");
      } catch {
        setSaveState("failed");
      }
    }, 800);
    return () => window.clearTimeout(timer);
  }, [state.present, state.revision, state.savedRevision, version.version_id, workflowId]);

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (state.revision === state.savedRevision) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [state.revision, state.savedRevision]);

  const selectedNode = state.present.nodes.find((node) => node.id === state.selectedNodeId) ?? null;
  const updateNode = (node: WorkflowGraphNode, portMutation?: InputPortMutation) => {
    const edges = portMutation
      ? state.present.edges.flatMap((item) => {
          if (item.target.node_id !== node.id || item.target.port_id !== portMutation.oldId) return [item];
          if (portMutation.kind === "delete") return [];
          return [{ ...item, target: { ...item.target, port_id: portMutation.newId } }];
        })
      : state.present.edges;
    dispatch(portMutation
      ? { type: "update-node-with-edges", node, edges }
      : { type: "update-node", node });
    if (node.kind !== "output") return;
    const sourceId = String(node.config.source_node_id ?? "");
    const source = state.present.nodes.find((item) => item.id === sourceId && item.kind === "agent");
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

  const saveNow = async () => {
    setSaveState("saving");
    const revision = state.revision;
    try {
      await workflowsApi.updateDraft(workflowId, version.version_id, state.present);
      dispatch({ type: "mark-saved", revision });
      setSaveState("saved");
    } catch (cause) {
      setSaveState("failed");
      throw cause;
    }
  };

  const runAction = async (kind: "validate" | "compile" | "publish") => {
    setAction(kind);
    setMessage("");
    try {
      await saveNow();
      if (kind === "validate") {
        const result = await workflowsApi.validate(workflowId, version.version_id);
        setValidation(result);
        setBottomTab("problems");
        setMessage(result.valid ? "验证通过" : `发现 ${result.errors.length} 个阻断问题`);
      } else if (kind === "compile") {
        const result = await workflowsApi.compile(workflowId, version.version_id);
        setValidation(result.validation_result);
        setPlan(result.compiled_plan);
        setBottomTab("plan");
        setMessage("执行计划已生成");
      } else {
        const published = await workflowsApi.publish(workflowId, version.version_id);
        setMessage(`V${published.version_number} 已发布`);
        router.push(`/workflows/${encodeURIComponent(workflowId)}/versions`);
      }
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
            <p>工作流设计 / <span>{workflowId}</span></p>
            <h1>{state.present.name}</h1>
          </div>
          <span className="ct-v2-version-chip">草稿 V{version.version_number}</span>
          <span className={`ct-v2-save-state is-${saveState}`}>
            {saveState === "saving" ? <Loader2 size={13} className="animate-spin" /> : saveState === "saved" ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            {saveState === "saving" ? "保存中" : saveState === "saved" ? "已保存" : "保存失败"}
          </span>
        </div>
        <div className="ct-v2-designer-actions">
          <button type="button" onClick={() => void saveNow()} title="保存草稿"><Save size={15} />保存</button>
          <button type="button" onClick={() => void runAction("validate")} disabled={Boolean(action)}>
            {action === "validate" ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}验证
          </button>
          <button type="button" onClick={() => void runAction("compile")} disabled={Boolean(action)}><Play size={15} />编译</button>
          <button type="button" className="is-primary" onClick={() => void runAction("publish")} disabled={Boolean(action) || Boolean(validation && !validation.valid)}><Send size={15} />发布</button>
        </div>
      </header>

      {message && <div className="ct-v2-inline-message" role="status">{message}</div>}

      <div className={`ct-v2-designer-grid ${selectedNode ? "has-inspector" : ""}`}>
        <WorkflowCanvas state={state} dispatch={dispatch} />
        {selectedNode && (
          <NodeInspector
            node={selectedNode}
            capabilities={capabilities}
            providers={providers}
            onChange={updateNode}
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
          {bottomTab === "plan" && <PlanPreview plan={plan} />}
          {bottomTab === "trial" && <TrialRunPanel workflowId={workflowId} versionId={version.version_id} graph={state.present} onBeforeRun={saveNow} />}
        </div>
      </section>
    </div>
  );
}

function ProblemList({ validation, onFocus }: { validation: WorkflowValidationResult | null; onFocus: (nodeId: string) => void }) {
  if (!validation) return <p className="ct-v2-bottom-empty">点击“验证”检查端口、执行器、MCP、Skills 和输出契约。</p>;
  if (!validation.errors.length && !validation.warnings.length) return <p className="ct-v2-bottom-empty is-success"><CheckCircle2 size={16} />没有阻断问题，可以编译并发布。</p>;
  return <div className="ct-v2-problem-list">{[...validation.errors, ...validation.warnings].map((item, index) => (
    <button key={`${item.code}-${index}`} type="button" onClick={() => item.node_id && onFocus(item.node_id)}>
      <AlertTriangle size={14} /><span><strong>{issueTitle(item.code)}</strong>{issueMessage(item.code, item.message)}</span><em>{item.node_id ?? "工作流"}</em>
    </button>
  ))}</div>;
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

function PlanPreview({ plan }: { plan: CompiledWorkflowPlan | null }) {
  if (!plan) return <p className="ct-v2-bottom-empty">验证通过后点击“编译”，这里会展示后端实际执行顺序。</p>;
  return <div className="ct-v2-plan-preview">{plan.topological_order.map((nodeId, index) => {
    const node = plan.nodes.find((item) => item.node_id === nodeId);
    return <div key={nodeId}><span>{index + 1}</span><strong>{nodeId}</strong><small>{node?.provider || node?.type}</small><em>{node?.depends_on.length ? `依赖 ${node.depends_on.join("、")}` : "无前置依赖"}</em></div>;
  })}</div>;
}

function WorkbenchLoading({ label }: { label: string }) {
  return <div className="ct-v2-page-loading"><Loader2 className="animate-spin" size={20} /><span>{label}</span></div>;
}

function WorkbenchError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="ct-v2-empty-state is-error"><AlertTriangle size={28} /><h1>无法打开工作流</h1><p>{message}</p><button type="button" onClick={onRetry}>重试</button></div>;
}

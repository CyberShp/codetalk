"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, Copy, Loader2, Plus, Send } from "lucide-react";
import { useCallback, useEffect, useReducer, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { AuthoringGraphV2, CompiledWorkflowPlan, WorkflowCapabilities, WorkflowGraphNode, WorkflowNodeKind, WorkflowNodeRegistry, WorkflowProviderCapability, WorkflowValidationResult } from "@/lib/types/workflow";
import { createNodeFromRegistry, createStarterGraph, safeWorkflowId, sanitizeWorkflowIdDraft } from "../workflow-graph";
import { createEditorState, workflowEditorReducer } from "../state/workflow-editor-reducer";
import { WorkflowCanvas } from "../designer/workflow-canvas";
import { NodeInspector, type PortMutation } from "../designer/node-inspector";
import { TrialRunPanel } from "../trial-run-panel";

const steps = ["基本信息", "定义输入", "执行节点", "定义输出", "编排流程", "验证发布"];

export function WorkflowWizard() {
  const router = useRouter();
  const params = useSearchParams();
  const requestedStep = Math.min(6, Math.max(1, Number(params.get("step") || 1)));
  const workflowParam = params.get("workflow") || "";
  const versionParam = params.get("version") || "";
  const [step, setStep] = useState(requestedStep);
  const [name, setName] = useState("");
  const [workflowId, setWorkflowId] = useState(workflowParam);
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState<"source" | "blank">("source");
  const [versionId, setVersionId] = useState(versionParam);
  const [editor, dispatch] = useReducer(workflowEditorReducer, createStarterGraph("new-workflow", "新工作流"), createEditorState);
  const [capabilities, setCapabilities] = useState<WorkflowCapabilities | null>(null);
  const [providers, setProviders] = useState<WorkflowProviderCapability[]>([]);
  const [nodeRegistry, setNodeRegistry] = useState<WorkflowNodeRegistry | null>(null);
  const [validation, setValidation] = useState<WorkflowValidationResult | null>(null);
  const [plan, setPlan] = useState<CompiledWorkflowPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    void Promise.all([workflowsApi.capabilities(), workflowsApi.providers(), workflowsApi.nodeRegistry(2)]).then(([caps, providerResult, registry]) => { setCapabilities(caps); setProviders(providerResult.providers); setNodeRegistry(registry); });
  }, []);

  useEffect(() => {
    if (!workflowParam || !versionParam) return;
    setBusy(true);
    void workflowsApi.version(workflowParam, versionParam).then((version) => {
      if (version.authoring_graph.schema_version !== 2) throw new Error("该草稿不是可编辑的 V2 工作流");
      const graph = version.authoring_graph as AuthoringGraphV2;
      setName(graph.name); setWorkflowId(graph.workflow_id); setDescription(graph.description); setVersionId(version.version_id);
      dispatch({ type: "replace", graph, markSaved: true });
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "草稿恢复失败")).finally(() => setBusy(false));
  }, [versionParam, workflowParam]);

  useEffect(() => {
    if (!workflowId || !versionId || editor.revision === editor.savedRevision) return;
    const revision = editor.revision;
    const graph = { ...editor.present, workflow_id: workflowId, name, description };
    const timer = window.setTimeout(() => {
      void workflowsApi.updateDraft(workflowId, versionId, graph)
        .then(() => dispatch({ type: "mark-saved", revision }))
        .catch((cause) => setError(cause instanceof Error ? cause.message : "画布草稿自动保存失败"));
    }, 650);
    return () => window.clearTimeout(timer);
  }, [description, editor.present, editor.revision, editor.savedRevision, name, versionId, workflowId]);

  const save = useCallback(async () => {
    if (!workflowId || !versionId) return;
    const graph = { ...editor.present, workflow_id: workflowId, name, description };
    await workflowsApi.updateHeader(workflowId, { name, description });
    await workflowsApi.updateDraft(workflowId, versionId, graph);
    dispatch({ type: "replace", graph, markSaved: true });
  }, [description, editor.present, name, versionId, workflowId]);

  const go = async (next: number) => {
    setError(""); setBusy(true);
    try {
      if (!workflowId || !versionId) {
        if (!name.trim()) throw new Error("请填写工作流名称");
        const id = safeWorkflowId(workflowId || name);
        const graph = template === "source" ? createStarterGraph(id, name.trim(), description.trim()) : emptyGraph(id, name.trim(), description.trim());
        const header = await workflowsApi.create({ id, name: name.trim(), description: description.trim(), authoring_graph: graph });
        if (!header.current_draft_version_id) throw new Error("服务器未返回草稿版本");
        setWorkflowId(id); setVersionId(header.current_draft_version_id); dispatch({ type: "replace", graph, markSaved: true });
        setStep(next);
        replaceLegacyLocation(legacyEditorUrl(id, header.current_draft_version_id, next));
        return;
      }
      validateWizardStep(step, editor.present as AuthoringGraphV2);
      await save(); setStep(next);
      replaceLegacyLocation(legacyEditorUrl(workflowId, versionId, next));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  };

  const selected = editor.present.nodes.find((node) => node.id === editor.selectedNodeId) ?? null;
  const inputNodes = editor.present.nodes.filter((node) => node.kind === "input");
  const agentNodes = editor.present.nodes.filter((node) => node.kind === "agent");
  const outputNodes = editor.present.nodes.filter((node) => node.kind === "output");
  const update = (node: WorkflowGraphNode, mutation?: PortMutation) => {
    if (!mutation) {
      dispatch({ type: "update-node", node });
      return;
    }
    const edges = editor.present.edges.flatMap((edge) => {
      const endpoint = mutation.direction === "input" ? edge.target : edge.source;
      if (endpoint.node_id !== node.id || endpoint.port_id !== mutation.oldId) return [edge];
      if (mutation.kind === "delete") return [];
      return [mutation.direction === "input"
        ? { ...edge, target: { ...edge.target, port_id: mutation.newId } }
        : { ...edge, source: { ...edge.source, port_id: mutation.newId } }];
    });
    dispatch({ type: "update-node-with-edges", node, edges });
  };

  return <main className="ct-v2-wizard" data-testid="workflow-wizard-ready" data-hydrated={hydrated ? "true" : "false"}>
    <header className="ct-v2-wizard-header"><Link href="/workflows"><ArrowLeft size={16} />工作流库</Link><div><p>{workflowParam ? "V2 兼容编辑" : "兼容模式新建"}</p><h1>{name || "未命名工作流"}</h1></div>{workflowParam && versionParam ? <button type="button" onClick={() => router.push(`/workflows/${encodeURIComponent(workflowParam)}/versions/${encodeURIComponent(versionParam)}`)} disabled={busy}><Copy size={14} />查看 V3 迁移预览</button> : <span>{workflowId ? `草稿 · ${workflowId}` : "尚未创建草稿"}</span>}</header>
    <ol className="ct-v2-stepper">{steps.map((label, index) => <li key={label} className={index + 1 === step ? "is-active" : index + 1 < step ? "is-done" : ""}><span>{index + 1 < step ? <Check size={13} /> : index + 1}</span><strong>{label}</strong></li>)}</ol>
    <section className={`ct-v2-wizard-body step-${step}`}>
      {step === 1 && <BasicStep name={name} id={workflowId} description={description} template={template} onName={(value) => { setName(value); setWorkflowId((current) => current || safeWorkflowId(value)); }} onId={setWorkflowId} onDescription={setDescription} onTemplate={setTemplate} />}
      {step === 2 && <RegistryNodeStep title="定义输入" copy="这些名称会直接成为创建任务和试运行时的表单提示。" kind="input" addLabel="添加输入" nodes={inputNodes} registry={nodeRegistry} capabilities={capabilities} providers={providers} dispatch={dispatch} onUpdate={update} />}
      {step === 3 && <RegistryNodeStep title="定义执行节点" copy="每个节点只描述一个清晰目标；Provider、Skills 和 MCP 会冻结进发布版本。" kind="agent" addLabel="添加 Agent" nodes={agentNodes} registry={nodeRegistry} capabilities={capabilities} providers={providers} dispatch={dispatch} onUpdate={update} />}
      {step === 4 && <RegistryNodeStep title="定义输出" copy="输出契约既提示用户交付什么，也约束执行器必须写出什么文件。" kind="output" addLabel="添加输出" nodes={outputNodes} registry={nodeRegistry} capabilities={capabilities} providers={providers} dispatch={dispatch} onUpdate={update} />}
      {step === 5 && (nodeRegistry ? <div className={`ct-v2-wizard-canvas ${selected ? "has-inspector" : ""}`}><WorkflowCanvas state={editor} dispatch={dispatch} registry={nodeRegistry} />{selected && <NodeInspector node={selected} capabilities={capabilities} providers={providers} registry={nodeRegistry} onChange={update} onClose={() => dispatch({ type: "select-node", nodeId: null })} />}</div> : <p className="ct-v2-bottom-empty">正在载入节点库…</p>)}
      {step === 6 && <ReviewStep workflowId={workflowId} versionId={versionId} graph={editor.present as AuthoringGraphV2} validation={validation} plan={plan} busy={busy} onBeforeRun={save} onValidate={async () => { setBusy(true); try { await save(); const result = await workflowsApi.validate(workflowId, versionId); setValidation(result); } finally { setBusy(false); } }} onCompile={async () => { setBusy(true); try { await save(); const result = await workflowsApi.compile(workflowId, versionId); setValidation(result.validation_result); setPlan(result.compiled_plan); } finally { setBusy(false); } }} onPublish={async () => { setBusy(true); try { await save(); await workflowsApi.publish(workflowId, versionId); router.push(`/workflows/${encodeURIComponent(workflowId)}/versions`); } finally { setBusy(false); } }} />}
    </section>
    {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
    <footer className="ct-v2-wizard-footer"><button type="button" disabled={step === 1 || busy} onClick={() => void go(step - 1)}><ArrowLeft size={15} />上一步</button><span>第 {step} / 6 步</span>{step < 6 ? <button className="ct-v2-primary-button" type="button" disabled={busy} onClick={() => void go(step + 1)}>{busy ? <Loader2 className="animate-spin" size={15} /> : null}保存并继续<ArrowRight size={15} /></button> : <Link href={`/workflows/${encodeURIComponent(workflowId)}`}>进入完整设计器</Link>}</footer>
  </main>;
}

function BasicStep({ name, id, description, template, onName, onId, onDescription, onTemplate }: { name: string; id: string; description: string; template: "source" | "blank"; onName: (v: string) => void; onId: (v: string) => void; onDescription: (v: string) => void; onTemplate: (v: "source" | "blank") => void }) { return <div className="ct-v2-form-step"><div className="ct-v2-step-copy"><h2>基本信息与模板</h2><p>模板只用于初始化草稿，创建后所有修改都直接作用于同一张画布。</p></div><div className="ct-v2-form-grid"><label><span>工作流名称 *</span><input autoFocus value={name} onChange={(event) => onName(event.target.value)} placeholder="例如：源码流程与 SFMEA 分析" /></label><label><span>工作流 ID</span><input value={id} onChange={(event) => onId(sanitizeWorkflowIdDraft(event.target.value))} placeholder="source-sfmea" /><small>创建后不可修改</small></label><label className="is-wide"><span>描述</span><textarea value={description} onChange={(event) => onDescription(event.target.value)} rows={3} placeholder="说明适用场景和预期交付" /></label></div><fieldset className="ct-v2-template-choice"><legend>初始化方式</legend><label className={template === "source" ? "is-selected" : ""}><input type="radio" checked={template === "source"} onChange={() => onTemplate("source")} /><strong>源码分析基础模板</strong><span>工作空间 → Agent → Markdown 报告</span></label><label className={template === "blank" ? "is-selected" : ""}><input type="radio" checked={template === "blank"} onChange={() => onTemplate("blank")} /><strong>空白画布</strong><span>从输入、执行和输出契约开始搭建</span></label></fieldset></div>; }

function RegistryNodeStep({ title, copy, kind, addLabel, nodes, registry, capabilities, providers, dispatch, onUpdate }: { title: string; copy: string; kind: WorkflowNodeKind; addLabel: string; nodes: WorkflowGraphNode[]; registry: WorkflowNodeRegistry | null; capabilities: WorkflowCapabilities | null; providers: WorkflowProviderCapability[]; dispatch: React.Dispatch<Parameters<typeof workflowEditorReducer>[1]>; onUpdate: (node: WorkflowGraphNode, mutation?: PortMutation) => void }) {
  const [selectedId, setSelectedId] = useState(nodes[0]?.id ?? "");
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0] ?? null;
  const add = () => {
    const index = nodes.length;
    const x = kind === "input" ? 80 : kind === "agent" ? 380 : 720;
    addRegistryNode(registry, kind, x, 140 + index * 160, dispatch, setSelectedId);
  };
  if (!registry) return <p className="ct-v2-bottom-empty">正在载入节点库…</p>;
  return <div className="ct-v2-wizard-registry-step"><div className="ct-v2-step-heading"><div><h2>{title}</h2><p>{copy}</p></div><button type="button" onClick={add}><Plus size={15} />{addLabel}</button></div><div className="ct-v2-wizard-registry-body"><nav aria-label={`${title}节点列表`}>{nodes.map((node) => <button type="button" key={node.id} className={node.id === selected?.id ? "is-selected" : ""} onClick={() => setSelectedId(node.id)}><strong>{node.label}</strong><small>{node.id}</small></button>)}{!nodes.length && <p>尚未添加节点。</p>}</nav>{selected && <NodeInspector node={selected} capabilities={capabilities} providers={providers} registry={registry} onChange={onUpdate} onClose={() => setSelectedId("")} />}</div></div>;
}

function ReviewStep({ workflowId, versionId, graph, validation, plan, busy, onBeforeRun, onValidate, onCompile, onPublish }: { workflowId: string; versionId: string; graph: AuthoringGraphV2; validation: WorkflowValidationResult | null; plan: CompiledWorkflowPlan | null; busy: boolean; onBeforeRun: () => Promise<void>; onValidate: () => Promise<void>; onCompile: () => Promise<void>; onPublish: () => Promise<void> }) { return <div className="ct-v2-review-step"><div className="ct-v2-review-actions"><button type="button" onClick={() => void onValidate()} disabled={busy}><Check size={15} />验证</button><button type="button" onClick={() => void onCompile()} disabled={busy}><ArrowRight size={15} />编译计划</button><button className="ct-v2-primary-button" type="button" onClick={() => void onPublish()} disabled={busy || !validation?.valid || !plan}><Send size={15} />发布工作流</button></div><div className="ct-v2-review-grid"><section><h2>验证结果</h2>{!validation ? <p>先验证端口、执行器、MCP、Skills 和输出契约。</p> : validation.valid ? <p className="is-success"><Check size={15} />验证通过</p> : <ul>{validation.errors.map((item) => <li key={`${item.code}-${item.node_id}`}>{item.message}</li>)}</ul>}</section><section><h2>真实执行顺序</h2>{plan ? <ol>{plan.topological_order.map((nodeId) => { const node = plan.nodes.find((item) => item.node_id === nodeId); return <li key={nodeId}><strong>{nodeId}</strong><span>{node?.provider || node?.type}</span><small>{node?.depends_on.length ? `依赖：${node.depends_on.join("、")}` : "无前置依赖"}</small></li>; })}</ol> : <p>验证通过后编译，后端实际计划会显示在这里。</p>}</section></div><TrialRunPanel workflowId={workflowId} versionId={versionId} graph={graph} onBeforeRun={onBeforeRun} /></div>; }

function emptyGraph(workflowId: string, name: string, description: string): AuthoringGraphV2 { return { schema_version: 2, workflow_id: workflowId, name, description, nodes: [], edges: [], settings: { stop_on_error: true, max_parallelism: 1 } }; }
function legacyEditorUrl(workflowId: string, versionId: string, step: number) { return `/workflows/${encodeURIComponent(workflowId)}/legacy?workflow=${encodeURIComponent(workflowId)}&version=${encodeURIComponent(versionId)}&step=${step}`; }
function replaceLegacyLocation(url: string) { window.history.replaceState(window.history.state, "", url); }
function addRegistryNode(registry: WorkflowNodeRegistry | null, kind: WorkflowNodeKind, x: number, y: number, dispatch: React.Dispatch<Parameters<typeof workflowEditorReducer>[1]>, onAdded?: (nodeId: string) => void) { const definition = registry?.nodes.find((item) => item.kind === kind); if (!definition) return; const node = createNodeFromRegistry(definition, x, y); dispatch({ type: "add-node", node }); onAdded?.(node.id); }
function validateWizardStep(step: number, graph: AuthoringGraphV2) { if (step === 2) { const ids = graph.nodes.filter((node) => node.kind === "input").map((node) => String(node.config.contract_id || "")); if (ids.some((id) => !id) || new Set(ids).size !== ids.length) throw new Error("输入 ID 不能为空或重复"); } if (step === 3) { const agents = graph.nodes.filter((node) => node.kind === "agent"); if (!agents.length || agents.some((node) => !String(node.config.goal || "").trim())) throw new Error("至少添加一个目标完整的执行节点"); } if (step === 4 && !graph.nodes.some((node) => node.kind === "output")) throw new Error("至少定义一个输出"); }

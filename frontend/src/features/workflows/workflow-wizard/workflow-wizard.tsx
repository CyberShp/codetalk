"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, Loader2, Plus, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { AuthoringGraphV2, CompiledWorkflowPlan, WorkflowCapabilities, WorkflowGraphNode, WorkflowProviderCapability, WorkflowValidationResult } from "@/lib/types/workflow";
import { createNode, createStarterGraph, safeWorkflowId, sanitizeWorkflowIdDraft } from "../workflow-graph";
import { createEditorState, workflowEditorReducer } from "../state/workflow-editor-reducer";
import { WorkflowCanvas } from "../designer/workflow-canvas";
import { NodeInspector } from "../designer/node-inspector";
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
  const [validation, setValidation] = useState<WorkflowValidationResult | null>(null);
  const [plan, setPlan] = useState<CompiledWorkflowPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void Promise.all([workflowsApi.capabilities(), workflowsApi.providers()]).then(([caps, providerResult]) => { setCapabilities(caps); setProviders(providerResult.providers); });
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
        router.replace(`/workflows/new?workflow=${encodeURIComponent(id)}&version=${encodeURIComponent(header.current_draft_version_id)}&step=${next}`);
        return;
      }
      validateWizardStep(step, editor.present);
      await save(); setStep(next);
      router.replace(`/workflows/new?workflow=${encodeURIComponent(workflowId)}&version=${encodeURIComponent(versionId)}&step=${next}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  };

  const selected = editor.present.nodes.find((node) => node.id === editor.selectedNodeId) ?? null;
  const inputNodes = editor.present.nodes.filter((node) => node.kind === "input");
  const agentNodes = editor.present.nodes.filter((node) => node.kind === "agent");
  const outputNodes = editor.present.nodes.filter((node) => node.kind === "output");
  const update = (node: WorkflowGraphNode) => dispatch({ type: "update-node", node });

  return <main className="ct-v2-wizard">
    <header className="ct-v2-wizard-header"><Link href="/workflows"><ArrowLeft size={16} />工作流库</Link><div><p>新建工作流</p><h1>{name || "未命名工作流"}</h1></div><span>{workflowId ? `草稿 · ${workflowId}` : "尚未创建草稿"}</span></header>
    <ol className="ct-v2-stepper">{steps.map((label, index) => <li key={label} className={index + 1 === step ? "is-active" : index + 1 < step ? "is-done" : ""}><span>{index + 1 < step ? <Check size={13} /> : index + 1}</span><strong>{label}</strong></li>)}</ol>
    <section className={`ct-v2-wizard-body step-${step}`}>
      {step === 1 && <BasicStep name={name} id={workflowId} description={description} template={template} onName={(value) => { setName(value); if (!workflowId) setWorkflowId(safeWorkflowId(value)); }} onId={setWorkflowId} onDescription={setDescription} onTemplate={setTemplate} />}
      {step === 2 && <InputStep nodes={inputNodes} onUpdate={update} onAdd={() => dispatch({ type: "add-node", node: createNode("input", 80, 140 + inputNodes.length * 130) })} onRemove={(id) => dispatch({ type: "remove-node", nodeId: id })} />}
      {step === 3 && <AgentStep nodes={agentNodes} providers={providers} capabilities={capabilities} onUpdate={update} onAdd={() => dispatch({ type: "add-node", node: createNode("agent", 380, 140 + agentNodes.length * 130) })} onRemove={(id) => dispatch({ type: "remove-node", nodeId: id })} />}
      {step === 4 && <OutputStep nodes={outputNodes} agents={agentNodes} onUpdate={update} onAdd={() => dispatch({ type: "add-node", node: createNode("output", 720, 140 + outputNodes.length * 130) })} onRemove={(id) => dispatch({ type: "remove-node", nodeId: id })} />}
      {step === 5 && <div className={`ct-v2-wizard-canvas ${selected ? "has-inspector" : ""}`}><WorkflowCanvas state={editor} dispatch={dispatch} />{selected && <NodeInspector node={selected} capabilities={capabilities} providers={providers} onChange={update} onClose={() => dispatch({ type: "select-node", nodeId: null })} />}</div>}
      {step === 6 && <ReviewStep workflowId={workflowId} versionId={versionId} graph={editor.present} validation={validation} plan={plan} busy={busy} onBeforeRun={save} onValidate={async () => { setBusy(true); try { await save(); const result = await workflowsApi.validate(workflowId, versionId); setValidation(result); } finally { setBusy(false); } }} onCompile={async () => { setBusy(true); try { await save(); const result = await workflowsApi.compile(workflowId, versionId); setValidation(result.validation_result); setPlan(result.compiled_plan); } finally { setBusy(false); } }} onPublish={async () => { setBusy(true); try { await save(); await workflowsApi.publish(workflowId, versionId); router.push(`/workflows/${encodeURIComponent(workflowId)}/versions`); } finally { setBusy(false); } }} />}
    </section>
    {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
    <footer className="ct-v2-wizard-footer"><button type="button" disabled={step === 1 || busy} onClick={() => void go(step - 1)}><ArrowLeft size={15} />上一步</button><span>第 {step} / 6 步</span>{step < 6 ? <button className="ct-v2-primary-button" type="button" disabled={busy} onClick={() => void go(step + 1)}>{busy ? <Loader2 className="animate-spin" size={15} /> : null}保存并继续<ArrowRight size={15} /></button> : <Link href={`/workflows/${encodeURIComponent(workflowId)}`}>进入完整设计器</Link>}</footer>
  </main>;
}

function BasicStep({ name, id, description, template, onName, onId, onDescription, onTemplate }: { name: string; id: string; description: string; template: "source" | "blank"; onName: (v: string) => void; onId: (v: string) => void; onDescription: (v: string) => void; onTemplate: (v: "source" | "blank") => void }) { return <div className="ct-v2-form-step"><div className="ct-v2-step-copy"><h2>基本信息与模板</h2><p>模板只用于初始化草稿，创建后所有修改都直接作用于同一张画布。</p></div><div className="ct-v2-form-grid"><label><span>工作流名称 *</span><input autoFocus value={name} onChange={(event) => onName(event.target.value)} placeholder="例如：源码流程与 SFMEA 分析" /></label><label><span>工作流 ID</span><input value={id} onChange={(event) => onId(sanitizeWorkflowIdDraft(event.target.value))} placeholder="source-sfmea" /><small>创建后不可修改</small></label><label className="is-wide"><span>描述</span><textarea value={description} onChange={(event) => onDescription(event.target.value)} rows={3} placeholder="说明适用场景和预期交付" /></label></div><fieldset className="ct-v2-template-choice"><legend>初始化方式</legend><label className={template === "source" ? "is-selected" : ""}><input type="radio" checked={template === "source"} onChange={() => onTemplate("source")} /><strong>源码分析基础模板</strong><span>工作空间 → Agent → Markdown 报告</span></label><label className={template === "blank" ? "is-selected" : ""}><input type="radio" checked={template === "blank"} onChange={() => onTemplate("blank")} /><strong>空白画布</strong><span>从输入、执行和输出契约开始搭建</span></label></fieldset></div>; }

function InputStep({ nodes, onUpdate, onAdd, onRemove }: NodeStepProps) { return <div className="ct-v2-form-step"><StepHeading title="定义输入" copy="这些名称会直接成为创建任务和试运行时的表单提示。" onAdd={onAdd} label="添加输入" />{nodes.map((node) => <article className="ct-v2-contract-row" key={node.id}><label><span>名称</span><input value={node.label} onChange={(event) => onUpdate({ ...node, label: event.target.value, config: { ...node.config, label: event.target.value } })} /></label><label><span>ID</span><input value={String(node.config.contract_id || node.id)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, contract_id: safeWorkflowId(event.target.value) } })} /></label><label><span>类型</span><select value={String(node.config.type || "text")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, type: event.target.value } })}>{["text", "long_text", "file", "file_set", "directory", "mr_link", "patch", "coverage_report"].map((item) => <option key={item}>{item}</option>)}</select></label><label><span>获取方式</span><select value={String(node.config.resolver || "manual")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, resolver: event.target.value as "manual" | "workspace" | "local" | "agent_mcp" } })}><option value="manual">用户填写</option><option value="workspace">工作空间</option><option value="local">本地文件</option></select></label><label className="is-wide"><span>填写说明</span><input value={String(node.config.role || "")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, role: event.target.value } })} /></label><label className="ct-v2-inline-check"><input type="checkbox" checked={Boolean(node.config.required)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, required: event.target.checked } })} />必填</label><button type="button" className="ct-v2-icon-danger" onClick={() => onRemove(node.id)} title="删除输入"><Trash2 size={15} /></button></article>)}{!nodes.length && <EmptyContracts text="尚未定义输入。添加后，任务向导会按这里的名称生成字段。" />}</div>; }

function AgentStep({ nodes, providers, capabilities, onUpdate, onAdd, onRemove }: NodeStepProps & { providers: WorkflowProviderCapability[]; capabilities: WorkflowCapabilities | null }) { const summary = useMemo(() => ({ provider: new Set(nodes.map((node) => node.config.provider).filter(Boolean)).size, skills: new Set(nodes.flatMap((node) => node.config.skill_ids || [])).size, mcp: new Set(nodes.flatMap((node) => node.config.mcp_profiles || [])).size }), [nodes]); return <div className="ct-v2-step-with-summary"><div className="ct-v2-form-step"><StepHeading title="定义执行节点" copy="每个节点只描述一个清晰目标；Provider、Skills 和 MCP 会冻结进发布版本。" onAdd={onAdd} label="添加 Agent" />{nodes.map((node) => <article className="ct-v2-agent-contract" key={node.id}><div className="ct-v2-agent-contract-head"><input aria-label="节点名称" value={node.label} onChange={(event) => onUpdate({ ...node, label: event.target.value, config: { ...node.config, label: event.target.value } })} /><button type="button" onClick={() => onRemove(node.id)} title="删除 Agent"><Trash2 size={15} /></button></div><label><span>节点目标 *</span><textarea rows={3} value={String(node.config.goal || "")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, goal: event.target.value } })} /></label><div className="ct-v2-form-grid"><label><span>执行器</span><select value={String(node.config.provider || "builtin-llm")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, provider: event.target.value, mcp_profiles: [] } })}>{providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name} · {provider.provider}{provider.status === "unavailable" ? "（不可用）" : ""}</option>)}</select></label><label><span>超时（秒）</span><input type="number" min={30} max={3600} value={Number(node.config.timeout_sec || 900)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, timeout_sec: Number(event.target.value) } })} /></label><label><span>失败策略</span><select value={String(node.config.failure_policy || "stop")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, failure_policy: event.target.value as "stop" | "continue_independent" } })}><option value="stop">停止工作流</option><option value="continue_independent">继续独立分支</option></select></label></div><CompactChecks title="Skills" options={(capabilities?.skill_catalog || []).map((item) => ({ id: item.id, label: item.label }))} selected={node.config.skill_ids || []} onChange={(selected) => onUpdate({ ...node, config: { ...node.config, skill_ids: selected } })} /><CompactChecks title="MCP" options={(providers.find((item) => item.provider === node.config.provider)?.capabilities?.mcp_profiles || []).map((id) => ({ id, label: id }))} selected={node.config.mcp_profiles || []} onChange={(selected) => onUpdate({ ...node, config: { ...node.config, mcp_profiles: selected } })} /></article>)}{!nodes.length && <EmptyContracts text="至少添加一个执行节点。内置模型和设置中的 Agent 都会在这里显示。" />}</div><aside className="ct-v2-step-summary"><h3>配置摘要</h3><dl><div><dt>Agent 节点</dt><dd>{nodes.length}</dd></div><div><dt>执行器</dt><dd>{summary.provider}</dd></div><div><dt>Skills</dt><dd>{summary.skills}</dd></div><div><dt>MCP</dt><dd>{summary.mcp}</dd></div></dl></aside></div>; }

function OutputStep({ nodes, agents, onUpdate, onAdd, onRemove }: NodeStepProps & { agents: WorkflowGraphNode[] }) { return <div className="ct-v2-form-step"><StepHeading title="定义输出" copy="输出契约既提示用户交付什么，也约束执行器必须写出什么文件。" onAdd={onAdd} label="添加输出" />{nodes.map((node) => <article className="ct-v2-contract-row" key={node.id}><label><span>输出名称</span><input value={node.label} onChange={(event) => onUpdate({ ...node, label: event.target.value, config: { ...node.config, label: event.target.value } })} /></label><label><span>输出 ID</span><input value={String(node.config.output_id || node.id)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, output_id: safeWorkflowId(event.target.value) } })} /></label><label><span>类型</span><select value={String(node.config.type || "markdown")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, type: event.target.value } })}><option value="markdown">Markdown</option><option value="json">JSON</option><option value="test_cases">测试用例</option></select></label><label><span>文件名</span><input value={String(node.config.artifact || "")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, artifact: event.target.value } })} /></label><label><span>来源节点</span><select value={String(node.config.source_node_id || "")} onChange={(event) => onUpdate({ ...node, config: { ...node.config, source_node_id: event.target.value } })}><option value="">由连线决定</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.label}</option>)}</select></label><label className="ct-v2-inline-check"><input type="checkbox" checked={Boolean(node.config.required)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, required: event.target.checked } })} />必需</label><label className="ct-v2-inline-check"><input type="checkbox" checked={Boolean(node.config.evidence_memory)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, evidence_memory: event.target.checked } })} />证据库</label><label className="ct-v2-inline-check"><input type="checkbox" checked={Boolean(node.config.semantic_import)} onChange={(event) => onUpdate({ ...node, config: { ...node.config, semantic_import: event.target.checked } })} />语义库</label><button type="button" className="ct-v2-icon-danger" onClick={() => onRemove(node.id)} title="删除输出"><Trash2 size={15} /></button></article>)}{!nodes.length && <EmptyContracts text="至少定义一个交付输出，执行器才知道要写出什么文件。" />}</div>; }

function ReviewStep({ workflowId, versionId, graph, validation, plan, busy, onBeforeRun, onValidate, onCompile, onPublish }: { workflowId: string; versionId: string; graph: AuthoringGraphV2; validation: WorkflowValidationResult | null; plan: CompiledWorkflowPlan | null; busy: boolean; onBeforeRun: () => Promise<void>; onValidate: () => Promise<void>; onCompile: () => Promise<void>; onPublish: () => Promise<void> }) { return <div className="ct-v2-review-step"><div className="ct-v2-review-actions"><button type="button" onClick={() => void onValidate()} disabled={busy}><Check size={15} />验证</button><button type="button" onClick={() => void onCompile()} disabled={busy}><ArrowRight size={15} />编译计划</button><button className="ct-v2-primary-button" type="button" onClick={() => void onPublish()} disabled={busy || !validation?.valid || !plan}><Send size={15} />发布工作流</button></div><div className="ct-v2-review-grid"><section><h2>验证结果</h2>{!validation ? <p>先验证端口、执行器、MCP、Skills 和输出契约。</p> : validation.valid ? <p className="is-success"><Check size={15} />验证通过</p> : <ul>{validation.errors.map((item) => <li key={`${item.code}-${item.node_id}`}>{item.message}</li>)}</ul>}</section><section><h2>真实执行顺序</h2>{plan ? <ol>{plan.topological_order.map((nodeId) => { const node = plan.nodes.find((item) => item.node_id === nodeId); return <li key={nodeId}><strong>{nodeId}</strong><span>{node?.provider || node?.type}</span><small>{node?.depends_on.length ? `依赖：${node.depends_on.join("、")}` : "无前置依赖"}</small></li>; })}</ol> : <p>验证通过后编译，后端实际计划会显示在这里。</p>}</section></div><TrialRunPanel workflowId={workflowId} versionId={versionId} graph={graph} onBeforeRun={onBeforeRun} /></div>; }

interface NodeStepProps { nodes: WorkflowGraphNode[]; onUpdate: (node: WorkflowGraphNode) => void; onAdd: () => void; onRemove: (id: string) => void; }
function StepHeading({ title, copy, onAdd, label }: { title: string; copy: string; onAdd: () => void; label: string }) { return <div className="ct-v2-step-heading"><div><h2>{title}</h2><p>{copy}</p></div><button type="button" onClick={onAdd}><Plus size={15} />{label}</button></div>; }
function EmptyContracts({ text }: { text: string }) { return <div className="ct-v2-contract-empty">{text}</div>; }
function CompactChecks({ title, options, selected, onChange }: { title: string; options: Array<{ id: string; label: string }>; selected: string[]; onChange: (items: string[]) => void }) { const [query, setQuery] = useState(""); const visible = options.filter((item) => `${item.label} ${item.id}`.toLowerCase().includes(query.toLowerCase())).slice(0, 16); return <fieldset className="ct-v2-compact-checks"><legend>{title}</legend>{options.length > 8 && <input aria-label={`搜索${title}`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索 ${title}`} />}<div>{visible.map((item) => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={() => onChange(selected.includes(item.id) ? selected.filter((id) => id !== item.id) : [...selected, item.id])} /><span>{item.label}</span></label>)}{!visible.length && <small>当前执行器没有可选项</small>}</div></fieldset>; }
function emptyGraph(workflowId: string, name: string, description: string): AuthoringGraphV2 { return { schema_version: 2, workflow_id: workflowId, name, description, nodes: [], edges: [], settings: { stop_on_error: true, max_parallelism: 1 } }; }
function validateWizardStep(step: number, graph: AuthoringGraphV2) { if (step === 2) { const ids = graph.nodes.filter((node) => node.kind === "input").map((node) => String(node.config.contract_id || "")); if (ids.some((id) => !id) || new Set(ids).size !== ids.length) throw new Error("输入 ID 不能为空或重复"); } if (step === 3) { const agents = graph.nodes.filter((node) => node.kind === "agent"); if (!agents.length || agents.some((node) => !String(node.config.goal || "").trim())) throw new Error("至少添加一个目标完整的执行节点"); } if (step === 4 && !graph.nodes.some((node) => node.kind === "output")) throw new Error("至少定义一个输出"); }

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkbenchTask } from "@/lib/types/task";
import type { WorkflowListItem, WorkflowProviderCapability, WorkflowSkillCapability, WorkflowVersion } from "@/lib/types/workflow";
import type { Workspace } from "@/lib/types";

const labels = ["选择工作流", "任务信息", "填写输入", "执行配置", "确认输出", "检查运行"];
type Definition = { inputs?: Array<Record<string, unknown>>; steps?: Array<Record<string, unknown>>; outputs?: Array<Record<string, unknown>> };

export function TaskWizard() {
  const router = useRouter();
  const params = useSearchParams();
  const taskParam = params.get("task") || "";
  const [step, setStep] = useState(Math.min(6, Math.max(1, Number(params.get("step") || 1))));
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [providers, setProviders] = useState<WorkflowProviderCapability[]>([]);
  const [skills, setSkills] = useState<WorkflowSkillCapability[]>([]);
  const [workflowId, setWorkflowId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [task, setTask] = useState<WorkbenchTask | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [tags, setTags] = useState("");
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [executionOverrides, setExecutionOverrides] = useState<Record<string, unknown>>({});
  const [outputOverrides, setOutputOverrides] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const definition = (version?.compiled_definition || task?.workflow_version?.compiled_definition || {}) as Definition;

  useEffect(() => { void Promise.all([workflowsApi.list(), api.workspaces.list(), workflowsApi.providers(), workflowsApi.capabilities()]).then(([workflowItems, workspaceItems, providerItems, capabilities]) => { setWorkflows(workflowItems.filter((item) => Boolean(item.v2?.published_version_id))); setWorkspaces(workspaceItems); setProviders(providerItems.providers); setSkills(capabilities.skill_catalog || []); }).catch((cause) => setError(cause instanceof Error ? cause.message : "向导数据加载失败")); }, []);
  useEffect(() => { if (!taskParam) return; setBusy(true); void workbenchTasksApi.get(taskParam).then((item) => { setTask(item); setWorkflowId(item.workflow_id); setVersionId(item.workflow_version_id); setName(item.name); setDescription(item.description); setWorkspaceId(item.workspace_id); setTags(item.tags.join(", ")); setInputs(item.input_values); setExecutionOverrides(item.execution_overrides); setOutputOverrides(item.output_overrides); }).catch((cause) => setError(cause instanceof Error ? cause.message : "任务草稿恢复失败")).finally(() => setBusy(false)); }, [taskParam]);
  useEffect(() => { if (!workflowId || !versionId) return; void workflowsApi.version(workflowId, versionId).then(setVersion).catch((cause) => setError(cause instanceof Error ? cause.message : "工作流版本加载失败")); }, [versionId, workflowId]);

  const selectWorkflow = (id: string) => { const item = workflows.find((candidate) => candidate.id === id); const published = item?.v2?.published_version_id || ""; setWorkflowId(id); setVersionId(published); setVersion(null); if (item) setName(`${item.name} · ${new Date().toLocaleDateString("zh-CN")}`); };
  const save = async (lifecycleStatus?: "draft" | "ready") => {
    if (!workflowId || !versionId || !workspaceId || !name.trim()) throw new Error("请完整填写任务名称、工作流和工作空间");
    const mutable = { name: name.trim(), description: description.trim(), lifecycle_status: lifecycleStatus || task?.lifecycle_status || "draft", input_values: inputs, execution_overrides: executionOverrides, output_overrides: outputOverrides, tags: tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean) };
    const saved = task
      ? await workbenchTasksApi.update(task.task_id, mutable)
      : await workbenchTasksApi.create({ ...mutable, workspace_id: workspaceId, workflow_id: workflowId, workflow_version_id: versionId });
    setTask(saved); return saved;
  };
  const go = async (next: number) => { setError(""); setBusy(true); try { validateStep(step, { workflowId, workspaceId, name, definition, inputs }); if (step >= 2) { const saved = await save("draft"); router.replace(`/tasks/new?task=${saved.task_id}&step=${next}`); } else router.replace(`/tasks/new?step=${next}`); setStep(next); } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); } finally { setBusy(false); } };
  const finish = async (mode: "draft" | "ready" | "run") => { setBusy(true); setError(""); try { const saved = await save("draft"); if (mode === "draft") { router.push(`/tasks/${saved.task_id}`); return; } await workbenchTasksApi.compile(saved.task_id); const ready = await workbenchTasksApi.update(saved.task_id, { lifecycle_status: "ready" }); if (mode === "ready") { router.push(`/tasks/${ready.task_id}`); return; } const attempt = await workbenchTasksApi.createRun(ready.task_id); await api.workbench.taskRuns.execute(attempt.task_run_id, 0, true); router.push(`/tasks/${ready.task_id}/runs/${attempt.task_run_id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务检查失败"); } finally { setBusy(false); } };

  return <main className="ct-v2-task-wizard"><header><Link href="/tasks"><ArrowLeft size={15} />任务中心</Link><div><span>新建任务</span><h1>{name || "未命名任务"}</h1></div><em>{task ? "草稿已保存" : "尚未创建草稿"}</em></header><ol>{labels.map((label, index) => <li key={label} className={step === index + 1 ? "is-active" : step > index + 1 ? "is-done" : ""}><span>{step > index + 1 ? <Check size={12} /> : index + 1}</span><strong>{label}</strong></li>)}</ol><section className="ct-v2-task-wizard-body">
    {step === 1 && <WorkflowChoice items={workflows} value={workflowId} onChange={selectWorkflow} />}
    {step === 2 && <TaskInfo name={name} description={description} workspaceId={workspaceId} tags={tags} workspaces={workspaces} onName={setName} onDescription={setDescription} onWorkspace={setWorkspaceId} onTags={setTags} />}
    {step === 3 && <DynamicInputs definitions={definition.inputs || []} values={inputs} onChange={setInputs} />}
    {step === 4 && <ExecutionConfig steps={definition.steps || []} providers={providers} skills={skills} overrides={executionOverrides} onChange={setExecutionOverrides} />}
    {step === 5 && <OutputConfig outputs={definition.outputs || []} steps={definition.steps || []} overrides={outputOverrides} onChange={setOutputOverrides} />}
    {step === 6 && <TaskReview task={task} name={name} workspace={workspaces.find((item) => item.id === workspaceId)?.name || ""} definition={definition} inputs={inputs} executionOverrides={executionOverrides} outputOverrides={outputOverrides} onFinish={finish} busy={busy} />}
  </section>{error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}<footer><button type="button" disabled={step === 1 || busy} onClick={() => void go(step - 1)}><ArrowLeft size={14} />上一步</button><span>第 {step} / 6 步</span>{step < 6 ? <button className="ct-v2-primary-button" type="button" disabled={busy} onClick={() => void go(step + 1)}>{busy && <Loader2 className="animate-spin" size={14} />}保存并继续<ArrowRight size={14} /></button> : <span />}</footer></main>;
}

function WorkflowChoice({ items, value, onChange }: { items: WorkflowListItem[]; value: string; onChange: (id: string) => void }) { return <div className="ct-v2-task-step"><h2>选择已发布工作流</h2><p>任务会固定引用当前发布版本，后续发布不会改变本任务。</p><div className="ct-v2-workflow-choice">{items.map((item) => <label className={value === item.id ? "is-selected" : ""} key={item.id}><input type="radio" checked={value === item.id} onChange={() => onChange(item.id)} /><strong>{item.name}</strong><span>{item.description || "无描述"}</span><small>Published · {item.v2?.published_version_id?.slice(0, 14)}</small></label>)}</div></div>; }
function TaskInfo({ name, description, workspaceId, tags, workspaces, onName, onDescription, onWorkspace, onTags }: { name: string; description: string; workspaceId: string; tags: string; workspaces: Workspace[]; onName:(v:string)=>void; onDescription:(v:string)=>void; onWorkspace:(v:string)=>void; onTags:(v:string)=>void }) { return <div className="ct-v2-task-step"><h2>任务信息与工作空间</h2><div className="ct-v2-task-form-grid"><label><span>任务名称 *</span><input value={name} onChange={(e)=>onName(e.target.value)} /></label><label><span>工作空间 *</span><select value={workspaceId} onChange={(e)=>onWorkspace(e.target.value)}><option value="">选择已创建的工作空间</option>{workspaces.map((item)=><option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label className="is-wide"><span>描述</span><textarea rows={3} value={description} onChange={(e)=>onDescription(e.target.value)} /></label><label className="is-wide"><span>标签</span><input value={tags} onChange={(e)=>onTags(e.target.value)} placeholder="存储, SPDK, 回归" /></label></div></div>; }
function DynamicInputs({ definitions, values, onChange }: { definitions: Array<Record<string, unknown>>; values: Record<string, unknown>; onChange:(v:Record<string, unknown>)=>void }) {
  const visible = definitions.filter((item) => !isWorkspaceInputDefinition(item));
  const upload = async (files: File[], id: string, multiple: boolean) => {
    const uploaded = await Promise.all(files.map((file) => api.workbench.uploadInputFile(file, id)));
    onChange({ ...values, [id]: multiple ? uploaded.map((item) => item.input_payload) : uploaded[0]?.input_payload });
  };
  return <div className="ct-v2-task-step"><h2>填写本次输入</h2><p>字段名称和要求来自工作流版本；工作空间路径由系统自动注入。</p><div className="ct-v2-dynamic-inputs">{visible.map((item) => {
    const id = String(item.id); const type = String(item.type || "text"); const isFileSet = type === "file_set";
    const currentFiles = isFileSet ? (values[id] as Array<Record<string, unknown>> || []) : values[id] ? [values[id] as Record<string, unknown>] : [];
    return <label key={id}><span>{String(item.label || id)}{item.required ? " *" : ""}</span><small>{String(item.role || type)} · {String(item.resolver || "manual")}</small>{["file", "file_set", "coverage_report", "patch", "diff"].includes(type) ? <><input type="file" multiple={isFileSet} onChange={(event) => { const files = Array.from(event.target.files || []); if (files.length) void upload(files, id, isFileSet); }} />{currentFiles.length > 0 && <small className="ct-v2-uploaded-files">已选择 {currentFiles.length} 个文件</small>}</> : type === "boolean" ? <input type="checkbox" checked={Boolean(values[id])} onChange={(event) => onChange({ ...values, [id]: event.target.checked })} /> : type === "long_text" ? <textarea rows={5} value={String(values[id] || "")} onChange={(event) => onChange({ ...values, [id]: event.target.value })} /> : <input value={String(values[id] || "")} onChange={(event) => onChange({ ...values, [id]: event.target.value })} />}</label>;
  })}{!visible.length && <p>该工作流只需要所选工作空间，无需额外输入。</p>}</div></div>;
}

function ExecutionConfig({ steps, providers, skills, overrides, onChange }: { steps:Array<Record<string,unknown>>;providers:WorkflowProviderCapability[];skills:WorkflowSkillCapability[];overrides:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void }) {
  const nodes = (overrides.nodes || {}) as Record<string, Record<string, unknown>>;
  const agentSteps = steps.filter((item) => item.type === "agent_task");
  const executors = providers.filter((provider) => provider.capabilities?.supports_artifact_export);
  const setNode = (id:string,value:Record<string,unknown>|null) => { const next = {...nodes}; if (value) next[id] = value; else delete next[id]; onChange(Object.keys(next).length ? {nodes:next} : {}); };
  return <div className="ct-v2-task-step"><h2>确认执行配置</h2><p>默认完整继承工作流；只有 Agent 节点可覆盖执行器、MCP 和 Skills。</p><div className="ct-v2-execution-list">{agentSteps.map((item) => {
    const id = String(item.id); const current = nodes[id]; const selectedProvider = String((current?.provider as Record<string,unknown>)?.value || item.provider || ""); const provider = providers.find((candidate) => candidate.provider === selectedProvider); const mcpOptions = provider?.capabilities?.mcp_profiles || [];
    return <article key={id}><div><strong>{String(item.label || id)}</strong><span>{providers.find((candidate) => candidate.provider === String(item.provider || ""))?.display_name || String(item.provider || item.type)}</span><small>Skills: {String((item.skills as string[] || []).join("、") || "无")} · MCP: {String((item.mcp_profiles as string[] || []).join("、") || "无")}</small></div><label><input type="checkbox" checked={Boolean(current)} onChange={(event) => setNode(id, event.target.checked ? {provider:{mode:"replace",value:String(item.provider || "")},mcp_profiles:{mode:"replace",value:item.mcp_profiles || []},skill_ids:{mode:"replace",value:item.skills || []}} : null)} />覆盖本任务</label>{current && <div className="ct-v2-override-fields"><label><span>执行器</span><select value={selectedProvider} onChange={(event) => setNode(id,{...current,provider:{mode:"replace",value:event.target.value},mcp_profiles:{mode:"replace",value:[]}})}>{executors.map((candidate) => <option key={candidate.provider} value={candidate.provider}>{candidate.display_name} · {providerStatus(candidate.status)}</option>)}</select></label><SearchMultiSelect label="MCP" options={mcpOptions.map((value) => ({id:value,label:value}))} selected={((current.mcp_profiles as Record<string,unknown>)?.value as string[] || [])} emptyText={provider?.capabilities?.supports_mcp ? "该执行器尚未配置 MCP" : "该执行器不支持 MCP"} onChange={(value) => setNode(id,{...current,mcp_profiles:{mode:"replace",value}})} /><SearchMultiSelect label="Skills" options={skills} selected={((current.skill_ids as Record<string,unknown>)?.value as string[] || [])} emptyText="没有可用 Skills" onChange={(value) => setNode(id,{...current,skill_ids:{mode:"replace",value}})} /><button type="button" onClick={() => setNode(id,null)}><RotateCcw size={13}/>恢复默认</button></div>}</article>;
  })}{!agentSteps.length && <p>这个工作流没有需要用户覆盖的 Agent 节点。</p>}</div></div>;
}

function SearchMultiSelect({ label, options, selected, emptyText, onChange }: { label:string; options:Array<{id:string;label:string;description?:string}>; selected:string[]; emptyText:string; onChange:(value:string[])=>void }) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const visible = options.filter((item) => !normalized || `${item.label} ${item.id} ${item.description || ""}`.toLowerCase().includes(normalized)).slice(0, 8);
  return <fieldset className="ct-v2-search-select"><legend>{label}<small>{selected.length ? `已选 ${selected.length}` : "继承值已复制"}</small></legend>{options.length > 6 && <input aria-label={`搜索 ${label}`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索 ${label}`} />}<div>{visible.map((item) => <label key={item.id} title={item.description || item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={(event) => onChange(event.target.checked ? [...selected, item.id] : selected.filter((value) => value !== item.id))} /><span>{item.label}</span><small>{item.id}</small></label>)}{!visible.length && <p>{emptyText}</p>}</div></fieldset>;
}

function providerStatus(status:string) { return ({available:"可用",configured:"已配置",workflow_callable:"可运行",bridge_disabled:"桥接未启用"} as Record<string,string>)[status] || status || "状态未知"; }
function OutputConfig({ outputs, steps, overrides, onChange }: { outputs:Array<Record<string,unknown>>;steps:Array<Record<string,unknown>>;overrides:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void }) {
  const changes = (overrides.outputs || {}) as Record<string,Record<string,unknown>>; const custom = (overrides.custom_outputs || []) as Array<Record<string,unknown>>;
  const update = (id:string,value:Record<string,unknown>) => onChange({...overrides,outputs:{...changes,[id]:value}});
  const updateCustom = (index:number, value:Record<string,unknown>) => { const next=[...custom]; next[index]=value; onChange({...overrides,custom_outputs:next}); };
  const add = () => onChange({...overrides,custom_outputs:[...custom,{id:`output_${custom.length+1}`,label:"任务专用输出",type:"markdown",from:String(steps[0]?.id || ""),artifact:`output-${custom.length+1}.md`,required:false}]});
  return <div className="ct-v2-task-step"><div className="ct-v2-step-title"><div><h2>确认交付输出</h2><p>输出名称会提醒用户，文件名与格式会直接约束执行器交付。</p></div><button type="button" onClick={add}><Plus size={14}/>添加输出</button></div><div className="ct-v2-output-list">{outputs.map((item) => { const id=String(item.id); const value=changes[id] || {}; return <article key={id}><label><input type="checkbox" disabled={Boolean(item.required)} checked={item.required ? true : value.enabled !== false} onChange={(event) => update(id,{...value,enabled:event.target.checked})}/>{String(item.label || id)}{item.required ? "（必需）" : ""}</label><input aria-label={`${id} 展示名称`} value={String(value.label || item.label || id)} onChange={(event) => update(id,{...value,label:event.target.value})}/><input aria-label={`${id} 文件名`} value={String(value.artifact || item.artifact || "")} onChange={(event) => update(id,{...value,artifact:event.target.value})}/><span className="ct-v2-output-type">{String(item.type || "文件")}</span></article>; })}{custom.map((item,index) => <article className="is-custom" key={String(item.id)}><input aria-label={`任务专用输出 ${index+1} 名称`} value={String(item.label || "")} onChange={(event) => updateCustom(index,{...item,label:event.target.value})}/><input aria-label={`任务专用输出 ${index+1} 文件名`} value={String(item.artifact || "")} onChange={(event) => updateCustom(index,{...item,artifact:event.target.value})}/><select aria-label={`任务专用输出 ${index+1} 类型`} value={String(item.type || "markdown")} onChange={(event) => { const type=event.target.value; updateCustom(index,{...item,type,schema:type === "json" ? {type:"object"} : undefined}); }}><option value="markdown">Markdown 报告</option><option value="json">JSON 数据</option><option value="test_cases">测试用例</option><option value="text">纯文本</option></select><select aria-label={`任务专用输出 ${index+1} 来源节点`} value={String(item.from || "")} onChange={(event) => updateCustom(index,{...item,from:event.target.value})}>{steps.map((step) => <option key={String(step.id)} value={String(step.id)}>{String(step.label || step.id)}</option>)}</select>{item.type === "json" && <select aria-label={`任务专用输出 ${index+1} JSON 结构`} value={String((item.schema as Record<string,unknown>)?.type || "object")} onChange={(event) => updateCustom(index,{...item,schema:{type:event.target.value}})}><option value="object">JSON 对象</option><option value="array">JSON 数组</option></select>}<button title="删除任务专用输出" type="button" onClick={() => onChange({...overrides,custom_outputs:custom.filter((_,candidate) => candidate !== index)})}><Trash2 size={14}/></button></article>)}</div></div>;
}
function TaskReview({ task,name,workspace,definition,inputs,executionOverrides,outputOverrides,onFinish,busy }:{task:WorkbenchTask|null;name:string;workspace:string;definition:Definition;inputs:Record<string,unknown>;executionOverrides:Record<string,unknown>;outputOverrides:Record<string,unknown>;onFinish:(m:"draft"|"ready"|"run")=>Promise<void>;busy:boolean}) { return <div className="ct-v2-task-step"><h2>检查并运行</h2><div className="ct-v2-task-review"><section><strong>{name}</strong><span>{workspace}</span><small>{task?.workflow_name} · {task?.workflow_version_id}</small></section><section><dl><div><dt>输入</dt><dd>{Object.keys(inputs).length}/{(definition.inputs||[]).filter((item)=>!isWorkspaceInputDefinition(item)).length}</dd></div><div><dt>执行节点</dt><dd>{(definition.steps||[]).length}</dd></div><div><dt>任务覆盖</dt><dd>{Object.keys((executionOverrides.nodes||{}) as object).length}</dd></div><div><dt>输出</dt><dd>{(definition.outputs||[]).length+(((outputOverrides.custom_outputs||[]) as unknown[]).length)}</dd></div></dl></section></div><div className="ct-v2-finish-actions"><button type="button" disabled={busy} onClick={()=>void onFinish("draft")}>保存草稿</button><button type="button" disabled={busy} onClick={()=>void onFinish("ready")}>保存为就绪任务</button><button className="ct-v2-primary-button" type="button" disabled={busy} onClick={()=>void onFinish("run")}>{busy&&<Loader2 className="animate-spin" size={14}/>}保存并运行</button></div></div>; }
function validateStep(step:number,data:{workflowId:string;workspaceId:string;name:string;definition:Definition;inputs:Record<string,unknown>}) { if(step===1&&!data.workflowId)throw new Error("请选择已发布工作流");if(step===2&&(!data.name.trim()||!data.workspaceId))throw new Error("请填写任务名称并选择工作空间");if(step===3){const missing=(data.definition.inputs||[]).filter(item=>item.required&&!isWorkspaceInputDefinition(item)&&isMissing(data.inputs[String(item.id)]));if(missing.length)throw new Error(`请填写必需输入：${missing.map(item=>String(item.label||item.id)).join("、")}`);} }
function isWorkspaceInputDefinition(item: Record<string, unknown>) {
  return item.resolver === "workspace" || (
    String(item.id) === "repo_path" && String(item.type) === "directory"
  );
}
function isMissing(value:unknown) { return value == null || (typeof value === "string" && !value.trim()) || (Array.isArray(value) && value.length === 0) || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0); }

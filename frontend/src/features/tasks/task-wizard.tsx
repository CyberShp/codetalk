"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, Loader2, MessageSquareText, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "@/lib/api";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkbenchTask } from "@/lib/types/task";
import type { WorkflowExecutionProfile, WorkflowListItem, WorkflowProviderCapability, WorkflowSkillCapability, WorkflowVersion } from "@/lib/types/workflow";
import type { Workspace } from "@/lib/types";
import { workflowStepMcpProfiles } from "./task-wizard-contract.mjs";

const labels = ["选择工作流", "任务信息", "填写输入", "执行配置", "确认输出", "检查运行"];
type Definition = { compiled_contract_version?: number; inputs?: Array<Record<string, unknown>>; steps?: Array<Record<string, unknown>>; outputs?: Array<Record<string, unknown>>; execution_profiles?: WorkflowExecutionProfile[]; default_execution_profile?: WorkflowExecutionProfile["id"] };
const CORE_WORKFLOW_IDS = new Set(["module_analysis", "resource_leak_hunt", "mr_blackbox_test", "patch_impact_review", "source_flow_sfmea_blackbox", "testing_activity_orchestration", "coverage_gap", "defect_retest", "module_risk_report"]);
const compatibilityExecutionProfiles: WorkflowExecutionProfile[] = [
  { id: "rapid", label: "速度型", delivery_class: "bounded_analysis", expected_duration_minutes: [8, 20], max_subagents: 1 },
  { id: "deep", label: "深度型", delivery_class: "full_test_delivery", expected_duration_minutes: [40, 90], max_subagents: 4 },
];

export function TaskWizard() {
  const router = useRouter();
  const params = useSearchParams();
  const taskParam = params.get("task") || "";
  const requestedWorkflowId = params.get("workflow_id") || "";
  const requestedWorkspaceId = params.get("workspace_id") || "";
  const requestedTarget = params.get("target") || "";
  const [step, setStep] = useState(Math.min(6, Math.max(1, Number(params.get("step") || 1))));
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [providers, setProviders] = useState<WorkflowProviderCapability[]>([]);
  const [skills, setSkills] = useState<WorkflowSkillCapability[]>([]);
  const [workflowId, setWorkflowId] = useState(requestedWorkflowId);
  const [versionId, setVersionId] = useState("");
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [task, setTask] = useState<WorkbenchTask | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workspaceId, setWorkspaceId] = useState(requestedWorkspaceId);
  const [tags, setTags] = useState("");
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [executionOverrides, setExecutionOverrides] = useState<Record<string, unknown>>({});
  const [outputOverrides, setOutputOverrides] = useState<Record<string, unknown>>({});
  const [executionProfileId, setExecutionProfileId] = useState<WorkflowExecutionProfile["id"]>("rapid");
  const [busy, setBusy] = useState(false);
  const [pendingUploads, setPendingUploads] = useState(0);
  const [error, setError] = useState("");
  const hydratedTaskId = useRef("");
  const hydrationRequestId = useRef(0);
  const definition = (version?.compiled_definition || task?.workflow_version?.compiled_definition || {}) as Definition;
  const isV3Contract = definition.compiled_contract_version === 3;
  const versionDefaultProfile = version
    ? ((version.compiled_definition as Definition).default_execution_profile
      || (version.compiled_definition as Definition).execution_profiles?.[0]?.id
      || "rapid")
    : "";

  useEffect(() => {
    void Promise.all([
      workflowsApi.list(),
      api.workspaces.list(),
      workflowsApi.providers(),
      workflowsApi.capabilities(),
    ]).then(([workflowItems, workspaceItems, providerItems, capabilities]) => {
      const published = workflowItems.filter((item) => Boolean(item.v2?.published_version_id));
      setWorkflows(published);
      setWorkspaces(workspaceItems);
      setProviders(providerItems.providers);
      setSkills(capabilities.skill_catalog || []);
      if (!taskParam && requestedWorkflowId) {
        const requested = published.find((item) => item.id === requestedWorkflowId);
        if (requested?.v2?.published_version_id) {
          setWorkflowId(requested.id);
          setVersionId(requested.v2.published_version_id);
          setName(`${requested.name} · ${new Date().toLocaleDateString("zh-CN")}`);
        }
      }
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "向导数据加载失败"));
  }, [requestedWorkflowId, taskParam]);
  useEffect(() => {
    if (!taskParam) {
      hydrationRequestId.current += 1;
      hydratedTaskId.current = "";
      setBusy(false);
      return;
    }
    if (hydratedTaskId.current === taskParam) return;
    const requestId = ++hydrationRequestId.current;
    let active = true;
    setBusy(true);
    void workbenchTasksApi.get(taskParam).then((item) => {
      if (!active || hydrationRequestId.current !== requestId) return;
      hydratedTaskId.current = taskParam;
      setTask(item); setWorkflowId(item.workflow_id); setVersionId(item.workflow_version_id); setName(item.name); setDescription(item.description); setWorkspaceId(item.workspace_id); setTags(item.tags.join(", ")); setInputs(item.input_values); setExecutionOverrides(item.execution_overrides); setOutputOverrides(item.output_overrides); if (item.execution_profile_id) setExecutionProfileId(item.execution_profile_id as WorkflowExecutionProfile["id"]);
    }).catch((cause) => {
      if (!active || hydrationRequestId.current !== requestId) return;
      setError(cause instanceof Error ? cause.message : "任务草稿恢复失败");
    }).finally(() => {
      if (active && hydrationRequestId.current === requestId) setBusy(false);
    });
    return () => { active = false; };
  }, [taskParam]);
  useEffect(() => {
    if (!workflowId || !versionId) return;
    let active = true;
    void workflowsApi.version(workflowId, versionId).then((item) => {
      if (!active) return;
      setVersion(item);
      if (!taskParam && requestedTarget) {
        const definition = (item.compiled_definition || {}) as Definition;
        const inputIds = new Set(
          (definition.inputs || []).map((input) => String(input.id || "")),
        );
        const targetInputId = [
          "test_goal",
          "analysis_object",
          "analysis_target",
          "module_scope",
          "target_scope",
        ].find((inputId) => inputIds.has(inputId));
        if (targetInputId) {
          setInputs((current) => ({
            ...current,
            [targetInputId]: current[targetInputId] || requestedTarget,
          }));
        }
      }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "工作流版本加载失败");
    });
    return () => { active = false; };
  }, [requestedTarget, taskParam, versionId, workflowId]);
  useEffect(() => {
    // A persisted task snapshot is authoritative.  Re-fetching its immutable
    // WorkflowVersion while moving between wizard steps used to reset a user
    // selection (for example deep -> rapid) back to the workflow default just
    // before RunSnapshot creation.
    if (task) return;
    const selected = versionDefaultProfile || "rapid";
    if (selected) setExecutionProfileId(selected);
  }, [task, version?.version_id, versionDefaultProfile]);

  const selectWorkflow = (id: string) => { const item = workflows.find((candidate) => candidate.id === id); const published = item?.v2?.published_version_id || ""; setWorkflowId(id); setVersionId(published); setVersion(null); if (item) setName(`${item.name} · ${new Date().toLocaleDateString("zh-CN")}`); };
  const save = async (lifecycleStatus?: "draft" | "ready") => {
    if (!workflowId || !versionId || !workspaceId || !name.trim()) throw new Error("请完整填写任务名称、工作流和工作空间");
    const mutable = { name: name.trim(), description: description.trim(), lifecycle_status: lifecycleStatus || task?.lifecycle_status || "draft", execution_profile_id: executionProfileId, input_values: inputs, execution_overrides: executionOverrides, output_overrides: sanitizeOutputOverrides(outputOverrides, isV3Contract), tags: tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean) };
    const saved = task
      ? await workbenchTasksApi.update(task.task_id, mutable)
      : await workbenchTasksApi.create({ ...mutable, workspace_id: workspaceId, workflow_id: workflowId, workflow_version_id: versionId });
    hydratedTaskId.current = saved.task_id;
    setTask(saved); return saved;
  };
  const go = async (next: number) => { setError(""); setBusy(true); try { validateStep(step, { workflowId, workspaceId, name, definition, inputs }); if (step >= 2) { const saved = await save("draft"); router.replace(`/tasks/new?task=${saved.task_id}&step=${next}`); } else router.replace(`/tasks/new?step=${next}`); setStep(next); } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); } finally { setBusy(false); } };
  const finish = async (mode: "draft" | "ready" | "run") => { setBusy(true); setError(""); try { const saved = await save("draft"); if (mode === "draft") { router.push(`/tasks/${saved.task_id}`); return; } await workbenchTasksApi.compile(saved.task_id); const ready = await workbenchTasksApi.update(saved.task_id, { lifecycle_status: "ready" }); if (mode === "ready") { router.push(`/tasks/${ready.task_id}`); return; } const attempt = await workbenchTasksApi.createRun(ready.task_id, "", executionProfileId); await api.workbench.taskRuns.execute(attempt.task_run_id, 0, true); router.push(`/tasks/${ready.task_id}/runs/${attempt.task_run_id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务检查失败"); } finally { setBusy(false); } };

  return <main className="ct-v2-task-wizard"><header><Link href="/tasks"><ArrowLeft size={15} />任务中心</Link><div><span>新建任务</span><h1>{name || "未命名任务"}</h1></div><em>{task ? "草稿已保存" : "尚未创建草稿"}</em></header>{task?.ai_origins?.[0] && <div className="ct-v2-task-origin" role="status"><MessageSquareText size={14}/><span>此任务来自 AI 线程，工作流版本和工作空间已固定。</span><Link href={`/ai/${encodeURIComponent(task.ai_origins[0].conversation_id)}`}>返回来源线程</Link></div>}<ol>{labels.map((label, index) => <li key={label} className={step === index + 1 ? "is-active" : step > index + 1 ? "is-done" : ""}><span>{step > index + 1 ? <Check size={12} /> : index + 1}</span><strong>{label}</strong></li>)}</ol><section className="ct-v2-task-wizard-body">
    {step === 1 && <WorkflowChoice items={workflows} value={workflowId} onChange={selectWorkflow} locked={Boolean(task)} />}
    {step === 2 && <TaskInfo name={name} description={description} workspaceId={workspaceId} tags={tags} workspaces={workspaces} onName={setName} onDescription={setDescription} onWorkspace={setWorkspaceId} onTags={setTags} workspaceLocked={Boolean(task)} />}
    {step === 3 && <DynamicInputs definitions={definition.inputs || []} values={inputs} onChange={setInputs} onUploadBusyChange={(delta) => setPendingUploads((count) => Math.max(0, count + delta))} onUploadError={setError} />}
    {step === 4 && <ExecutionConfig steps={definition.steps || []} providers={providers} skills={skills} overrides={executionOverrides} onChange={setExecutionOverrides} profiles={definition.execution_profiles?.length ? definition.execution_profiles : compatibilityExecutionProfiles} selectedProfile={executionProfileId} onProfileChange={setExecutionProfileId} />}
    {step === 5 && <OutputConfig outputs={definition.outputs || []} steps={definition.steps || []} overrides={outputOverrides} onChange={setOutputOverrides} v3Contract={isV3Contract} />}
    {step === 6 && <TaskReview task={task} name={name} workspace={workspaces.find((item) => item.id === workspaceId)?.name || ""} definition={definition} inputs={inputs} outputOverrides={outputOverrides} executionProfileId={executionProfileId} onFinish={finish} busy={busy} v3Contract={isV3Contract} />}
  </section>{error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}<footer><button type="button" disabled={step === 1 || busy || pendingUploads > 0} onClick={() => void go(step - 1)}><ArrowLeft size={14} />上一步</button><span>第 {step} / 6 步</span>{step < 6 ? <button className="ct-v2-primary-button" type="button" disabled={busy || pendingUploads > 0} onClick={() => void go(step + 1)}>{(busy || pendingUploads > 0) && <Loader2 className="animate-spin" size={14} />}{pendingUploads > 0 ? "文件上传中" : "保存并继续"}<ArrowRight size={14} /></button> : <span />}</footer></main>;
}

function WorkflowChoice({ items, value, onChange, locked }: { items: WorkflowListItem[]; value: string; onChange: (id: string) => void; locked: boolean }) { const [showAll, setShowAll] = useState(false); const visible = items.filter((item) => locked ? item.id === value : showAll || CORE_WORKFLOW_IDS.has(item.id) || item.id === value); return <div className="ct-v2-task-step"><div className="ct-v2-workflow-choice-heading"><div><h2>选择已发布工作流</h2><p>{locked ? "任务草稿已固定发布版本；如需其他工作流，请新建任务。" : "任务会固定引用当前发布版本，后续发布不会改变本任务。"}</p></div>{!locked && <div className="ct-v2-workflow-filter" role="group" aria-label="工作流范围"><button type="button" className={!showAll ? "is-active" : ""} onClick={() => setShowAll(false)}>常用工作流</button><button type="button" className={showAll ? "is-active" : ""} onClick={() => setShowAll(true)}>全部场景</button></div>}</div><div className="ct-v2-workflow-choice">{visible.map((item) => <label className={value === item.id ? "is-selected" : ""} key={item.id}><input type="radio" disabled={locked} checked={value === item.id} onChange={() => onChange(item.id)} /><strong>{item.presentation?.label ?? item.name}</strong><span>{item.description || "无描述"}</span><small>已发布版本 V{item.version || 1}{item.presentation?.lifecycle === "legacy" ? " · Legacy" : ""}</small></label>)}</div></div>; }
function TaskInfo({ name, description, workspaceId, tags, workspaces, onName, onDescription, onWorkspace, onTags, workspaceLocked }: { name: string; description: string; workspaceId: string; tags: string; workspaces: Workspace[]; onName:(v:string)=>void; onDescription:(v:string)=>void; onWorkspace:(v:string)=>void; onTags:(v:string)=>void; workspaceLocked:boolean }) { return <div className="ct-v2-task-step"><h2>任务信息与工作空间</h2><div className="ct-v2-task-form-grid"><label><span>任务名称 *</span><input value={name} onChange={(e)=>onName(e.target.value)} /></label><label><span>工作空间 *</span><select disabled={workspaceLocked} value={workspaceId} onChange={(e)=>onWorkspace(e.target.value)}><option value="">选择已创建的工作空间</option>{workspaces.map((item)=><option value={item.id} key={item.id}>{item.name}</option>)}</select>{workspaceLocked && <small>任务创建后工作空间保持固定</small>}</label><label className="is-wide"><span>描述</span><textarea rows={3} value={description} onChange={(e)=>onDescription(e.target.value)} /></label><label className="is-wide"><span>标签</span><input value={tags} onChange={(e)=>onTags(e.target.value)} placeholder="存储, SPDK, 回归" /></label></div></div>; }
function DynamicInputs({ definitions, values, onChange, onUploadBusyChange, onUploadError }: { definitions: Array<Record<string, unknown>>; values: Record<string, unknown>; onChange:Dispatch<SetStateAction<Record<string, unknown>>>; onUploadBusyChange:(delta:number)=>void; onUploadError:(message:string)=>void }) {
  const visible = definitions.filter((item) => !isWorkspaceInputDefinition(item));
  const upload = async (files: File[], id: string, multiple: boolean) => {
    onUploadError("");
    onUploadBusyChange(1);
    try {
      const uploaded = await Promise.all(files.map((file) => api.workbench.uploadInputFile(file, id)));
      onChange((currentValues) => ({ ...currentValues, [id]: multiple ? uploaded.map((item) => item.input_payload) : uploaded[0]?.input_payload }));
    } catch (cause) {
      onUploadError(cause instanceof Error ? `文件上传失败：${cause.message}` : "文件上传失败，请重试。");
    } finally {
      onUploadBusyChange(-1);
    }
  };
  return <div className="ct-v2-task-step"><h2>填写本次输入</h2><p>字段名称和要求来自工作流版本；工作空间路径由系统自动注入。</p><div className="ct-v2-dynamic-inputs">{visible.map((item) => {
    const id = String(item.id); const type = String(item.type || "text"); const isFileSet = type === "file_set";
    const currentFiles = isFileSet ? (values[id] as Array<Record<string, unknown>> || []) : values[id] ? [values[id] as Record<string, unknown>] : [];
    return <label key={id}><span>{String(item.label || id)}{item.required ? " *" : ""}</span><small>{String(item.role || type)} · {inputResolverLabel(item.resolver)}</small>{Boolean(item.example) && <small>示例：{String(item.example)}</small>}{["file", "file_set", "coverage_report", "patch", "diff"].includes(type) ? <><input type="file" multiple={isFileSet} onChange={(event) => { const files = Array.from(event.target.files || []); if (files.length) void upload(files, id, isFileSet); }} />{currentFiles.length > 0 && <small className="ct-v2-uploaded-files">已选择 {currentFiles.length} 个文件</small>}</> : type === "boolean" ? <input type="checkbox" checked={Boolean(values[id])} onChange={(event) => onChange((currentValues) => ({ ...currentValues, [id]: event.target.checked }))} /> : ["text", "long_text"].includes(type) ? <textarea rows={type === "long_text" ? 5 : 3} value={String(values[id] || "")} onChange={(event) => onChange((currentValues) => ({ ...currentValues, [id]: event.target.value }))} /> : <input value={String(values[id] || "")} onChange={(event) => onChange((currentValues) => ({ ...currentValues, [id]: event.target.value }))} />}{Boolean(item.required) && isMissing(values[id]) && Boolean(item.missing_guidance) && <small className="ct-v2-input-guidance">{String(item.missing_guidance)}</small>}</label>;
  })}{!visible.length && <p>该工作流只需要所选工作空间，无需额外输入。</p>}</div></div>;
}

function ExecutionConfig({ steps, providers, skills, overrides, onChange, profiles, selectedProfile, onProfileChange }: { steps:Array<Record<string,unknown>>;providers:WorkflowProviderCapability[];skills:WorkflowSkillCapability[];overrides:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void;profiles:WorkflowExecutionProfile[];selectedProfile:WorkflowExecutionProfile["id"];onProfileChange:(value:WorkflowExecutionProfile["id"])=>void }) {
  const nodes = (overrides.nodes || {}) as Record<string, Record<string, unknown>>;
  const agentSteps = steps.filter((item) => item.type === "agent_task");
  const executors = providers.filter((provider) => provider.capabilities?.supports_artifact_export);
  const setNode = (id:string,value:Record<string,unknown>|null) => { const next = {...nodes}; if (value) next[id] = value; else delete next[id]; onChange(Object.keys(next).length ? {nodes:next} : {}); };
  const selectedPolicy = profiles.find((profile) => profile.id === selectedProfile);
  return <div className="ct-v2-task-step"><h2>确认执行配置</h2><p>默认完整继承工作流；只有 Agent 节点可覆盖执行器、MCP 和 Skills。</p>{profiles.length > 0 && <fieldset className="ct-v2-execution-profile"><legend>执行档位</legend><div>{profiles.map((profile) => <label key={profile.id}><input type="radio" name="execution-profile" checked={selectedProfile === profile.id} onChange={() => onProfileChange(profile.id)} /><strong>{profile.label}</strong><span>{profile.delivery_class === "bounded_analysis" ? "聚焦分析" : "完整测试交付"} · 预计 {profile.expected_duration_minutes[0]}-{profile.expected_duration_minutes[1]} 分钟 · 最多 {profile.max_subagents} 个辅助 Agent</span></label>)}</div>{selectedPolicy && <small>本次选择会在启动时冻结到运行快照，重试将沿用该档位。</small>}</fieldset>}<div className="ct-v2-execution-list">{agentSteps.map((item) => {
    const id = String(item.id); const current = nodes[id]; const selectedProvider = String((current?.provider as Record<string,unknown>)?.value || item.provider || ""); const provider = providers.find((candidate) => candidate.provider === selectedProvider); const mcpOptions = provider?.capabilities?.mcp_profiles || []; const inheritedMcpProfiles = workflowStepMcpProfiles(item);
    return <article key={id}><div><strong>{String(item.label || id)}</strong><span>{providers.find((candidate) => candidate.provider === String(item.provider || ""))?.display_name || String(item.provider || item.type)}</span><small>Skills: {String((item.skills as string[] || []).join("、") || "无")} · MCP: {String(inheritedMcpProfiles.join("、") || "无")}</small></div><label><input type="checkbox" checked={Boolean(current)} onChange={(event) => setNode(id, event.target.checked ? {provider:{mode:"replace",value:String(item.provider || "")},mcp_profiles:{mode:"replace",value:inheritedMcpProfiles},skill_ids:{mode:"replace",value:item.skills || []}} : null)} />覆盖本任务</label>{current && <div className="ct-v2-override-fields"><label><span>执行器</span><select value={selectedProvider} onChange={(event) => setNode(id,{...current,provider:{mode:"replace",value:event.target.value},mcp_profiles:{mode:"replace",value:[]}})}>{executors.map((candidate) => <option key={candidate.provider} value={candidate.provider}>{candidate.display_name} · {providerStatus(candidate.status)}</option>)}</select></label><SearchMultiSelect label="MCP" options={mcpOptions.map((value) => ({id:value,label:value}))} selected={((current.mcp_profiles as Record<string,unknown>)?.value as string[] || [])} emptyText={provider?.capabilities?.supports_mcp ? "该执行器尚未配置 MCP" : "该执行器不支持 MCP"} onChange={(value) => setNode(id,{...current,mcp_profiles:{mode:"replace",value}})} /><SearchMultiSelect label="Skills" options={skills} selected={((current.skill_ids as Record<string,unknown>)?.value as string[] || [])} emptyText="没有可用 Skills" onChange={(value) => setNode(id,{...current,skill_ids:{mode:"replace",value}})} /><button type="button" onClick={() => setNode(id,null)}><RotateCcw size={13}/>恢复默认</button></div>}</article>;
  })}{!agentSteps.length && <p>这个工作流没有需要用户覆盖的 Agent 节点。</p>}</div></div>;
}

function SearchMultiSelect({ label, options, selected, emptyText, onChange }: { label:string; options:Array<{id:string;label:string;description?:string}>; selected:string[]; emptyText:string; onChange:(value:string[])=>void }) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const visible = options.filter((item) => !normalized || `${item.label} ${item.id} ${item.description || ""}`.toLowerCase().includes(normalized)).slice(0, 8);
  return <fieldset className="ct-v2-search-select"><legend>{label}<small>{selected.length ? `已选 ${selected.length}` : "继承值已复制"}</small></legend>{options.length > 6 && <input aria-label={`搜索 ${label}`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索 ${label}`} />}<div>{visible.map((item) => <label key={item.id} title={item.description || item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={(event) => onChange(event.target.checked ? [...selected, item.id] : selected.filter((value) => value !== item.id))} /><span>{item.label}</span><small>{item.id}</small></label>)}{!visible.length && <p>{emptyText}</p>}</div></fieldset>;
}

function providerStatus(status:string) { return ({available:"可用",configured:"已配置",workflow_callable:"可运行",bridge_disabled:"桥接未启用"} as Record<string,string>)[status] || status || "状态未知"; }
function OutputConfig({ outputs, steps, overrides, onChange, v3Contract }: { outputs:Array<Record<string,unknown>>;steps:Array<Record<string,unknown>>;overrides:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void;v3Contract:boolean }) {
  const changes = (overrides.outputs || {}) as Record<string,Record<string,unknown>>; const custom = v3Contract ? [] : (overrides.custom_outputs || []) as Array<Record<string,unknown>>;
  const update = (id:string,value:Record<string,unknown>) => onChange({...overrides,outputs:{...changes,[id]:value}});
  const updateCustom = (index:number, value:Record<string,unknown>) => { const next=[...custom]; next[index]=value; onChange({...overrides,custom_outputs:next}); };
  const add = () => onChange({...overrides,custom_outputs:[...custom,{id:`output_${custom.length+1}`,label:"任务专用输出",type:"markdown",from:String(steps[0]?.id || ""),artifact:`output-${custom.length+1}.md`,required:false}]});
  return <div className="ct-v2-task-step"><div className="ct-v2-step-title"><div><h2>确认交付输出</h2><p>{v3Contract ? "此版本只允许交付工作流已声明的输出；可选输出可以在本次任务中启停。" : "输出名称会提醒用户，文件名与格式会直接约束执行器交付。"}</p></div>{!v3Contract && <button type="button" onClick={add}><Plus size={14}/>添加输出</button>}</div><div className="ct-v2-output-list">{outputs.map((item) => { const id=String(item.id); const displayLabel=String(item.label || "输出"); const value=changes[id] || {}; const mindmap=item.type === "test_design_mindmap"; const enabled=outputEnabled(item, overrides); return <article key={id}><label><input type="checkbox" disabled={Boolean(item.required)} checked={enabled} onChange={(event) => update(id,{...value,enabled:event.target.checked})}/>{displayLabel}{item.required ? "（必需）" : ""}</label><input aria-label={`${displayLabel} 展示名称`} readOnly={v3Contract} value={String(value.label || item.label || id)} onChange={(event) => update(id,{...value,label:event.target.value})}/><input aria-label={`${displayLabel} 文件名`} readOnly={v3Contract || mindmap} value={String(value.artifact || item.artifact || "")} onChange={(event) => update(id,{...value,artifact:event.target.value})}/><span className="ct-v2-output-type">{mindmap ? "测试设计脑图 · JSON / HTML / SVG" : String(item.type || "文件")}</span></article>; })}{custom.map((item,index) => { const mindmap=item.type === "test_design_mindmap"; return <article className="is-custom" key={String(item.id)}><input aria-label={`任务专用输出 ${index+1} 名称`} value={String(item.label || "")} onChange={(event) => updateCustom(index,{...item,label:event.target.value})}/><input aria-label={`任务专用输出 ${index+1} 文件名`} readOnly={mindmap} value={String(item.artifact || "")} onChange={(event) => updateCustom(index,{...item,artifact:event.target.value})}/><select aria-label={`任务专用输出 ${index+1} 类型`} value={String(item.type || "markdown")} onChange={(event) => { const type=event.target.value; updateCustom(index,type === "test_design_mindmap" ? {...item,type,label:"测试设计脑图",artifact:"test_design_mindmap.json",companion_artifacts:["test_design_mindmap.html","test_design_mindmap.svg"],schema:undefined} : {...item,type,companion_artifacts:undefined,schema:type === "json" ? {type:"object"} : undefined}); }}><option value="markdown">Markdown 报告</option><option value="json">JSON 数据</option><option value="test_cases">测试用例</option><option value="test_design_mindmap">测试设计脑图</option><option value="text">纯文本</option></select><select aria-label={`任务专用输出 ${index+1} 来源节点`} value={String(item.from || "")} onChange={(event) => updateCustom(index,{...item,from:event.target.value})}>{steps.map((step) => <option key={String(step.id)} value={String(step.id)}>{String(step.label || step.id)}</option>)}</select>{item.type === "json" && <select aria-label={`任务专用输出 ${index+1} JSON 结构`} value={String((item.schema as Record<string,unknown>)?.type || "object")} onChange={(event) => updateCustom(index,{...item,schema:{type:event.target.value}})}><option value="object">JSON 对象</option><option value="array">JSON 数组</option></select>}<button title="删除任务专用输出" type="button" onClick={() => onChange({...overrides,custom_outputs:custom.filter((_,candidate) => candidate !== index)})}><Trash2 size={14}/></button></article>; })}</div></div>;
/*
  const add = () => { const source = outputSourceSteps[0]; if (!source) return; onChange({...overrides,custom_outputs:[...custom,{id:`output_${custom.length+1}`,label:"任务专用输出",type:"markdown",from:String(source.id || ""),artifact:`output-${custom.length+1}.md`,required:true}]}); };
  return <div className="ct-v2-task-step"><div className="ct-v2-step-title"><div><h2>确认交付输出</h2><p>输出名称会提醒用户，文件名与格式会直接约束执行器交付。</p></div><button type="button" disabled={!outputSourceSteps.length} onClick={add}><Plus size={14}/>添加输出</button></div><div className="ct-v2-output-list">{outputs.map((item) => { const id=String(item.id); const value=changes[id] || {}; return <article key={id}><label><input type="checkbox" disabled={Boolean(item.required)} checked={item.required ? true : value.enabled !== false} onChange={(event) => update(id,{...value,enabled:event.target.checked})}/>{String(item.label || id)}{item.required ? "（必需）" : ""}</label><input aria-label={`${id} 展示名称`} value={String(value.label || item.label || id)} onChange={(event) => update(id,{...value,label:event.target.value})}/><input aria-label={`${id} 文件名`} value={String(value.artifact || item.artifact || "")} onChange={(event) => update(id,{...value,artifact:event.target.value})}/><span className="ct-v2-output-type">{String(item.type || "文件")}</span></article>; })}{custom.map((item,index) => <article className="is-custom" key={String(item.id)}><input aria-label={`任务专用输出 ${index+1} 名称`} value={String(item.label || "")} onChange={(event) => updateCustom(index,{...item,label:event.target.value})}/><input aria-label={`任务专用输出 ${index+1} 文件名`} value={String(item.artifact || "")} onChange={(event) => updateCustom(index,{...item,artifact:event.target.value})}/><select aria-label={`任务专用输出 ${index+1} 类型`} value={String(item.type || "markdown")} onChange={(event) => { const type=event.target.value; updateCustom(index,{...item,type,schema:type === "json" ? {type:"object"} : undefined}); }}><option value="markdown">Markdown 报告</option><option value="json">JSON 数据</option><option value="test_cases">测试用例</option><option value="text">纯文本</option></select><select aria-label={`任务专用输出 ${index+1} 来源节点`} value={String(item.from || "")} onChange={(event) => updateCustom(index,{...item,from:event.target.value})}>{outputSourceSteps.map((step) => <option key={String(step.id)} value={String(step.id)}>{String(step.label || step.id)}</option>)}</select>{item.type === "json" && <select aria-label={`任务专用输出 ${index+1} JSON 结构`} value={String((item.schema as Record<string,unknown>)?.type || "object")} onChange={(event) => updateCustom(index,{...item,schema:{type:event.target.value}})}><option value="object">JSON 对象</option><option value="array">JSON 数组</option></select>}<button title="删除任务专用输出" type="button" onClick={() => onChange({...overrides,custom_outputs:custom.filter((_,candidate) => candidate !== index)})}><Trash2 size={14}/></button></article>)}</div></div>;
*/
}
function TaskReview({ task,name,workspace,definition,inputs,outputOverrides,executionProfileId,onFinish,busy,v3Contract }:{task:WorkbenchTask|null;name:string;workspace:string;definition:Definition;inputs:Record<string,unknown>;outputOverrides:Record<string,unknown>;executionProfileId:WorkflowExecutionProfile["id"];onFinish:(m:"draft"|"ready"|"run")=>Promise<void>;busy:boolean;v3Contract:boolean}) { const enabledOutputs=(definition.outputs||[]).filter((item)=>outputEnabled(item,outputOverrides)).length; const profile=(definition.execution_profiles?.length ? definition.execution_profiles : compatibilityExecutionProfiles).find((item)=>item.id===executionProfileId); const customOutputCount=v3Contract ? 0 : ((outputOverrides.custom_outputs||[]) as unknown[]).length; return <div className="ct-v2-task-step"><h2>检查并运行</h2><div className="ct-v2-task-review"><section><strong>{name}</strong><span>{workspace}</span><small>{task?.workflow_name} · {task?.workflow_version_number ? `V${task.workflow_version_number}` : "已冻结版本"}</small></section><section><dl><div><dt>输入</dt><dd>{Object.keys(inputs).length}/{(definition.inputs||[]).filter((item)=>!isWorkspaceInputDefinition(item)).length}</dd></div><div><dt>执行档位</dt><dd>{profile?.label || executionProfileId}</dd></div><div><dt>执行节点</dt><dd>{(definition.steps||[]).length}</dd></div><div><dt>输出</dt><dd>{enabledOutputs+customOutputCount}</dd></div></dl></section></div><div className="ct-v2-finish-actions"><button type="button" disabled={busy} onClick={()=>void onFinish("draft")}>保存草稿</button><button type="button" disabled={busy} onClick={()=>void onFinish("ready")}>保存为就绪任务</button><button className="ct-v2-primary-button" type="button" disabled={busy} onClick={()=>void onFinish("run")}>{busy&&<Loader2 className="animate-spin" size={14}/>}保存并运行</button></div></div>; }
function sanitizeOutputOverrides(overrides: Record<string, unknown>, v3Contract: boolean) { if (!v3Contract) return overrides; const { custom_outputs: customOutputs, ...declaredOutputOverrides } = overrides; void customOutputs; return declaredOutputOverrides; }
function outputEnabled(item:Record<string,unknown>, overrides:Record<string,unknown>) { const changes=(overrides.outputs||{}) as Record<string,Record<string,unknown>>; const value=changes[String(item.id)]||{}; return Boolean(item.required)||Boolean(value.enabled??item.default_enabled??true); }
function inputResolverLabel(value:unknown) { return ({manual:"手动填写",workspace:"工作空间自动注入",local:"本地文件"} as Record<string,string>)[String(value||"manual")]||String(value||"手动填写"); }
function validateStep(step:number,data:{workflowId:string;workspaceId:string;name:string;definition:Definition;inputs:Record<string,unknown>}) { if(step===1&&!data.workflowId)throw new Error("请选择已发布工作流");if(step===2&&(!data.name.trim()||!data.workspaceId))throw new Error("请填写任务名称并选择工作空间");if(step===3){const missing=(data.definition.inputs||[]).filter(item=>item.required&&!isWorkspaceInputDefinition(item)&&isMissing(data.inputs[String(item.id)]));if(missing.length)throw new Error(`请填写必需输入：${missing.map(item=>String(item.label||item.id)).join("、")}`);} }
function isWorkspaceInputDefinition(item: Record<string, unknown>) {
  return item.resolver === "workspace" || (
    String(item.id) === "repo_path" && String(item.type) === "directory"
  );
}
function isMissing(value:unknown) { return value == null || (typeof value === "string" && !value.trim()) || (Array.isArray(value) && value.length === 0) || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0); }

"use client";

import Link from "next/link";
import { Archive, ArrowLeft, FileOutput, Loader2, MessageSquareText, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { listArtifactProfiles, type ArtifactProfile } from "@/lib/artifact-profiles";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import type { PreparedWorkbenchTaskRun } from "@/lib/types";
import type { WorkbenchTask } from "@/lib/types/task";
import { taskArtifactValidationLabels, taskDeliveryLabels, taskExecutionLabels, taskGovernanceLabels, taskLifecycleLabels, taskQualityLabels, taskStatusLabel } from "./task-status";
import { hasV3RunAxisSummary, isV3TaskRun, taskRunOverviewProjection } from "./task-run-status-projection.mjs";

const tabs = ["概览", "运行记录", "输入", "执行配置", "输出", "活动记录"] as const;

export function WorkbenchTaskDetailPage({ taskId }: { taskId: string }) {
  const router = useRouter();
  const [task, setTask] = useState<WorkbenchTask | null>(null);
  const [tab, setTab] = useState<(typeof tabs)[number]>("概览");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [latestRunDetail, setLatestRunDetail] = useState<PreparedWorkbenchTaskRun | null>(null);
  const [latestRunDetailLoading, setLatestRunDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [artifactProfiles, setArtifactProfiles] = useState<ArtifactProfile[]>([]);
  const [artifactProfileId, setArtifactProfileId] = useState("");
  const load = useCallback(async () => { setLoading(true); try { setTask(await workbenchTasksApi.get(taskId)); setError(""); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务加载失败"); } finally { setLoading(false); } }, [taskId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void listArtifactProfiles().then(setArtifactProfiles).catch(() => setArtifactProfiles([]));
  }, []);
  useEffect(() => {
    const latestRun = task?.latest_run;
    if (!latestRun || !isV3TaskRun(task, latestRun) || hasV3RunAxisSummary(latestRun)) {
      setLatestRunDetail(null);
      setLatestRunDetailLoading(false);
      return;
    }
    let cancelled = false;
    setLatestRunDetail(null);
    setLatestRunDetailLoading(true);
    void api.workbench.taskRuns.get(latestRun.task_run_id)
      .then((run) => {
        if (!cancelled) setLatestRunDetail(run);
      })
      .catch(() => {
        if (!cancelled) setLatestRunDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLatestRunDetailLoading(false);
      });
    return () => { cancelled = true; };
  }, [task]);
  const run = async () => { setRunning(true); setError(""); try { const attempt = await workbenchTasksApi.createRun(taskId, "", "", artifactProfileId); await api.workbench.taskRuns.execute(attempt.task_run_id, 0, true); router.push(`/tasks/${taskId}/runs/${attempt.task_run_id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "运行启动失败"); setRunning(false); } };
  const archive = async () => { try { setTask(await workbenchTasksApi.archive(taskId)); } catch (cause) { setError(cause instanceof Error ? cause.message : "归档失败"); } };
  if (loading && !task) return <div className="ct-v2-page-loading">正在读取任务…</div>;
  if (!task) return <div className="ct-v2-empty-state is-error"><p>{error || "任务不存在"}</p><Link href="/tasks">返回任务中心</Link></div>;
  const runs = task.runs || [];
  const bindingName = task.skill_name || task.skill_id || "Skill 不可用";
  const bindingVersion = task.skill_version_id || "";
  const latestRunDetailForProjection = latestRunDetail?.task_run_id === task.latest_run?.task_run_id ? latestRunDetail : null;
  const latestRunProjection = taskRunOverviewProjection(task, task.latest_run, latestRunDetailForProjection, { loadingDetail: latestRunDetailLoading && !latestRunDetailForProjection });
  return <main className="ct-v2-task-detail">
    <header className="ct-v2-task-detail-header"><div><Link href="/tasks"><ArrowLeft size={15} />任务中心</Link><span className={`ct-v2-status is-${task.lifecycle_status}`}>{taskStatusLabel(taskLifecycleLabels, task.lifecycle_status)}</span><h1>{task.name}</h1><p>{task.description || "无任务描述"}</p></div><div>{task.lifecycle_status === "ready" && <label className="ct-v2-run-artifact-profile"><span><FileOutput size={14} />交付件档案</span><select aria-label="交付件档案" value={artifactProfileId} onChange={(event) => setArtifactProfileId(event.target.value)}><option value="">按绑定规则自动选择</option>{artifactProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.version}</option>)}</select></label>}{task.lifecycle_status === "ready" && <button className="ct-v2-primary-button" type="button" disabled={running} onClick={() => void run()}>{running ? <Loader2 className="animate-spin" size={15} /> : <Play size={15} />}{running ? "正在启动" : "启动新运行"}</button>}{task.lifecycle_status !== "archived" && <button type="button" onClick={() => void archive()}><Archive size={15} />归档</button>}</div></header>
    {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
    <nav className="ct-v2-detail-tabs" aria-label="任务详情视图">{tabs.map((item) => <button type="button" key={item} className={tab === item ? "is-active" : ""} onClick={() => setTab(item)}>{item}</button>)}</nav>
    <section className="ct-v2-task-detail-body">
      {tab === "概览" && <div className="ct-v2-overview-grid"><section><h2>任务定义</h2><DefinitionRow label="Skill" value={bindingName} detail={bindingVersion || "历史版本"} /><DefinitionRow label="工作空间" value={task.workspace_name} /><DefinitionRow label="标签" value={task.tags.join("、") || "—"} /></section><section><h2>最近运行</h2>{task.latest_run ? <><DefinitionRow label="Attempt" value={String(task.latest_run.attempt_number)} /><DefinitionRow label="执行状态" value={taskStatusLabel(taskExecutionLabels, latestRunProjection.execution)} />{latestRunProjection.kind === "v3" ? <><DefinitionRow label="产物校验" value={taskStatusLabel(taskArtifactValidationLabels, latestRunProjection.artifactValidation)} /><DefinitionRow label="专业治理" value={taskStatusLabel(taskGovernanceLabels, latestRunProjection.governance)} /><DefinitionRow label="交付状态" value={taskStatusLabel(taskDeliveryLabels, latestRunProjection.delivery)} /></> : latestRunProjection.kind === "legacy" ? <DefinitionRow label="质量 / 交付" value={`${taskStatusLabel(taskQualityLabels, latestRunProjection.quality)} / ${taskStatusLabel(taskDeliveryLabels, latestRunProjection.delivery)}`} /> : null}<Link href={`/tasks/${taskId}/runs/${task.latest_run.task_run_id}`}>打开运行驾驶舱</Link></> : <p>还没有运行记录。</p>}</section>{Boolean(task.ai_origins?.length) && <section className="ct-v2-task-ai-links"><h2>关联 AI 线程</h2><p>任务来源和围绕运行的后续分析都保留在这里。</p>{task.ai_origins?.map((origin) => <div key={`${origin.relation_type}:${origin.conversation_id}:${origin.task_run_id || origin.ai_run_id}`}><MessageSquareText size={15} /><span><strong>{origin.relation_type === "task_created_from_ai" ? "任务来源" : "运行分析"}</strong><small>{origin.task_run_id ? "关联运行记录" : "由 AI 线程创建"}</small></span><Link href={`/ai/${encodeURIComponent(origin.conversation_id)}`}>打开线程</Link></div>)}</section>}</div>}
      {tab === "运行记录" && <div className="ct-v2-attempt-list">{runs.map((item) => <article key={item.task_run_id}><span>Attempt {item.attempt_number}</span><strong>{taskStatusLabel(taskExecutionLabels, item.execution_status)}</strong><small>{formatDate(item.created_at)}</small>{item.parent_task_run_id && <em><RotateCcw size={13} />来自 {item.parent_task_run_id.slice(0, 18)}</em>}<Link href={`/tasks/${taskId}/runs/${item.task_run_id}`}>查看运行</Link></article>)}{!runs.length && <p>还没有运行记录。</p>}</div>}
      {tab === "输入" && <KeyValueRows values={task.input_values} empty="当前任务没有输入值。" />}
      {tab === "执行配置" && <div className="ct-v2-definition-sections"><section><h2>继承基线</h2><p>系统默认 → {bindingName} → 当前任务覆盖</p><DefinitionRow label="执行档位" value={task.execution_profile_id || "Skill 默认档位"} /></section><KeyValueRows values={task.execution_overrides} empty="未设置任务级覆盖，使用 Skill Version 默认配置。" /></div>}
      {tab === "输出" && <div className="ct-v2-definition-sections"><section><h2>交付件档案</h2><p>档案在启动运行时解析并冻结，Skill 声明可交付输出。</p><DefinitionRow label="当前选择" value={artifactProfiles.find((item) => item.id === artifactProfileId)?.name || "按工作空间、特性标签和本机默认规则选择"} /><Link href="/artifact-profiles">管理交付件档案</Link></section><KeyValueRows values={task.output_overrides} empty="未设置任务级输出覆盖，使用 Skill Version 输出契约。" /></div>}
      {tab === "活动记录" && <div className="ct-v2-activity-list"><DefinitionRow label="创建" value={formatDate(task.created_at)} /><DefinitionRow label="更新" value={formatDate(task.updated_at)} />{task.archived_at && <DefinitionRow label="归档" value={formatDate(task.archived_at)} />}</div>}
    </section>
  </main>;
}

function DefinitionRow({ label, value, detail }: { label: string; value: string; detail?: string }) { return <div className="ct-v2-definition-row"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function KeyValueRows({ values, empty }: { values: Record<string, unknown>; empty: string }) { const entries = Object.entries(values); return entries.length ? <div className="ct-v2-key-value-list">{entries.map(([key, value]) => <div key={key}><strong>{key}</strong><span>{typeof value === "string" ? value : JSON.stringify(value)}</span></div>)}</div> : <p className="ct-v2-detail-empty">{empty}</p>; }
function formatDate(value: string) { return value ? new Date(value).toLocaleString("zh-CN") : "—"; }

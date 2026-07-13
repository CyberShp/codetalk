"use client";

import Link from "next/link";
import { Archive, ArrowLeft, Loader2, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import type { WorkbenchTask } from "@/lib/types/task";
import { taskDeliveryLabels, taskExecutionLabels, taskLifecycleLabels, taskQualityLabels, taskStatusLabel } from "./task-status";

const tabs = ["概览", "运行记录", "输入", "执行配置", "输出", "活动记录"] as const;

export function WorkbenchTaskDetailPage({ taskId }: { taskId: string }) {
  const router = useRouter();
  const [task, setTask] = useState<WorkbenchTask | null>(null);
  const [tab, setTab] = useState<(typeof tabs)[number]>("概览");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => { setLoading(true); try { setTask(await workbenchTasksApi.get(taskId)); setError(""); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务加载失败"); } finally { setLoading(false); } }, [taskId]);
  useEffect(() => { void load(); }, [load]);
  const run = async () => { setRunning(true); setError(""); try { const attempt = await workbenchTasksApi.createRun(taskId); await api.workbench.taskRuns.execute(attempt.task_run_id, 0, true); router.push(`/tasks/${taskId}/runs/${attempt.task_run_id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "运行启动失败"); setRunning(false); } };
  const archive = async () => { try { setTask(await workbenchTasksApi.archive(taskId)); } catch (cause) { setError(cause instanceof Error ? cause.message : "归档失败"); } };
  if (loading && !task) return <div className="ct-v2-page-loading">正在读取任务…</div>;
  if (!task) return <div className="ct-v2-empty-state is-error"><p>{error || "任务不存在"}</p><Link href="/tasks">返回任务中心</Link></div>;
  const runs = task.runs || [];
  return <main className="ct-v2-task-detail">
    <header className="ct-v2-task-detail-header"><div><Link href="/tasks"><ArrowLeft size={15} />任务中心</Link><span className={`ct-v2-status is-${task.lifecycle_status}`}>{taskStatusLabel(taskLifecycleLabels, task.lifecycle_status)}</span><h1>{task.name}</h1><p>{task.description || "无任务描述"}</p></div><div>{task.lifecycle_status === "ready" && <button className="ct-v2-primary-button" type="button" disabled={running} onClick={() => void run()}>{running ? <Loader2 className="animate-spin" size={15} /> : <Play size={15} />}{running ? "正在启动" : "启动新运行"}</button>}{task.lifecycle_status !== "archived" && <button type="button" onClick={() => void archive()}><Archive size={15} />归档</button>}</div></header>
    {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
    <nav className="ct-v2-detail-tabs" aria-label="任务详情视图">{tabs.map((item) => <button type="button" key={item} className={tab === item ? "is-active" : ""} onClick={() => setTab(item)}>{item}</button>)}</nav>
    <section className="ct-v2-task-detail-body">
      {tab === "概览" && <div className="ct-v2-overview-grid"><section><h2>任务定义</h2><DefinitionRow label="工作流" value={task.workflow_name} detail={task.workflow_version_id} /><DefinitionRow label="工作空间" value={task.workspace_name} /><DefinitionRow label="标签" value={task.tags.join("、") || "—"} /></section><section><h2>最近运行</h2>{task.latest_run ? <><DefinitionRow label="Attempt" value={String(task.latest_run.attempt_number)} /><DefinitionRow label="执行状态" value={taskStatusLabel(taskExecutionLabels, task.latest_run.execution_status)} /><DefinitionRow label="质量 / 交付" value={`${taskStatusLabel(taskQualityLabels, task.latest_run.quality_status)} / ${taskStatusLabel(taskDeliveryLabels, task.latest_run.delivery_status)}`} /><Link href={`/tasks/${taskId}/runs/${task.latest_run.task_run_id}`}>打开运行驾驶舱</Link></> : <p>还没有运行记录。</p>}</section></div>}
      {tab === "运行记录" && <div className="ct-v2-attempt-list">{runs.map((item) => <article key={item.task_run_id}><span>Attempt {item.attempt_number}</span><strong>{taskStatusLabel(taskExecutionLabels, item.execution_status)}</strong><small>{formatDate(item.created_at)}</small>{item.parent_task_run_id && <em><RotateCcw size={13} />来自 {item.parent_task_run_id.slice(0, 18)}</em>}<Link href={`/tasks/${taskId}/runs/${item.task_run_id}`}>查看运行</Link></article>)}{!runs.length && <p>还没有运行记录。</p>}</div>}
      {tab === "输入" && <KeyValueRows values={task.input_values} empty="当前任务没有输入值。" />}
      {tab === "执行配置" && <div className="ct-v2-definition-sections"><section><h2>继承基线</h2><p>系统默认 → {task.workflow_name} → 当前任务覆盖</p></section><KeyValueRows values={task.execution_overrides} empty="未设置任务级覆盖，使用工作流版本默认配置。" /></div>}
      {tab === "输出" && <KeyValueRows values={task.output_overrides} empty="未设置任务级输出覆盖，使用工作流版本输出契约。" />}
      {tab === "活动记录" && <div className="ct-v2-activity-list"><DefinitionRow label="创建" value={formatDate(task.created_at)} /><DefinitionRow label="更新" value={formatDate(task.updated_at)} />{task.archived_at && <DefinitionRow label="归档" value={formatDate(task.archived_at)} />}</div>}
    </section>
  </main>;
}

function DefinitionRow({ label, value, detail }: { label: string; value: string; detail?: string }) { return <div className="ct-v2-definition-row"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function KeyValueRows({ values, empty }: { values: Record<string, unknown>; empty: string }) { const entries = Object.entries(values); return entries.length ? <div className="ct-v2-key-value-list">{entries.map(([key, value]) => <div key={key}><strong>{key}</strong><span>{typeof value === "string" ? value : JSON.stringify(value)}</span></div>)}</div> : <p className="ct-v2-detail-empty">{empty}</p>; }
function formatDate(value: string) { return value ? new Date(value).toLocaleString("zh-CN") : "—"; }

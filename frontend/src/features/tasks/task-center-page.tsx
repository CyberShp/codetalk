"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Archive, Copy, ExternalLink, History, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import { skillsApi } from "@/lib/api/skills";
import type { WorkbenchRunSummary, WorkbenchTask } from "@/lib/types/task";
import type { SkillVersion } from "@/lib/types/skill";
import type { Workspace } from "@/lib/types";
import { taskDeliveryLabels, taskExecutionLabels, taskLifecycleLabels, taskQualityLabels, taskStatusLabel } from "./task-status";

const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 25;

export function TaskCenterPage() {
  const router = useRouter();
  const search = useSearchParams();
  const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
  const [total, setTotal] = useState(0);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [skillVersions, setSkillVersions] = useState<SkillVersion[]>([]);
  const [history, setHistory] = useState<WorkbenchRunSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const searchTimer = useRef<number | null>(null);
  const query = useMemo(() => ({
    q: search.get("q") || "",
    lifecycle_status: search.get("lifecycle_status") || "",
    execution_status: search.get("execution_status") || "",
    quality_status: search.get("quality_status") || "",
    skill_id: search.get("skill_id") || "",
    workspace_id: search.get("workspace_id") || "",
    page: Math.max(1, Number(search.get("page") || "1") || 1),
    page_size: PAGE_SIZE,
  }), [search]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await workbenchTasksApi.list(query);
      setTasks(result.items); setTotal(result.total);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "任务加载失败"); }
    finally { setLoading(false); }
  }, [query]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void Promise.all([api.workspaces.list(), skillsApi.listVersions()]).then(([workspaceItems, versionResult]) => {
      setWorkspaces(workspaceItems); setSkillVersions(versionResult.items);
    }).catch(() => undefined);
  }, []);
  useEffect(() => () => {
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
  }, []);

  const setFilter = useCallback((key: string, value: string) => {
    // Read the browser URL at interaction time so rapid filter changes cannot
    // overwrite each other with a stale useSearchParams render snapshot.
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    window.location.replace(`/tasks${next.size ? `?${next.toString()}` : ""}`);
  }, []);
  const bindFilterSelect = useCallback((element: HTMLSelectElement | null) => {
    if (!element) return;
    element.onchange = () => {
      const key = element.dataset.filterKey;
      if (key) setFilter(key, element.value);
    };
  }, [setFilter]);
  const scheduleSearch = (value: string) => {
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      const next = new URLSearchParams(window.location.search);
      if (value) next.set("q", value);
      else next.delete("q");
      next.delete("page");
      router.replace(`/tasks${next.size ? `?${next.toString()}` : ""}`);
    }, SEARCH_DEBOUNCE_MS);
  };
  const archiveTask = async (taskId: string) => { try { await workbenchTasksApi.archive(taskId); await load(); } catch (cause) { setError(cause instanceof Error ? cause.message : "归档失败"); } };
  const cloneTask = async (taskId: string) => { try { const cloned = await workbenchTasksApi.clone(taskId); router.push(`/tasks/${cloned.task_id}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "复制失败"); } };
  const toggleHistory = async () => { if (history) { setHistory(null); return; } try { setHistory((await workbenchTasksApi.history()).items); } catch (cause) { setError(cause instanceof Error ? cause.message : "历史运行加载失败"); } };

  return <main className="ct-v2-library ct-v2-task-center">
    <header className="ct-v2-page-header"><div><h1>任务中心</h1></div><div className="ct-v2-page-actions"><button type="button" onClick={() => void toggleHistory()}><History size={15} />历史运行</button><Link className="ct-v2-primary-button" href="/tasks/new"><Plus size={15} />新建任务</Link></div></header>
    <section className="ct-v2-task-filters" aria-label="任务筛选">
      <label className="ct-v2-search-field"><Search size={15} /><input key={query.q} aria-label="搜索任务" defaultValue={query.q} onChange={(event) => scheduleSearch(event.target.value)} placeholder="搜索任务名称、描述或标签" /></label>
      <label><span>生命周期</span><select ref={bindFilterSelect} data-filter-key="lifecycle_status" key={`lifecycle-${query.lifecycle_status}`} defaultValue={query.lifecycle_status}><option value="">全部</option><option value="draft">草稿</option><option value="ready">就绪</option><option value="archived">已归档</option></select></label>
      <label><span>运行状态</span><select ref={bindFilterSelect} data-filter-key="execution_status" key={`execution-${query.execution_status}`} defaultValue={query.execution_status}><option value="">全部</option><option value="not_started">未运行</option><option value="prepared">已准备</option><option value="running">运行中</option><option value="completed">已完成</option><option value="failed">失败</option></select></label>
      <label><span>质量</span><select ref={bindFilterSelect} data-filter-key="quality_status" key={`quality-${query.quality_status}`} defaultValue={query.quality_status}><option value="">全部</option><option value="not_checked">未检查</option><option value="pending">检查中</option><option value="passed">通过</option><option value="warning">有警告</option><option value="blocked">已阻断</option></select></label>
      <label><span>Skill</span><select ref={bindFilterSelect} data-filter-key="skill_id" key={`skill-${query.skill_id}`} defaultValue={query.skill_id}><option value="">全部</option>{skillVersions.map((item) => <option key={item.version_id} value={item.skill_id}>{item.skill_id}</option>)}</select></label>
      <label><span>工作空间</span><select ref={bindFilterSelect} data-filter-key="workspace_id" key={`workspace-${query.workspace_id}`} defaultValue={query.workspace_id}><option value="">全部</option>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    </section>
    {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
    {history && <HistoricalRuns runs={history} />}
    <div className="ct-v2-table-summary"><span>{loading ? "正在刷新" : `${total} 个任务`}</span><span /></div>
    <div className="ct-v2-table-shell"><table className="ct-v2-table"><thead><tr><th>任务</th><th>生命周期</th><th>当前运行</th><th>Skill / Version</th><th>工作空间</th><th>质量</th><th>交付</th><th>更新时间</th><th aria-label="操作" /></tr></thead><tbody>{tasks.map((task) => <tr key={task.task_id}><td><Link href={`/tasks/${task.task_id}`}><strong>{task.name}</strong><small>{task.description || task.tags.join(" · ") || "无描述"}</small></Link></td><td><Status value={task.lifecycle_status} label={taskStatusLabel(taskLifecycleLabels, task.lifecycle_status)} /></td><td><Status value={task.latest_run?.execution_status || "not_started"} label={taskStatusLabel(taskExecutionLabels, task.latest_run?.execution_status || "not_started")} /><small>{task.latest_run ? `Attempt ${task.latest_run.attempt_number}` : "—"}</small></td><td><strong>{task.skill_name || task.skill_id}</strong><small>{task.skill_version_id}</small></td><td><span className="ct-v2-cell-text" title={task.workspace_name}>{task.workspace_name}</span></td><td>{taskStatusLabel(taskQualityLabels, task.latest_run?.quality_status || "not_checked")}</td><td>{taskStatusLabel(taskDeliveryLabels, task.latest_run?.delivery_status || "none")}</td><td>{formatTime(task.updated_at)}</td><td><div className="ct-v2-row-actions"><Link href={`/tasks/${task.task_id}`} title="打开任务"><ExternalLink size={15} /></Link><button type="button" title="复制任务" onClick={() => void cloneTask(task.task_id)}><Copy size={15} /></button>{task.lifecycle_status !== "archived" && <button type="button" title="归档任务" onClick={() => void archiveTask(task.task_id)}><Archive size={15} /></button>}</div></td></tr>)}{!loading && !tasks.length && <tr><td colSpan={9}><div className="ct-v2-table-empty">没有符合当前筛选条件的任务</div></td></tr>}</tbody></table></div>
    <Pagination page={query.page} total={total} onPage={(page) => setFilter("page", page === 1 ? "" : String(page))} />
  </main>;
}

function HistoricalRuns({ runs }: { runs: WorkbenchRunSummary[] }) { return <section className="ct-v2-history-band"><div><h2>历史运行</h2><p>这些运行创建于 Task 模型之前，仅供查看，不会被改写。</p></div><div>{runs.length ? runs.slice(0, 12).map((run) => <Link key={run.task_run_id} href={`/workbench?task_run_id=${run.task_run_id}`}><span>历史运行记录</span><strong>{taskStatusLabel(taskExecutionLabels, run.execution_status)}</strong><small>{formatTime(run.created_at)}</small></Link>) : <span>没有旧运行</span>}</div></section>; }
function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) { const pages = Math.max(1, Math.ceil(total / PAGE_SIZE)); return <nav className="ct-v2-pagination" aria-label="任务分页"><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button><span>第 {page} / {pages} 页</span><button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</button></nav>; }
function Status({ value, label }: { value: string; label?: string }) { return <span className={`ct-v2-status is-${value}`}>{label || value}</span>; }
function formatTime(value: string) { if (!value) return "—"; return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  FileArchive,
  FileText,
  FolderOpen,
  Loader2,
  PlayCircle,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import { compactMachineToken } from "@/lib/display-text";
import type { Workspace } from "@/lib/types";
import type { WorkbenchRunSummary, WorkbenchTask } from "@/lib/types/task";

type QueueTone = "danger" | "warning" | "success" | "info" | "muted";

interface QueueItem {
  id: string;
  title: string;
  meta: string;
  status: string;
  tone: QueueTone;
  href: string;
  action: string;
}

type SectionErrors = {
  workspaces?: string;
  tasks?: string;
  runs?: string;
};

interface HomeSnapshot {
  workspaces: Workspace[];
  tasks: WorkbenchTask[];
  runs: WorkbenchRunSummary[];
}

const HOME_SNAPSHOT_KEY = "codetalk.home.snapshot.v1";

const EMPTY_HOME_SNAPSHOT: HomeSnapshot = {
  workspaces: [],
  tasks: [],
  runs: [],
};

function readHomeSnapshot(): HomeSnapshot | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(HOME_SNAPSHOT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<HomeSnapshot>;
    return {
      workspaces: Array.isArray(parsed.workspaces) ? parsed.workspaces : [],
      tasks: Array.isArray(parsed.tasks) ? parsed.tasks : [],
      runs: Array.isArray(parsed.runs) ? parsed.runs : [],
    };
  } catch {
    return null;
  }
}

function writeHomeSnapshot(snapshot: HomeSnapshot): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(HOME_SNAPSHOT_KEY, JSON.stringify(snapshot));
  } catch {
    // Session storage is an optional fast-return cache; failure should not affect the page.
  }
}

function statusTone(status: string | undefined): QueueTone {
  const value = (status ?? "").toLowerCase();
  if (/(fail|error|cancel|blocked|partial)/.test(value)) return "danger";
  if (/(running|pending|ready|draft|warning|waiting)/.test(value)) return "warning";
  if (/(pass|success|complete|done)/.test(value)) return "success";
  return "info";
}

function statusLabel(status: string | undefined): string {
  const value = (status ?? "").toLowerCase();
  if (!value) return "待处理";
  if (/(fail|error)/.test(value)) return "失败";
  if (/cancel/.test(value)) return "已取消";
  if (/blocked/.test(value)) return "阻塞";
  if (/running/.test(value)) return "运行中";
  if (/pending|waiting/.test(value)) return "等待";
  if (/ready/.test(value)) return "就绪";
  if (/draft/.test(value)) return "草稿";
  if (/complete|done|pass|success/.test(value)) return "完成";
  return status ?? "待处理";
}

function runHref(run: WorkbenchRunSummary): string {
  const taskId = (run.task_id ?? "").trim();
  const runId = (run.task_run_id ?? "").trim();
  const isKnownTaskRun =
    taskId &&
    runId &&
    !run.legacy &&
    taskId !== runId &&
    !taskId.startsWith("task_run") &&
    !taskId.startsWith("knowledge_");
  if (!isKnownTaskRun) return `/workbench?task_run_id=${encodeURIComponent(run.task_run_id)}`;
  return `/tasks/${encodeURIComponent(run.task_id)}/runs/${encodeURIComponent(run.task_run_id)}`;
}

function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return "暂无时间";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return value;
  const diff = Date.now() - time;
  const minutes = Math.max(1, Math.round(diff / 60000));
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

function taskSkillLabel(task: WorkbenchTask): string {
  return task.skill_name || task.skill_id || task.skill_version_id || "未绑定 Skill";
}

function buildQueue(
  workspaces: Workspace[],
  tasks: WorkbenchTask[],
  runs: WorkbenchRunSummary[],
): QueueItem[] {
  const items: QueueItem[] = [];

  workspaces
    .filter((workspace) => workspace.indexed === -1)
    .slice(0, 2)
    .forEach((workspace) => {
      items.push({
        id: `workspace-index-${workspace.id}`,
        title: `${workspace.name} 索引失败`,
        meta: workspace.last_index_error || workspace.repo_path || "需要重新检查本地源码路径",
        status: "阻塞",
        tone: "danger",
        href: `/workspaces/${workspace.id}`,
        action: "修复索引",
      });
    });

  tasks
    .filter((task) => {
      const run = task.latest_run;
      return run && statusTone(`${run.execution_status} ${run.delivery_status}`) === "danger";
    })
    .slice(0, 3)
    .forEach((task) => {
      const run = task.latest_run;
      if (!run) return;
      items.push({
        id: `task-run-${run.task_run_id}`,
        title: `${task.name} 需要复盘`,
        meta: `${task.workspace_name || "未绑定项目"} · ${taskSkillLabel(task)}`,
        status: statusLabel(run.execution_status || run.delivery_status),
        tone: "danger",
        href: runHref(run),
        action: "进入复盘",
      });
    });

  tasks
    .filter((task) => task.lifecycle_status === "ready" && !task.latest_run)
    .slice(0, 2)
    .forEach((task) => {
      items.push({
        id: `task-ready-${task.task_id}`,
        title: `${task.name} 可以运行`,
        meta: `${task.workspace_name || "未绑定项目"} · ${taskSkillLabel(task)}`,
        status: "就绪",
        tone: "warning",
        href: `/tasks/${task.task_id}`,
        action: "准备运行",
      });
    });

  runs
    .filter((run) => statusTone(`${run.execution_status} ${run.delivery_status}`) !== "success")
    .slice(0, 2)
    .forEach((run) => {
      if (items.some((item) => item.id.includes(run.task_run_id))) return;
      items.push({
        id: `run-history-${run.task_run_id}`,
        title: "运行待处理",
        meta: `${compactMachineToken(run.workflow_id || run.task_run_id, 28)} · ${formatRelativeTime(run.started_at || run.created_at)}`,
        status: statusLabel(run.execution_status || run.delivery_status),
        tone: statusTone(`${run.execution_status} ${run.delivery_status}`),
        href: runHref(run),
        action: "进入复盘",
      });
    });

  if (workspaces.length === 0) {
    items.push({
      id: "empty-workspace",
      title: "创建第一个项目基底",
      meta: "选择本地源码目录",
      status: "待开始",
      tone: "info",
      href: "/workspaces/new",
      action: "创建项目",
    });
  }

  if (items.length === 0) {
    items.push({
      id: "empty-task",
      title: "今天没有阻塞项",
      meta: "任务队列正常",
      status: "正常",
      tone: "success",
      href: "/tasks/new",
      action: "新建任务",
    });
  }

  return items.slice(0, 5);
}

function toneClasses(tone: QueueTone): string {
  if (tone === "danger") return "bg-red-500/10 text-red-600 border-red-500/20";
  if (tone === "warning") return "bg-amber-500/10 text-amber-700 border-amber-500/20";
  if (tone === "success") return "bg-green-500/10 text-green-700 border-green-500/20";
  if (tone === "info") return "bg-primary/10 text-primary border-primary/20";
  return "bg-surface-container text-on-surface-variant border-outline-variant/40";
}

export default function WorkbenchPage() {
  const snapshotRef = useRef<HomeSnapshot>(EMPTY_HOME_SNAPSHOT);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
  const [runs, setRuns] = useState<WorkbenchRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});

  const loadData = useCallback(async () => {
    setLoading(true);
    setSectionErrors({});
    const [workspaceResult, taskResult, runResult] = await Promise.allSettled([
      api.workspaces.list(),
      workbenchTasksApi.list({ page_size: 8 }),
      workbenchTasksApi.history(),
    ]);

    const errors: SectionErrors = {};
    const nextSnapshot: HomeSnapshot = { ...snapshotRef.current };
    let hasFulfilledSection = false;
    if (workspaceResult.status === "fulfilled") {
      nextSnapshot.workspaces = workspaceResult.value;
      setWorkspaces(workspaceResult.value);
      hasFulfilledSection = true;
    } else errors.workspaces = "项目状态加载失败";

    if (taskResult.status === "fulfilled") {
      nextSnapshot.tasks = taskResult.value.items;
      setTasks(taskResult.value.items);
      hasFulfilledSection = true;
    } else errors.tasks = "任务队列加载失败";

    if (runResult.status === "fulfilled") {
      nextSnapshot.runs = runResult.value.items;
      setRuns(runResult.value.items);
      hasFulfilledSection = true;
    } else errors.runs = "运行历史加载失败";

    if (hasFulfilledSection) {
      snapshotRef.current = nextSnapshot;
      writeHomeSnapshot(nextSnapshot);
    }
    setSectionErrors(errors);
    setHasLoadedOnce(true);
    setLoading(false);
  }, []);

  useEffect(() => {
    const cachedSnapshot = readHomeSnapshot();
    queueMicrotask(() => {
      if (cachedSnapshot) {
        snapshotRef.current = cachedSnapshot;
        setWorkspaces(cachedSnapshot.workspaces);
        setTasks(cachedSnapshot.tasks);
        setRuns(cachedSnapshot.runs);
        setHasLoadedOnce(true);
      }
      void loadData();
    });
  }, [loadData]);

  const indexedWorkspaces = workspaces.filter((workspace) => workspace.indexed === 1).length;
  const failedWorkspaces = workspaces.filter((workspace) => workspace.indexed === -1).length;
  const reportCount = workspaces.reduce((total, workspace) => total + workspace.reports.length, 0);
  const activeRuns = runs.filter((run) => statusTone(run.execution_status) === "warning").length;
  const queueItems = useMemo(() => buildQueue(workspaces, tasks, runs), [runs, tasks, workspaces]);
  const currentRun = runs[0] ?? tasks.find((task) => task.latest_run)?.latest_run ?? null;
  const isInitialLoading = loading && !hasLoadedOnce;
  const isRefreshing = loading && hasLoadedOnce;

  return (
    <div className="ct-home-shell ct-home-workbench w-full px-4 pb-4 xl:px-6">
      <header className="ct-home-header mb-4 flex min-h-14 flex-col gap-3 border-b border-outline-variant/30 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-medium text-on-surface-variant">工作台</p>
          <p className="font-display text-lg font-bold text-on-surface">今日测试工作</p>
        </div>
        <div className="ct-home-actions flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
          <button
            type="button"
            onClick={loadData}
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-outline-variant/40 bg-surface-container-low px-3 text-sm font-medium text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
          >
            <RefreshCw size={15} />
            刷新
          </button>
          <Link
            href="/skills"
            data-testid="home-primary-skill"
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-outline-variant/40 bg-surface-container-low px-4 text-sm font-semibold text-on-surface"
          >
            <FileArchive size={16} />
            Skill 中心
          </Link>
          <Link
            href="/tasks/new"
            data-testid="home-primary-task"
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-on-primary"
          >
            <Plus size={16} />
            新建任务
          </Link>
          <Link
            href="/workspaces/new"
            data-testid="home-primary-project"
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-outline-variant/40 bg-surface-container-low px-4 text-sm font-semibold text-on-surface"
          >
            <FolderOpen size={16} />
            创建项目
          </Link>
        </div>
      </header>

      <section className="mb-4">
        <h1 className="font-display text-3xl font-bold text-on-surface">测试人员工作台</h1>
      </section>

      <section className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="今日状态">
        <div className="ct-home-metric rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-on-surface-variant">
            <AlertTriangle size={14} />
            待复核事项
          </div>
          <div className="ct-home-metric-value font-display text-2xl font-bold text-amber-700">{isInitialLoading ? "--" : queueItems.length}</div>
        </div>
        <div className="ct-home-metric rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-on-surface-variant">
            <PlayCircle size={14} />
            运行中
          </div>
          <div className="ct-home-metric-value font-display text-2xl font-bold text-green-700">{isInitialLoading ? "--" : activeRuns}</div>
        </div>
        <div className="ct-home-metric rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-on-surface-variant">
            <Database size={14} />
            已索引项目
          </div>
          <div className="ct-home-metric-value font-display text-2xl font-bold text-primary">{isInitialLoading ? "--" : `${indexedWorkspaces}/${workspaces.length}`}</div>
        </div>
        <div className="ct-home-metric rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
          <div className="mb-3 flex items-center gap-2 text-xs font-medium text-on-surface-variant">
            <FileText size={14} />
            沉淀报告
          </div>
          <div className="ct-home-metric-value font-display text-2xl font-bold text-on-surface">{isInitialLoading ? "--" : reportCount}</div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <section aria-label="待处理队列" className="ct-home-panel rounded-lg border border-outline-variant/30 bg-surface-container-low p-4">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
                <Activity size={17} />
                待处理队列
                {isRefreshing && <Loader2 size={14} className="animate-spin text-on-surface-variant" />}
              </h2>
            </div>
            <Link href="/tasks" className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/40 bg-surface px-3 py-2 text-sm font-medium text-on-surface hover:bg-surface-container">
              打开任务中心
              <ArrowRight size={14} />
            </Link>
          </div>

          {isInitialLoading ? (
            <div className="flex h-56 items-center justify-center text-on-surface-variant">
              <Loader2 size={22} className="animate-spin" />
            </div>
          ) : (
            <div className="space-y-2">
              {queueItems.map((item) => (
                <Link key={item.id} href={item.href} className="ct-home-queue-row grid gap-3 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2.5 hover:bg-surface-container sm:grid-cols-[minmax(0,1fr)_auto_auto]">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-on-surface" title={item.title}>{item.title}</div>
                    <div className="mt-1 truncate text-xs text-on-surface-variant" title={item.meta}>{item.meta}</div>
                  </div>
                  <span className={`inline-flex w-fit items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${toneClasses(item.tone)}`}>{item.status}</span>
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-primary">
                    {item.action}
                    <ArrowRight size={13} />
                  </span>
                </Link>
              ))}
            </div>
          )}

          {(sectionErrors.tasks || sectionErrors.runs) && (
            <p className="mt-4 text-xs text-amber-700">{[sectionErrors.tasks, sectionErrors.runs].filter(Boolean).join("；")}</p>
          )}
        </section>

        <div className="grid gap-4">
          <section aria-label="当前运行" className="ct-home-panel rounded-lg border border-outline-variant/30 bg-surface-container-low p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
                  <PlayCircle size={17} />
                  当前运行
                </h2>
              </div>
              <Link href="/workbench" className="text-xs font-semibold text-primary">运行驾驶舱</Link>
            </div>
            {currentRun ? (
              <div className="rounded-lg border border-outline-variant/30 bg-surface p-4">
                <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${toneClasses(statusTone(`${currentRun.execution_status} ${currentRun.delivery_status}`))}`}>
                  {statusLabel(currentRun.execution_status || currentRun.delivery_status)}
                </span>
                <p className="mt-3 truncate text-sm font-semibold text-on-surface">{compactMachineToken(currentRun.workflow_id || currentRun.task_id, 28)}</p>
                <p className="mt-1 text-xs text-on-surface-variant">
                  {formatRelativeTime(currentRun.started_at || currentRun.created_at)} · attempt {currentRun.attempt_number}
                </p>
                <Link href={runHref(currentRun)} className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-on-primary">
                  进入复盘
                  <ArrowRight size={14} />
                </Link>
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface p-4">
                <p className="text-sm font-medium text-on-surface">暂无运行记录</p>
              </div>
            )}
          </section>

          <section aria-label="项目基底" className="ct-home-panel rounded-lg border border-outline-variant/30 bg-surface-container-low p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
                  <FolderOpen size={17} />
                  项目基底
                </h2>
              </div>
              <Link href="/workspaces" className="text-xs font-semibold text-primary">全部项目</Link>
            </div>
            <div className="space-y-2">
              {workspaces.slice(0, 4).map((workspace) => {
                const hasPath = Boolean(workspace.repo_path.trim());
                const tone: QueueTone = !hasPath ? "muted" : workspace.indexed === 1 ? "success" : workspace.indexed === -1 ? "danger" : "warning";
                const label = !hasPath ? "未绑定" : workspace.indexed === 1 ? "已索引" : workspace.indexed === -1 ? "失败" : "索引中";
                return (
                  <Link key={workspace.id} href={`/workspaces/${workspace.id}`} className="flex items-center justify-between gap-3 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 hover:bg-surface-container">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-on-surface">{workspace.name}</span>
                      <span className="block truncate text-xs text-on-surface-variant">{hasPath ? workspace.repo_path : "未绑定本地文件夹"}</span>
                    </span>
                    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${toneClasses(tone)}`}>{label}</span>
                  </Link>
                );
              })}
              {!isInitialLoading && workspaces.length === 0 && (
                <Link href="/workspaces/new" className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-outline-variant/50 bg-surface px-3 py-6 text-sm font-semibold text-primary">
                  <Plus size={16} />
                  创建项目基底
                </Link>
              )}
            </div>
            {sectionErrors.workspaces && <p className="mt-3 text-xs text-amber-700">{sectionErrors.workspaces}</p>}
          </section>
        </div>
      </div>

      <section aria-label="证据与知识" className="ct-home-panel mt-4 rounded-lg border border-outline-variant/30 bg-surface-container-low p-4">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
              <ShieldCheck size={17} />
              证据与知识
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/evidence-library" className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/40 bg-surface px-3 py-2 text-sm font-medium text-on-surface hover:bg-surface-container">
              <Search size={14} />
              查证据
            </Link>
            <Link href="/knowledge-center" className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/40 bg-surface px-3 py-2 text-sm font-medium text-on-surface hover:bg-surface-container">
              <CheckCircle2 size={14} />
              沉淀知识
            </Link>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-outline-variant/30 bg-surface p-4">
            <p className="text-xs font-medium text-on-surface-variant">报告产物</p>
            <p className="mt-2 font-display text-xl font-bold text-on-surface">{isInitialLoading ? "--" : reportCount}</p>
          </div>
          <div className="rounded-lg border border-outline-variant/30 bg-surface p-4">
            <p className="text-xs font-medium text-on-surface-variant">索引异常</p>
            <p className="mt-2 font-display text-xl font-bold text-red-600">{isInitialLoading ? "--" : failedWorkspaces}</p>
          </div>
          <div className="rounded-lg border border-outline-variant/30 bg-surface p-4">
            <p className="text-xs font-medium text-on-surface-variant">下一步</p>
            <p className="mt-2 text-sm font-semibold text-on-surface">
              {failedWorkspaces > 0 ? "先修复索引失败项目" : (queueItems[0]?.action ?? "新建任务")}
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

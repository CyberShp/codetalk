"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clipboard,
  Download,
  FileText,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Search,
  Square,
  Wrench,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, currentApiBase } from "@/lib/api";
import type {
  PreparedWorkbenchTaskRun,
  WorkbenchRunUiNodeSummary,
  WorkbenchTaskArtifact,
  WorkbenchTaskRunEvent,
} from "@/lib/types";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import type { WorkbenchTask } from "@/lib/types/task";
import {
  taskDeliveryLabels,
  taskExecutionLabels,
  taskQualityLabels,
  taskStatusLabel,
} from "@/features/tasks/task-status";

const tabs = ["摘要", "实时输出", "工具调用", "全部事件"] as const;
const terminalStatuses = new Set(["completed", "success", "failed", "error", "cancelled", "interrupted"]);
const MAX_LOADED_EVENTS = 2000;
const EVENT_PAGE_SIZE = 1000;

export function RunCockpitPage({ taskId, runId }: { taskId: string; runId: string }) {
  const [task, setTask] = useState<WorkbenchTask | null>(null);
  const [run, setRun] = useState<PreparedWorkbenchTaskRun | null>(null);
  const [events, setEvents] = useState<WorkbenchTaskRunEvent[]>([]);
  const [artifacts, setArtifacts] = useState<WorkbenchTaskArtifact[]>([]);
  const [tab, setTab] = useState<(typeof tabs)[number]>("摘要");
  const [query, setQuery] = useState("");
  const [nodeFilter, setNodeFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [paused, setPaused] = useState(false);
  const [pauseBoundary, setPauseBoundary] = useState<number | null>(null);
  const [frozenEvents, setFrozenEvents] = useState<WorkbenchTaskRunEvent[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [preview, setPreview] = useState<{ path: string; content: string; truncated: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlderEvents, setLoadingOlderEvents] = useState(false);
  const [error, setError] = useState("");
  const eventViewport = useRef<HTMLDivElement>(null);
  const lastEventId = useRef(0);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextTask, nextRun, eventResult, artifactResult] = await Promise.all([
        workbenchTasksApi.get(taskId),
        api.workbench.taskRuns.get(runId),
        api.workbench.taskRuns.events(runId, { tail: true, limit: EVENT_PAGE_SIZE }),
        api.workbench.taskRuns.artifacts(runId),
      ]);
      if (nextRun.task_id && nextRun.task_id !== taskId) throw new Error("该运行不属于当前任务");
      setTask(nextTask);
      setRun(nextRun);
      setEvents((current) => mergeEvents(current, eventResult.items));
      setHasOlderEvents(Boolean(eventResult.has_older));
      setArtifacts(artifactResult.artifacts);
      lastEventId.current = eventResult.latest_event_id;
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "运行信息加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [runId, taskId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!run || terminalStatuses.has(statusOf(run))) return;
    const stream = new EventSource(
      `${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/events/stream?after_id=${lastEventId.current}`,
      { withCredentials: true },
    );
    const onEvent = (raw: MessageEvent<string>) => {
      const item = JSON.parse(raw.data) as WorkbenchTaskRunEvent;
      lastEventId.current = Math.max(lastEventId.current, item.event_id);
      setEvents((current) => mergeEvents(current, [item]));
    };
    const onDone = () => { stream.close(); void refresh(true); };
    stream.addEventListener("task_run_event", onEvent as EventListener);
    stream.addEventListener("task_run_done", onDone);
    stream.onerror = () => { void refresh(true); };
    return () => stream.close();
  }, [refresh, run, runId]);

  const visibleEvents = useMemo(() => {
    const eventSource = paused ? frozenEvents : events;
    return eventSource.filter((item) => {
      if (pauseBoundary !== null && item.event_id > pauseBoundary) return false;
      const text = `${eventMessage(item)} ${eventDetail(item)}`.toLowerCase();
      const node = eventNode(item);
      if (query && !text.includes(query.toLowerCase())) return false;
      if (nodeFilter && node !== nodeFilter) return false;
      if (kindFilter && item.event_kind !== kindFilter) return false;
      if (tab === "实时输出" && !["output", "thinking", "reasoning", "error"].includes(item.event_kind)) return false;
      if (tab === "工具调用" && !["tool_use", "tool_result"].includes(item.event_kind)) return false;
      return true;
    });
  }, [events, frozenEvents, kindFilter, nodeFilter, pauseBoundary, paused, query, tab]);
  useEffect(() => {
    if (!paused && autoScroll && eventViewport.current) eventViewport.current.scrollTop = eventViewport.current.scrollHeight;
  }, [autoScroll, paused, visibleEvents]);

  const togglePaused = () => {
    if (paused) {
      setPaused(false);
      setPauseBoundary(null);
      setFrozenEvents([]);
      return;
    }
    setPauseBoundary(events.at(-1)?.event_id ?? lastEventId.current);
    setFrozenEvents(events);
    setPaused(true);
  };

  if (loading && !run) return <div className="ct-v2-page-loading"><Loader2 className="animate-spin" />正在打开运行驾驶舱…</div>;
  if (!run || !task) return <div className="ct-v2-empty-state is-error"><AlertTriangle /><h1>无法打开运行</h1><p>{error || "运行不存在"}</p><Link href={`/tasks/${taskId}`}>返回任务</Link></div>;

  const summary = run.run_ui_summary;
  const currentNode = summary?.current_node;
  const status = statusOf(run);
  const running = ["queued", "running", "prepared"].includes(status);
  const failed = ["failed", "error", "interrupted"].includes(status);
  const duration = formatDuration(run.started_at || run.runtime?.started_at, run.completed_at || run.runtime?.completed_at);
  const nodeNames = [...new Set(events.map(eventNode).filter(Boolean))];
  const kinds = [...new Set(events.map((item) => item.event_kind).filter(Boolean))];
  const publicArtifacts = artifacts.filter((item) => item.audience !== "diagnostic");
  const deliverables = publicArtifacts.filter((item) => item.audience === "deliverable");

  const cancel = async () => {
    setActionBusy(true);
    try { await api.workbench.taskRuns.cancel(runId); await refresh(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "取消运行失败"); }
    finally { setActionBusy(false); }
  };
  const retry = async () => {
    setActionBusy(true);
    try {
      const attempt = await workbenchTasksApi.createRun(taskId, runId);
      await api.workbench.taskRuns.execute(attempt.task_run_id, 0, true);
      window.location.assign(`/tasks/${taskId}/runs/${attempt.task_run_id}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "创建重试运行失败"); setActionBusy(false); }
  };
  const openArtifact = async (path: string) => {
    try {
      const content = await api.workbench.taskRuns.artifactContent(runId, path);
      setPreview({ path, content: content.content, truncated: content.truncated });
    } catch (cause) { setError(cause instanceof Error ? cause.message : "产物预览失败"); }
  };
  const loadOlderEvents = async () => {
    const firstEventId = events[0]?.event_id;
    if (!firstEventId || !hasOlderEvents || loadingOlderEvents) return;
    setLoadingOlderEvents(true);
    try {
      const result = await api.workbench.taskRuns.events(runId, {
        before_id: firstEventId,
        limit: EVENT_PAGE_SIZE,
      });
      setEvents((current) => mergeEvents(current, result.items, "older"));
      setHasOlderEvents(Boolean(result.has_older));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更早事件加载失败");
    } finally {
      setLoadingOlderEvents(false);
    }
  };

  return <main className="ct-v2-run-cockpit">
    <header className="ct-v2-run-header">
      <div className="ct-v2-run-identity"><Link href={`/tasks/${taskId}`} aria-label="返回任务"><ArrowLeft size={16} /></Link><div><span>Attempt {run.attempt_number || 1}</span><h1>{task.name}</h1></div></div>
      <StatusBlock label="执行状态" value={taskStatusLabel(taskExecutionLabels, status)} tone={status} />
      <StatusBlock label="质量状态" value={taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")} tone={run.quality_status || "not_checked"} />
      <StatusBlock label="交付状态" value={taskStatusLabel(taskDeliveryLabels, run.delivery_status || "none")} tone={run.delivery_status || "none"} />
      <div className="ct-v2-run-metric"><span>耗时</span><strong>{duration}</strong></div>
      <div className="ct-v2-run-metric"><span>当前节点</span><strong>{currentNode?.label || "等待调度"}</strong></div>
      <div className="ct-v2-run-actions">{running && <button type="button" disabled={actionBusy} onClick={() => void cancel()}><Square size={14} />取消</button>}</div>
    </header>
    {error && <div className="ct-v2-run-error" role="alert"><AlertTriangle size={15} />{error}<button aria-label="关闭错误" onClick={() => setError("")}><X size={14} /></button></div>}

    <section className="ct-v2-run-workspace">
      <div className="ct-v2-run-main">
        <nav className="ct-v2-run-tabs" role="tablist">{tabs.map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? "is-active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>
        {tab === "摘要" ? <RunSummary summary={summary} events={events} failed={failed} /> : <>
          <div className="ct-v2-event-toolbar"><label><Search size={13} /><input aria-label="搜索运行事件" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索输出" /></label><select aria-label="按节点筛选" value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)}><option value="">全部节点</option>{nodeNames.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="按类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="">全部类型</option>{kinds.map((item) => <option key={item}>{item}</option>)}</select><button title="暂停或继续显示" onClick={togglePaused}>{paused ? <Play size={14} /> : <Pause size={14} />}{paused ? "继续" : "暂停"}</button><button title="自动跟随最新输出" className={autoScroll ? "is-active" : ""} onClick={() => setAutoScroll((value) => !value)}>自动滚动</button><button title="复制当前事件" onClick={() => void navigator.clipboard.writeText(visibleEvents.map(eventClipboardLine).join("\n"))}><Clipboard size={14} /></button></div>
          <div className="ct-v2-event-viewport" ref={eventViewport} onScroll={(event) => { const target = event.currentTarget; if (target.scrollHeight - target.scrollTop - target.clientHeight > 36) setAutoScroll(false); }}>
            {hasOlderEvents && <button className="ct-v2-event-load-older" type="button" disabled={loadingOlderEvents} onClick={() => void loadOlderEvents()}>{loadingOlderEvents ? "正在加载…" : "加载更早事件"}</button>}
            {paused && <div className="ct-v2-event-empty">显示已冻结在当前时刻，后台运行不受影响。</div>}
            {visibleEvents.length ? visibleEvents.map((item) => <EventRow key={item.event_id} item={item} />) : <div className="ct-v2-event-empty">暂无符合条件的公开事件。</div>}
          </div>
        </>}
      </div>
      <NodeInspector node={currentNode} />
    </section>

    <section className="ct-v2-run-results">
      {failed && <FailurePanel summary={summary} onRetry={() => void retry()} busy={actionBusy} />}
      <section className="ct-v2-run-deliverables"><header><div><h2>交付件</h2><span>{deliverables.length} 个可交付文件</span></div></header><div>{deliverables.length ? deliverables.map((item) => <ArtifactRow key={item.relative_path} item={item} runId={runId} onOpen={openArtifact} />) : <p>运行完成后，用户可下载的最终文件会显示在这里。</p>}</div></section>
      <section className="ct-v2-run-quality"><h2>质量结果</h2><strong>{taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")}</strong><p>{qualityMessage(run)}</p></section>
      <details className="ct-v2-run-support"><summary>支撑文件与输入快照（{publicArtifacts.length - deliverables.length}）</summary>{publicArtifacts.filter((item) => item.audience !== "deliverable").map((item) => <ArtifactRow key={item.relative_path} item={item} runId={runId} onOpen={openArtifact} />)}</details>
    </section>

    <button className="ct-v2-diagnostic-trigger" type="button" onClick={() => setDiagnosticsOpen(true)}><Wrench size={14} />技术诊断</button>
    <aside className={`ct-v2-diagnostic-drawer ${diagnosticsOpen ? "is-open" : ""}`} hidden={!diagnosticsOpen} aria-label="技术诊断"><header><h2>技术诊断</h2><button aria-label="关闭技术诊断" onClick={() => setDiagnosticsOpen(false)}><X size={16} /></button></header><a href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/diagnostic-package`}><Download size={14} />下载脱敏诊断包</a><details><summary>运行快照</summary><pre>{JSON.stringify(run, null, 2)}</pre></details><details><summary>原始公开事件</summary><pre>{JSON.stringify(events, null, 2)}</pre></details><details><summary>诊断产物</summary>{artifacts.filter((item) => item.audience === "diagnostic").map((item) => <span key={item.relative_path}>{item.relative_path}</span>)}</details></aside>
    {preview && <div className="ct-v2-artifact-modal" role="dialog" aria-modal="true" aria-label="产物预览"><section><header><FileText size={15} /><strong>{preview.path}</strong><button aria-label="关闭产物预览" onClick={() => setPreview(null)}><X size={16} /></button></header><pre>{preview.content}</pre>{preview.truncated && <p>内容较长，预览已截断，请下载完整文件。</p>}</section></div>}
  </main>;
}

function StatusBlock({ label, value, tone }: { label: string; value: string; tone: string }) { return <div className="ct-v2-run-status"><span>{label}</span><strong className={`is-${tone}`}>{value}</strong></div>; }
function RunSummary({ summary, events, failed }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; events: WorkbenchTaskRunEvent[]; failed: boolean }) { return <div className="ct-v2-run-summary"><section><h2>{failed ? "运行在节点处停止" : "节点进度"}</h2><div className="ct-v2-node-timeline">{(summary?.nodes || []).map((node) => <div key={node.id} className={`is-${node.status || "prepared"}`}><span>{node.status === "completed" || node.status === "success" ? <CheckCircle2 size={15} /> : node.status === "running" ? <Loader2 className="animate-spin" size={15} /> : node.status === "failed" || node.status === "error" ? <AlertTriangle size={15} /> : <i />}</span><strong>{node.label}</strong><small>{node.status_label}</small></div>)}</div></section><section><h2>最新活动</h2>{events.slice(-8).reverse().map((item) => <EventRow key={item.event_id} item={item} compact />)}</section></div>; }
function NodeInspector({ node }: { node?: WorkbenchRunUiNodeSummary }) { return <aside className="ct-v2-node-inspector"><header><span>运行上下文</span><h2>当前节点</h2></header>{node ? <div><strong>{node.label}</strong><small>{node.type} · {node.status_label}</small><InspectorGroup label="执行器" values={[node.executor_label || node.provider || "系统内置"]} /><InspectorGroup label="Skills" values={(node.skills || []).map((item) => item.label || item.id)} /><InspectorGroup label="MCP" values={node.mcp_profiles || []} /><InspectorGroup label="输入绑定" values={(node.inputs || []).map((item) => item.role || item.id)} /><InspectorGroup label="预期输出" values={(node.outputs || []).map((item) => item.artifact || item.id)} /></div> : <p>等待调度第一个节点。</p>}</aside>; }
function InspectorGroup({ label, values }: { label: string; values: string[] }) { return <section><h3>{label}</h3>{values.length ? values.map((item) => <span key={item}>{item}</span>) : <small>无</small>}</section>; }
function FailurePanel({ summary, onRetry, busy }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; onRetry: () => void; busy: boolean }) { const failure = summary?.failure; const node = summary?.nodes.find((item) => item.id === failure?.failed_node_id); return <section className="ct-v2-run-failure"><AlertTriangle size={18} /><div><h2>{node?.label || "运行节点"}执行失败</h2><p>{failure?.reasons?.[0] || "执行器未完成当前节点，请查看公开事件或技术诊断。"}</p><dl><div><dt>失败类型</dt><dd>{node?.type || "执行错误"}</dd></div><div><dt>是否可重试</dt><dd>{failure?.can_retry ? "可以" : "需要修改配置"}</dd></div><div><dt>重试范围</dt><dd>新建 Attempt，复用冻结输入和成功上游产物，从失败节点继续</dd></div></dl></div>{failure?.can_retry && <button disabled={busy} onClick={onRetry}><RefreshCw size={14} />从失败节点重试</button>}</section>; }
function ArtifactRow({ item, runId, onOpen }: { item: WorkbenchTaskArtifact; runId: string; onOpen: (path: string) => void }) { const path = item.relative_path || item.path; const encoded = path.split("/").map(encodeURIComponent).join("/"); return <article className="ct-v2-artifact-row"><FileText size={15} /><button onClick={() => void onOpen(path)}><strong>{path.split("/").pop()}</strong><small>{formatBytes(item.size_bytes)} · {item.kind}</small></button><a title="下载文件" href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/artifacts/download/${encoded}`}><Download size={15} /></a></article>; }
function EventRow({ item, compact = false }: { item: WorkbenchTaskRunEvent; compact?: boolean }) { return <article className={`ct-v2-event-row is-${item.event_kind} ${compact ? "is-compact" : ""}`}><time>{new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{eventNode(item) || "系统"}</span><em>{eventKindLabel(item.event_kind)}</em><div><strong>{eventMessage(item)}</strong>{!compact && eventDetail(item) && <pre>{eventDetail(item)}</pre>}</div></article>; }
function mergeEvents(current: WorkbenchTaskRunEvent[], incoming: WorkbenchTaskRunEvent[], direction: "live" | "older" = "live") { const map = new Map(current.map((item) => [item.event_id, item])); incoming.forEach((item) => map.set(item.event_id, item)); const ordered = [...map.values()].sort((a, b) => a.event_id - b.event_id); return direction === "older" ? ordered.slice(0, MAX_LOADED_EVENTS) : ordered.slice(-MAX_LOADED_EVENTS); }
function statusOf(run: PreparedWorkbenchTaskRun) { return String(run.execution_status || run.runtime?.status || run.status || "prepared").toLowerCase(); }
function eventNode(item: WorkbenchTaskRunEvent) { return String(item.payload.step_id || item.payload.node_id || item.payload.node_label || ""); }
function eventMessage(item: WorkbenchTaskRunEvent) { return String(item.payload.user_message || item.payload.message || eventTypeLabel(item.event_type)); }
function eventDetail(item: WorkbenchTaskRunEvent) { const value = item.payload.text ?? item.payload.output ?? item.payload.error ?? item.payload.detail ?? ""; return typeof value === "string" ? value : value ? JSON.stringify(value, null, 2) : ""; }
function eventClipboardLine(item: WorkbenchTaskRunEvent) { return `[${new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}] ${eventNode(item) || "系统"} ${eventMessage(item)} ${eventDetail(item)}`.trim(); }
function eventKindLabel(kind: string) { return ({ status: "状态", done: "完成", artifact: "产物", output: "输出", error: "错误", thinking: "思考", reasoning: "推理", diagnostic: "诊断", trace: "跟踪", tool_use: "工具调用", tool_result: "工具结果" } as Record<string, string>)[kind] || kind; }
function eventTypeLabel(type: string) { return ({ queued: "已进入运行队列", running: "运行已开始", step_started: "节点开始执行", step_completed: "节点执行完成", step_failed: "节点执行失败", node_reused: "已复用父运行的成功节点", completed: "运行已完成", cancelled: "运行已取消", artifact_created: "产物已生成", agent_output: "执行器产生新输出" } as Record<string, string>)[type] || type.replaceAll("_", " "); }
function qualityMessage(run: PreparedWorkbenchTaskRun) { const status = run.quality_status || "not_checked"; if (status === "passed") return "结构化质量检查已通过。"; if (status === "warning") return "产物已生成，但仍有质量警告需要复核。"; if (status === "blocked") return "质量门禁未通过，请先修复阻断项。"; if (status === "pending") return "正在检查产物完整性和质量。"; return "本次运行尚未执行质量检查。"; }
function formatDuration(start?: string, end?: string) { if (!start) return "—"; const milliseconds = Math.max(0, new Date(end || Date.now()).getTime() - new Date(start).getTime()); const seconds = Math.floor(milliseconds / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }

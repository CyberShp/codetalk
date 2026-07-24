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
  MessageSquareText,
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
import {
  formatStageAttemptLabel,
  selectStageAttemptStart,
  selectStageProgressEvent,
} from "@/features/runs/stage-progress-event";

const tabs = ["摘要", "实时输出", "工具调用", "全部事件"] as const;
const terminalStatuses = new Set(["completed", "partial", "success", "failed", "error", "cancelled", "interrupted"]);
const lifecycleEventTypes = new Set([
  "queued", "running", "step_started", "node_started", "step_completed", "node_completed",
  "step_failed", "node_failed", "provider_readiness_blocked", "completed", "partial", "failed", "error", "cancelled", "interrupted",
]);
const MAX_LOADED_EVENTS = 2000;
const EVENT_PAGE_SIZE = 1000;

type MindmapNode = {
  id: string;
  type: string;
  title: string;
  summary?: string;
  priority?: string;
  status?: string;
  parent_id?: string | null;
  evidence_refs?: string[];
  trace_refs?: Record<string, string[]>;
};

type MindmapDocument = {
  schema_version: string;
  status: string;
  default_expand_depth?: number;
  nodes: MindmapNode[];
};

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
  const [selectedNodeId, setSelectedNodeId] = useState("");
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
      setRun(applyLifecycleEvents(nextRun, eventResult.items));
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
      setRun((current) => current ? applyLifecycleEvents(current, [item]) : current);
      if (lifecycleEventTypes.has(item.event_type)) void refresh(true);
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
  const inspectedNode = summary?.nodes.find((item) => item.id === selectedNodeId) || currentNode;
  const status = statusOf(run);
  const running = ["queued", "running", "prepared"].includes(status);
  const failed = ["failed", "error", "interrupted"].includes(status);
  const partial = status === "partial";
  const diagnosticTrial = Boolean(
    run.task_bundle?.diagnostic
    && typeof run.task_bundle.diagnostic === "object"
    && (run.task_bundle.diagnostic as { not_a_formal_delivery?: unknown }).not_a_formal_delivery === true,
  );
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
  const discussRun = async () => {
    setActionBusy(true);
    try {
      const result = await api.aiConversations.openForTaskRun(runId);
      window.location.assign(`/ai/${encodeURIComponent(result.conversation.id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法打开关联 AI 线程");
      setActionBusy(false);
    }
  };
  const openArtifact = async (path: string) => {
    try {
      const content = path.endsWith("test_design_mindmap.json")
        ? await api.workbench.taskRuns.artifactContent(runId, path, 2_000_000)
        : await api.workbench.taskRuns.artifactContent(runId, path);
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
      <div className="ct-v2-run-identity"><Link href={`/tasks/${taskId}`} aria-label="返回任务"><ArrowLeft size={16} /></Link><div><span>Attempt {run.attempt_number || 1}{diagnosticTrial ? " · 节点诊断运行" : ""}</span><h1>{task.name}</h1>{diagnosticTrial && <small>仅验证此节点的真实执行链，不计入正式质量与交付。</small>}</div></div>
      <StatusBlock label="执行状态" value={taskStatusLabel(taskExecutionLabels, status)} tone={status} />
      <StatusBlock label="质量状态" value={taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")} tone={run.quality_status || "not_checked"} />
      <StatusBlock label="交付状态" value={taskStatusLabel(taskDeliveryLabels, run.delivery_status || "none")} tone={run.delivery_status || "none"} />
      <div className="ct-v2-run-metric"><span>耗时</span><RunDuration start={run.started_at || run.runtime?.started_at} end={run.completed_at || run.runtime?.completed_at} active={running} /></div>
      <div className="ct-v2-run-metric"><span>当前节点</span><strong>{displayNodeName(currentNode?.label || currentNode?.id || "等待调度")}</strong></div>
      <div className="ct-v2-run-actions">
        <button type="button" disabled={actionBusy} onClick={() => void discussRun()}><MessageSquareText size={14} />围绕本次运行继续分析</button>
        {running && <button type="button" disabled={actionBusy} onClick={() => void cancel()}><Square size={14} />取消</button>}
      </div>
    </header>
    {error && <div className="ct-v2-run-error" role="alert"><AlertTriangle size={15} />{error}<button aria-label="关闭错误" onClick={() => setError("")}><X size={14} /></button></div>}

    <section className="ct-v2-run-workspace">
      <div className="ct-v2-run-main">
        <nav className="ct-v2-run-tabs" role="tablist">{tabs.map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? "is-active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>
        {tab === "摘要" ? <RunSummary summary={summary} events={events} failed={failed} partial={partial} selectedNodeId={inspectedNode?.id || ""} onSelectNode={setSelectedNodeId} onRetry={() => void retry()} actionBusy={actionBusy} /> : <>
          <div className="ct-v2-event-toolbar"><label><Search size={13} /><input aria-label="搜索运行事件" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索输出" /></label><select aria-label="按节点筛选" value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)}><option value="">全部节点</option>{nodeNames.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="按类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="">全部类型</option>{kinds.map((item) => <option key={item}>{item}</option>)}</select><button title="暂停或继续显示" onClick={togglePaused}>{paused ? <Play size={14} /> : <Pause size={14} />}{paused ? "继续" : "暂停"}</button><button title="自动跟随最新输出" className={autoScroll ? "is-active" : ""} onClick={() => setAutoScroll((value) => !value)}>自动滚动</button><button title="复制当前事件" onClick={() => void navigator.clipboard.writeText(visibleEvents.map(eventClipboardLine).join("\n"))}><Clipboard size={14} /></button></div>
          <div className="ct-v2-event-viewport" ref={eventViewport} onScroll={(event) => { const target = event.currentTarget; if (target.scrollHeight - target.scrollTop - target.clientHeight > 36) setAutoScroll(false); }}>
            {hasOlderEvents && <button className="ct-v2-event-load-older" type="button" disabled={loadingOlderEvents} onClick={() => void loadOlderEvents()}>{loadingOlderEvents ? "正在加载…" : "加载更早事件"}</button>}
            {paused && <div className="ct-v2-event-empty">显示已冻结在当前时刻，后台运行不受影响。</div>}
            {visibleEvents.length ? (tab === "工具调用" ? pairedToolCalls(visibleEvents).map((item) => <ToolCallRow key={item.id} item={item} />) : visibleEvents.map((item) => <EventRow key={item.event_id} item={item} />)) : <div className="ct-v2-event-empty">暂无符合条件的公开事件。</div>}
          </div>
        </>}
      </div>
      <NodeInspector node={inspectedNode} />
    </section>

    <section className="ct-v2-run-results">
      {failed && <FailurePanel summary={summary} onRetry={() => void retry()} busy={actionBusy} />}
      <section className="ct-v2-run-deliverables"><header><div><h2>交付件</h2><span>{deliverables.length} 个可交付文件</span></div></header><div>{deliverables.length ? deliverables.map((item) => <ArtifactRow key={item.relative_path} item={item} runId={runId} onOpen={openArtifact} />) : <p>运行完成后，用户可下载的最终文件会显示在这里。</p>}</div></section>
      <QualityPanel run={run} onRetry={() => void retry()} busy={actionBusy} />
      <details className="ct-v2-run-support"><summary>支撑文件与输入快照（{publicArtifacts.length - deliverables.length}）</summary>{publicArtifacts.filter((item) => item.audience !== "deliverable").map((item) => <ArtifactRow key={item.relative_path} item={item} runId={runId} onOpen={openArtifact} />)}</details>
    </section>

    <button className="ct-v2-diagnostic-trigger" type="button" onClick={() => setDiagnosticsOpen(true)}><Wrench size={14} />技术诊断</button>
    <aside className={`ct-v2-diagnostic-drawer ${diagnosticsOpen ? "is-open" : ""}`} hidden={!diagnosticsOpen} aria-label="技术诊断"><header><h2>技术诊断</h2><button aria-label="关闭技术诊断" onClick={() => setDiagnosticsOpen(false)}><X size={16} /></button></header><a href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/diagnostic-package`}><Download size={14} />下载脱敏诊断包</a><details><summary>运行快照</summary><pre>{JSON.stringify(run, null, 2)}</pre></details><details><summary>原始公开事件</summary><pre>{JSON.stringify(events, null, 2)}</pre></details><details><summary>诊断产物</summary>{artifacts.filter((item) => item.audience === "diagnostic").map((item) => <span key={item.relative_path}>{item.relative_path}</span>)}</details></aside>
    {preview && <div className="ct-v2-artifact-modal" role="dialog" aria-modal="true" aria-label="产物预览"><section><header><FileText size={15} /><strong>{artifactDisplayName(preview.path)}</strong><button aria-label="关闭产物预览" onClick={() => setPreview(null)}><X size={16} /></button></header>{preview.path.endsWith("test_design_mindmap.json") ? <TestDesignMindmapPreview content={preview.content} /> : <pre>{preview.content}</pre>}{preview.truncated && <p>内容较长，预览已截断，请下载完整文件。</p>}</section></div>}
  </main>;
}

function TestDesignMindmapPreview({ content }: { content: string }) {
  const document = useMemo<MindmapDocument | null>(() => {
    try {
      const parsed = JSON.parse(content) as MindmapDocument;
      return parsed.schema_version === "test-design-mindmap-v1" && Array.isArray(parsed.nodes)
        ? parsed
        : null;
    } catch {
      return null;
    }
  }, [content]);
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("");
  const [nodeType, setNodeType] = useState("");
  const [status, setStatus] = useState("");
  const [maxDepth, setMaxDepth] = useState(2);
  const [selectedId, setSelectedId] = useState("");
  const nodes = useMemo(() => document?.nodes ?? [], [document]);
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const depthOf = useCallback((node: MindmapNode) => {
    let depth = 0;
    let parentId = node.parent_id;
    const visited = new Set<string>();
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      depth += 1;
      parentId = byId.get(parentId)?.parent_id;
    }
    return depth;
  }, [byId]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return nodes.filter((node) => {
      if (depthOf(node) > maxDepth) return false;
      if (priority && node.priority !== priority) return false;
      if (nodeType && node.type !== nodeType) return false;
      if (status && node.status !== status) return false;
      return !normalized || `${node.title} ${node.summary ?? ""}`.toLowerCase().includes(normalized);
    });
  }, [depthOf, maxDepth, nodeType, nodes, priority, query, status]);
  const selected = byId.get(selectedId) ?? visible[0];
  if (!document) return <div className="ct-v2-mindmap-invalid"><AlertTriangle size={16} />脑图 JSON 无法解析，请下载诊断文件查看。</div>;
  const types = [...new Set(nodes.map((node) => node.type))].sort();
  return <div className="ct-v2-mindmap-preview">
    <div className="ct-v2-mindmap-toolbar">
      <label><Search size={13} /><input aria-label="搜索脑图节点" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索脑图节点" /></label>
      <select aria-label="按脑图优先级筛选" value={priority} onChange={(event) => setPriority(event.target.value)}><option value="">全部优先级</option><option>P0</option><option>P1</option><option>P2</option></select>
      <select aria-label="按脑图节点类型筛选" value={nodeType} onChange={(event) => setNodeType(event.target.value)}><option value="">全部节点类型</option>{types.map((type) => <option key={type}>{type}</option>)}</select>
      <select aria-label="按脑图状态筛选" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option>READY</option><option>PARTIAL</option><option>BLOCKED</option></select>
      <button type="button" onClick={() => setMaxDepth(99)}>展开全部</button>
      <button type="button" onClick={() => setMaxDepth(document.default_expand_depth ?? 2)}>折叠到两层</button>
    </div>
    <div className="ct-v2-mindmap-body">
      <div className="ct-v2-mindmap-tree" aria-label="测试设计脑图节点">
        {visible.map((node) => <button type="button" key={node.id} className={`is-${String(node.status || "PARTIAL").toLowerCase()} ${selected?.id === node.id ? "is-selected" : ""}`} style={{ paddingLeft: `${12 + depthOf(node) * 20}px` }} onClick={() => setSelectedId(node.id)}><strong>{node.title}</strong><small>{node.type} · {node.priority || "P2"} · {node.status || "PARTIAL"}</small></button>)}
        {!visible.length && <p>没有符合筛选条件的节点。</p>}
      </div>
      <aside className="ct-v2-mindmap-detail">
        {selected ? <><span>{selected.type} · {selected.priority || "P2"}</span><h2>{selected.title}</h2><p>{selected.summary || "暂无摘要"}</p><MindmapRefs title="源码证据" values={selected.evidence_refs || []} /><MindmapRefs title="SFMEA / 测试追溯" values={Object.entries(selected.trace_refs || {}).flatMap(([key, values]) => values.map((value) => `${key}: ${value}`))} /></> : <p>选择节点查看源码证据和测试追溯。</p>}
      </aside>
    </div>
  </div>;
}

function MindmapRefs({ title, values }: { title: string; values: string[] }) {
  return <section><h3>{title}</h3>{values.length ? values.map((value) => <code key={value}>{value}</code>) : <small>无</small>}</section>;
}

function StatusBlock({ label, value, tone }: { label: string; value: string; tone: string }) { return <div className="ct-v2-run-status"><span>{label}</span><strong className={`is-${tone}`}>{value}</strong></div>; }
function RunSummary({ summary, events, failed, partial, selectedNodeId, onSelectNode, onRetry, actionBusy }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; events: WorkbenchTaskRunEvent[]; failed: boolean; partial: boolean; selectedNodeId: string; onSelectNode: (id: string) => void; onRetry: () => void; actionBusy: boolean }) { return <div className="ct-v2-run-summary"><section><h2>{failed ? "运行在节点处停止" : partial ? "运行保留了部分结果" : "节点进度"}</h2><div className="ct-v2-node-timeline">{(summary?.nodes || []).map((node) => { const nodeName = displayNodeName(node.label || node.id); return <button type="button" key={node.id} className={`is-${node.status || "prepared"} ${selectedNodeId === node.id ? "is-selected" : ""}`} onClick={() => onSelectNode(node.id)} aria-label={`查看节点 ${nodeName}`}><span>{node.status === "completed" || node.status === "success" ? <CheckCircle2 size={15} /> : node.status === "running" ? <Loader2 className="animate-spin" size={15} /> : node.status === "failed" || node.status === "error" ? <AlertTriangle size={15} /> : <i />}</span><strong>{nodeName}</strong><small>{node.status_label}</small></button>; })}</div></section><StageProgressPanel events={events} runPartial={partial} onRetry={onRetry} busy={actionBusy} /><section><h2>最新活动</h2>{events.slice(-8).reverse().map((item) => <EventRow key={item.event_id} item={item} compact />)}</section></div>; }

function hasFlowEvidenceMetrics(payload: WorkbenchTaskRunEvent["payload"]) {
  return ["entry_point_count", "call_edge_count", "test_reference_count"].some(
    (key) => payload[key] !== undefined && payload[key] !== null,
  );
}

function StageProgressPanel({ events, runPartial, onRetry, busy }: { events: WorkbenchTaskRunEvent[]; runPartial: boolean; onRetry: () => void; busy: boolean }) {
  const stageEvents = events.filter((item) => String(item.payload.kind || "").startsWith("stage_"));
  const latest = selectStageProgressEvent(stageEvents, runPartial);
  const kind = String(latest?.payload.kind || "");
  const status = String(latest?.payload.status || "");
  const partial = runPartial || kind === "stage_timed_out" || status === "partial";
  const completed = kind === "stage_completed" || kind === "stage_reused" || status === "completed";
  const clockMs = useStageClock(Boolean(latest) && !partial && !completed);
  if (!stageEvents.length) return null;
  if (!latest) return null;
  const stageId = String(latest.payload.stage_id || "business_flow");
  const evidencePayload = [...stageEvents].reverse().find((item) => hasFlowEvidenceMetrics(item.payload))?.payload || {};
  const providerPayload = [...stageEvents].reverse().find((item) => item.payload.stage_id === stageId && item.payload.kind === "stage_provider_started")?.payload || {};
  const outputPayload = [...stageEvents].reverse().find((item) => item.payload.stage_id === stageId && Number(item.payload.output_characters || 0) > 0)?.payload || {};
  const latestDelta = [...stageEvents].reverse().find((item) => item.payload.stage_id === stageId && item.payload.kind === "stage_output_delta")?.payload;
  const latestDeltaText = typeof latestDelta?.delta === "string" ? latestDelta.delta : "";
  const payload = { ...evidencePayload, ...providerPayload, ...outputPayload, ...latest.payload };
  const first = selectStageAttemptStart(stageEvents, stageId);
  const elapsedEnd = !partial && !completed && clockMs ? clockMs : new Date(latest.created_at).getTime();
  const elapsedSeconds = first ? Math.max(0, Math.round((elapsedEnd - new Date(first.created_at).getTime()) / 1000)) : 0;
  const stateLabel = partial ? "部分完成" : completed ? "已完成" : "运行中";
  return <section className={`ct-v2-stage-progress ${partial ? "is-partial" : ""}`} aria-label="阶段执行进度">
    <header><div><span>{stageDisplayName(stageId)}</span><h2>{runPartial ? "工作流已结束，当前最佳结果已保留" : eventMessage(latest)}</h2></div><em>{stateLabel}</em></header>
    <dl>
      <div><dt>调用链证据</dt><dd>{Number(payload.entry_point_count || 0)} 个入口 · {Number(payload.call_edge_count || 0)} 条调用边 · {Number(payload.test_reference_count || 0)} 个测试引用</dd></div>
      <div><dt>当前输出</dt><dd>{Number(payload.output_characters || 0)} 字符</dd></div>
      <div><dt>已运行</dt><dd>{elapsedSeconds} 秒</dd></div>
      <div><dt>最后活动</dt><dd>{new Date(latest.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</dd></div>
      <div><dt>执行器</dt><dd>{String(payload.model || "当前内置模型")}</dd></div>
      <div><dt>尝试</dt><dd>{formatStageAttemptLabel(payload)}</dd></div>
    </dl>
    {latestDeltaText && <pre className="ct-v2-stage-live-output" aria-label="阶段实时输出">{latestDeltaText}</pre>}
    {partial && <button type="button" disabled={busy} onClick={onRetry}><RefreshCw size={14} />继续生成 / 从本阶段重试</button>}
  </section>;
}

function useRunClock(active: boolean) {
  const [clockMs, setClockMs] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return clockMs;
}

function RunDuration({ start, end, active }: { start?: string; end?: string; active: boolean }) {
  const clockMs = useRunClock(active);
  return <strong>{formatDuration(start, end, clockMs)}</strong>;
}

const useStageClock = useRunClock;

function stageDisplayName(value: string) { return ({ source_analysis: "源码证据", flow_evidence_pack: "调用链证据", flow_outline: "流程骨架", business_flow: "业务流程", sfmea: "SFMEA", black_box_cases: "黑盒用例", breadth_inventory: "广度盘点", developer_explanation: "开发讲解与处置", scenario_expansion: "八源场景扩展", test_design_governance: "测试设计治理", coverage_judge: "覆盖质量门禁", test_design_mindmap: "测试设计脑图", behavior_claim_validation: "独立事实核验", test_strategy: "测试策略", test_design: "测试设计" } as Record<string, string>)[value] || value; }
function NodeInspector({ node }: { node?: WorkbenchRunUiNodeSummary }) { return <aside className="ct-v2-node-inspector"><header><span>运行上下文</span><h2>节点详情</h2></header>{node ? <div><strong>{displayNodeName(node.label || node.id || "当前节点")}</strong><small>{displayNodeType(node.type || "agent_task")} · {node.status_label}</small><InspectorText label="节点目标" value={displayNodeGoal(node)} /><InspectorText label="为什么执行" value={node.why || "由工作流依赖关系调度"} /><InspectorGroup label="直接依赖" values={(node.dependency_labels || node.depends_on || []).map(displayNodeName)} /><InspectorInputGroup values={node.received_inputs || []} /><InspectorGroup label="Agent / Provider" values={[node.executor_label || node.provider || "系统内置"]} /><InspectorGroup label="Skills" values={(node.skills || []).map((item) => item.label || item.id)} /><InspectorGroup label="MCP" values={node.mcp_profiles || []} /><InspectorGroup label="正在调用的工具" values={node.active_tools || []} /><InspectorGroup label="已产生的输出" values={(node.outputs || []).filter((item) => item.status_label === "已生成").map((item) => item.artifact || item.id)} /><InspectorGroup label="下一节点" values={(node.next_node_labels || node.next_node_ids || []).map(displayNodeName)} /><InspectorText label="开始时间" value={formatNodeTime(node.started_at)} /><InspectorText label="节点耗时" value={formatNodeDuration(node.duration_ms)} /></div> : <p>等待调度第一个节点。</p>}</aside>; }
function InspectorGroup({ label, values }: { label: string; values: string[] }) { return <section><h3>{label}</h3>{values.length ? values.map((item) => <span key={item}>{item}</span>) : <small>无</small>}</section>; }
function InspectorText({ label, value }: { label: string; value: string }) { return <section><h3>{label}</h3><p>{value || "无"}</p></section>; }
function InspectorInputGroup({ values }: { values: NonNullable<WorkbenchRunUiNodeSummary["received_inputs"]> }) { return <section><h3>实际收到的输入</h3>{values.length ? values.map((item) => <div className="ct-v2-inspector-input" key={item.id}><strong>{item.role || item.id}</strong><span>{item.value_summary || "已绑定"}</span></div>) : <small>无</small>}</section>; }
function QualityPanel({ run, onRetry, busy }: { run: PreparedWorkbenchTaskRun; onRetry: () => void; busy: boolean }) {
  const quality = run.test_activity_quality;
  const axes = quality?.quality_axes;
  const factSummary = quality?.fact_verification;
  return <section className="ct-v2-run-quality">
    <header><div><h2>质量结果</h2><strong>{taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")}</strong></div><span>{quality?.issue_count ?? 0} 个阻断项</span>{run.quality_status === "blocked" && <button type="button" disabled={busy} onClick={onRetry}><RefreshCw size={14} />{busy ? "正在启动修复" : "修复质量问题并重试"}</button>}</header>
    <div className="ct-v2-quality-axes">
      <QualityAxis label="结构合规率" status={axes?.structure?.status} value={axes?.structure?.score} detail={`${axes?.structure?.issue_count ?? 0} 个结构问题`} />
      <QualityAxis label="事实核验通过率" status={axes?.facts?.status} value={axes?.facts?.pass_rate ?? factSummary?.pass_rate} detail={`${factSummary?.verified ?? 0}/${factSummary?.total ?? 0} 条已验证 · ${factSummary?.contradicted ?? 0} 条冲突 · ${factSummary?.insufficient ?? 0} 条证据不足`} />
      <QualityAxis label="可执行性通过率" status={axes?.executability?.status} value={axes?.executability?.pass_rate} detail={`${axes?.executability?.issue_count ?? 0} 个执行能力问题`} />
      <QualityAxis label="覆盖处置门禁" status={axes?.coverage_judge?.status} value={axes?.coverage_judge?.score} detail={`${axes?.coverage_judge?.blocking_reasons?.length ?? 0} 个覆盖阻断项`} />
    </div>
    <p>{qualityMessage(run)}{quality?.lint_warning_count ? ` 另有 ${quality.lint_warning_count} 条结构提示，不作为事实核验结论。` : ""}</p>
  </section>;
}
function QualityAxis({ label, status, value, detail }: { label: string; status?: string; value?: number | null; detail: string }) { const checked = status !== "not_checked" && value !== null && value !== undefined; return <article className={`is-${status || "not_checked"}`}><span>{label}</span><strong>{checked ? `${value}%` : "未检查"}</strong><small>{detail}</small></article>; }
function FailurePanel({ summary, onRetry, busy }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; onRetry: () => void; busy: boolean }) { const failure = summary?.failure; const node = summary?.nodes.find((item) => item.id === failure?.failed_node_id); const nodeName = displayNodeName(node?.label || node?.id || "运行节点"); const preflightTitle = failure?.preflight_kind === "independent_quality_audit" ? "独立质量核验未就绪" : "执行器启动前检查未通过"; return <section className="ct-v2-run-failure"><AlertTriangle size={18} /><div><h2>{failure?.preflight_blocked ? preflightTitle : `${nodeName}执行失败`}</h2><p>{failure?.reasons?.[0] || "执行器未完成当前节点，请查看公开事件或技术诊断。"}</p><dl><div><dt>用户目标阶段</dt><dd>{displayNodeName(failure?.user_goal_stage || nodeName || "当前节点")}</dd></div><div><dt>失败性质</dt><dd>{failure?.failure_class === "configuration" ? "配置问题" : "运行时问题"}</dd></div><div><dt>已保留上游结果</dt><dd>{failure?.preserved_node_labels?.map(displayNodeName).join("、") || "无"}</dd></div><div><dt>重试时复用</dt><dd>{failure?.reuse_node_labels?.map(displayNodeName).join("、") || "无"}</dd></div><div><dt>重试时重跑</dt><dd>{failure?.rerun_node_labels?.map(displayNodeName).join("、") || nodeName}</dd></div><div><dt>推荐操作</dt><dd>{failure?.recommended_action || "查看公开事件后创建新 Attempt。"}</dd></div></dl></div>{failure?.preflight_blocked ? <Link href="/settings"><Wrench size={14} />检查执行器设置</Link> : failure?.can_retry && <button disabled={busy} onClick={onRetry}><RefreshCw size={14} />从失败节点重试</button>}</section>; }
function ArtifactRow({ item, runId, onOpen }: { item: WorkbenchTaskArtifact; runId: string; onOpen: (path: string) => void }) { const path = item.relative_path || item.path; const encoded = path.split("/").map(encodeURIComponent).join("/"); return <article className="ct-v2-artifact-row"><FileText size={15} /><button onClick={() => void onOpen(path)}><strong>{artifactDisplayName(path)}</strong><small>{path.split("/").pop()} · {formatBytes(item.size_bytes)}</small></button><a title="下载文件" href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/artifacts/download/${encoded}`}><Download size={15} /></a></article>; }
function artifactDisplayName(path: string) { const name=path.split("/").pop() || path; return ({"test_design_mindmap.json":"测试设计脑图（结构化）","test_design_mindmap.html":"测试设计脑图（交互版）","test_design_mindmap.svg":"测试设计脑图（评审版）","judge_report.json":"覆盖质量判定"} as Record<string,string>)[name] || name; }
function EventRow({ item, compact = false }: { item: WorkbenchTaskRunEvent; compact?: boolean }) { return <article className={`ct-v2-event-row is-${item.event_kind} ${compact ? "is-compact" : ""}`}><time>{new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{displayNodeName(eventNode(item) || "系统")}</span><em>{eventKindLabel(item.event_kind)}</em><div><strong>{eventMessage(item)}</strong>{!compact && eventDetail(item) && <pre>{eventDetail(item)}</pre>}</div></article>; }
type PairedToolCall = { id: string; use?: WorkbenchTaskRunEvent; result?: WorkbenchTaskRunEvent };
function pairedToolCalls(events: WorkbenchTaskRunEvent[]): PairedToolCall[] { const rows: PairedToolCall[] = []; const pending = new Map<string, PairedToolCall[]>(); for (const event of events) { const key = String(event.payload.call_id || event.payload.tool_call_id || event.payload.id || event.payload.tool || event.payload.name || "tool"); if (event.event_kind === "tool_use") { const row = { id: `tool-${event.event_id}`, use: event }; rows.push(row); const queue = pending.get(key) || []; queue.push(row); pending.set(key, queue); continue; } if (event.event_kind === "tool_result") { const row = pending.get(key)?.shift(); if (row) row.result = event; else rows.push({ id: `tool-result-${event.event_id}`, result: event }); } } return rows; }
function ToolCallRow({ item }: { item: PairedToolCall }) { const source = item.use || item.result; if (!source) return null; const tool = String(source.payload.tool || source.payload.name || "工具"); const resultSummary = item.result ? eventDetail(item.result) || eventMessage(item.result) : "等待工具返回结果"; return <article className={`ct-v2-tool-call ${item.result ? "is-complete" : "is-running"}`}><header><time>{new Date(source.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><strong>{tool}</strong><span>{item.result ? "已完成" : "调用中"}</span></header><p>{item.use ? eventMessage(item.use) : "收到工具结果"}</p><pre>{resultSummary}</pre></article>; }
function mergeEvents(current: WorkbenchTaskRunEvent[], incoming: WorkbenchTaskRunEvent[], direction: "live" | "older" = "live") { const map = new Map(current.map((item) => [item.event_id, item])); incoming.forEach((item) => map.set(item.event_id, item)); const ordered = [...map.values()].sort((a, b) => a.event_id - b.event_id); return direction === "older" ? ordered.slice(0, MAX_LOADED_EVENTS) : ordered.slice(-MAX_LOADED_EVENTS); }
function applyLifecycleEvents(run: PreparedWorkbenchTaskRun, events: WorkbenchTaskRunEvent[]) {
  const event = [...events].reverse().find((item) => lifecycleEventTypes.has(item.event_type));
  if (!event) return run;
  const status = lifecycleStatus(event.event_type);
  if (!status) return run;
  const nodeId = eventNode(event);
  const summary = run.run_ui_summary;
  const nodes = summary?.nodes.map((node) => node.id === nodeId && status === "running"
    ? { ...node, status: "running", status_label: "运行中", started_at: node.started_at || event.created_at }
    : node);
  const currentNode = nodes?.find((node) => node.id === nodeId) || summary?.current_node;
  return {
    ...run,
    execution_status: status,
    runtime: { ...run.runtime, status, started_at: run.runtime?.started_at || (status === "running" ? event.created_at : undefined) },
    started_at: run.started_at || (status === "running" ? event.created_at : undefined),
    run_ui_summary: summary ? {
      ...summary,
      status,
      status_label: taskStatusLabel(taskExecutionLabels, status),
      current_node: currentNode,
      nodes: nodes || summary.nodes,
    } : summary,
  };
}
function lifecycleStatus(eventType: string) {
  if (["queued"].includes(eventType)) return "queued";
  if (["running", "step_started", "node_started", "step_completed", "node_completed"].includes(eventType)) return "running";
  if (["completed", "partial", "failed", "error", "cancelled", "interrupted"].includes(eventType)) return eventType;
  if (["step_failed", "node_failed", "provider_readiness_blocked"].includes(eventType)) return "failed";
  return "";
}
function statusOf(run: PreparedWorkbenchTaskRun) { return String(run.execution_status || run.runtime?.status || run.status || "prepared").toLowerCase(); }
function eventNode(item: WorkbenchTaskRunEvent) { return String(item.payload.step_id || item.payload.node_id || item.payload.node_label || ""); }
function eventMessage(item: WorkbenchTaskRunEvent) { const message = String(item.payload.user_message || item.payload.message || eventTypeLabel(item.event_type)); return ({ "run completed": "运行已结束", "node blocked": "节点因上游门禁阻断" } as Record<string, string>)[message.toLowerCase()] || message; }
function eventDetail(item: WorkbenchTaskRunEvent) { const value = item.payload.delta ?? item.payload.text ?? item.payload.output ?? item.payload.error ?? item.payload.detail ?? ""; return typeof value === "string" ? value : value ? JSON.stringify(value, null, 2) : ""; }
function eventClipboardLine(item: WorkbenchTaskRunEvent) { return `[${new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}] ${eventNode(item) || "系统"} ${eventMessage(item)} ${eventDetail(item)}`.trim(); }
function eventKindLabel(kind: string) { return ({ status: "状态", done: "完成", artifact: "产物", output: "输出", error: "错误", thinking: "思考", reasoning: "推理", diagnostic: "诊断", trace: "跟踪", tool_use: "工具调用", tool_result: "工具结果" } as Record<string, string>)[kind] || kind; }
function eventTypeLabel(type: string) { return ({ queued: "已进入运行队列", running: "运行已开始", step_started: "节点开始执行", step_completed: "节点执行完成", step_failed: "节点执行失败", node_reused: "已复用父运行的成功节点", completed: "运行已完成", partial: "运行保留了部分结果", cancelled: "运行已取消", artifact_created: "产物已生成", agent_output: "执行器产生新输出" } as Record<string, string>)[type] || type.replaceAll("_", " "); }
function qualityMessage(run: PreparedWorkbenchTaskRun) { const status = run.quality_status || "not_checked"; if (status === "passed") return "结构化质量检查已通过。"; if (status === "warning") return "产物已生成，但仍有质量警告需要复核。"; if (status === "blocked") return "质量门禁未通过，请先修复阻断项。"; if (status === "pending") return "正在检查产物完整性和质量。"; return "本次运行尚未执行质量检查。"; }
function formatNodeTime(value?: string) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "尚未开始"; }
function formatNodeDuration(value?: number) { if (!value) return "尚未完成"; const seconds = Math.floor(value / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function formatDuration(start?: string, end?: string, nowMs = Date.now()) { if (!start) return "—"; const milliseconds = Math.max(0, new Date(end || nowMs).getTime() - new Date(start).getTime()); const seconds = Math.floor(milliseconds / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function displayNodeName(value: string) { return ({ analyze_source_flow: "源码驱动测试分析", validate_evidence: "源码证据校验", render_report: "汇总报告生成" } as Record<string, string>)[value] || value; }
function displayNodeType(value: string) { return ({ agent_task: "智能分析", evidence_validate: "证据校验", report_render: "报告生成" } as Record<string, string>)[value] || value; }
function displayNodeGoal(node: WorkbenchRunUiNodeSummary) {
  if (node.id === "analyze_source_flow") return "先检查可用的 GitNexus 和 CGC 产物，再读取本地源码与测试证据，生成代码证据、外部可观察流程、SFMEA 和可执行黑盒测试用例。";
  const goal = String(node.goal || "完成当前工作流阶段").replace(/\s+/g, " ").trim();
  return goal.length > 180 ? `${goal.slice(0, 180)}…` : goal;
}
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }

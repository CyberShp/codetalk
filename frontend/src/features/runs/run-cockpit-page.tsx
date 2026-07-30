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
  WorkbenchRunUiSummary,
  WorkbenchRunUiNodeSummary,
  WorkbenchTaskArtifact,
  WorkbenchTaskRunEvent,
} from "@/lib/types";
import { workbenchTasksApi } from "@/lib/api/workbench-tasks";
import type { WorkbenchTask } from "@/lib/types/task";
import {
  taskArtifactValidationLabels,
  taskDeliveryLabels,
  taskExecutionLabels,
  taskGovernanceLabels,
  taskQualityLabels,
  taskStatusLabel,
} from "@/features/tasks/task-status";
import {
  formatStageAttemptLabel,
  requiresRunSummaryRefresh,
  selectStageAttemptStart,
  selectStageProgressEvent,
} from "@/features/runs/stage-progress-event";

const tabs = ["摘要", "实时输出", "工具调用", "全部事件"] as const;
const terminalStatuses = new Set(["completed", "partial", "success", "failed", "error", "cancelled", "interrupted", "quality_blocked"]);
const lifecycleEventTypes = new Set([
  "queued", "running", "step_started", "node_started", "step_completed", "node_completed",
  "waiting_for_input", "node_waiting", "step_failed", "node_failed", "provider_readiness_blocked", "quality_blocked", "completed", "partial", "failed", "error", "cancelled", "interrupted",
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

type NodeLabelMap = Map<string, string>;

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
  const refreshEpoch = useRef(0);
  const nodeLabels = useMemo(
    () => buildNodeLabelMap(run?.run_ui_summary?.nodes || []),
    [run?.run_ui_summary?.nodes],
  );
  const refresh = useCallback(async (quiet = false) => {
    const refreshId = ++refreshEpoch.current;
    if (!quiet) setLoading(true);
    try {
      const [nextTask, nextRun, eventResult, artifactResult] = await Promise.all([
        workbenchTasksApi.get(taskId),
        api.workbench.taskRuns.get(runId),
        api.workbench.taskRuns.events(runId, { tail: true, limit: EVENT_PAGE_SIZE }),
        api.workbench.taskRuns.artifacts(runId),
      ]);
      if (refreshId !== refreshEpoch.current) return;
      if (nextRun.task_id && nextRun.task_id !== taskId) throw new Error("该运行不属于当前任务");
      setTask(nextTask);
      setRun(applyLifecycleEvents(nextRun, eventResult.items));
      setEvents((current) => mergeEvents(current, eventResult.items));
      setHasOlderEvents(Boolean(eventResult.has_older));
      setArtifacts(artifactResult.artifacts);
      lastEventId.current = Math.max(lastEventId.current, eventResult.latest_event_id);
      setError("");
    } catch (cause) {
      if (refreshId !== refreshEpoch.current) return;
      setError(cause instanceof Error ? cause.message : "运行信息加载失败");
    } finally {
      if (refreshId === refreshEpoch.current) setLoading(false);
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
      if (
        lifecycleEventTypes.has(item.event_type) ||
        requiresRunSummaryRefresh(item)
      ) void refresh(true);
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
      const text = `${eventNodeLabel(item, nodeLabels)} ${eventMessage(item, nodeLabels)} ${eventDetail(item, nodeLabels)}`.toLowerCase();
      const node = eventNodeLabel(item, nodeLabels);
      if (query && !text.includes(query.toLowerCase())) return false;
      if (nodeFilter && node !== nodeFilter) return false;
      if (kindFilter && item.event_kind !== kindFilter) return false;
      if (tab === "实时输出" && !["output", "thinking", "reasoning", "error"].includes(item.event_kind)) return false;
      if (tab === "工具调用" && !["tool_use", "tool_result"].includes(item.event_kind)) return false;
      return true;
    });
  }, [events, frozenEvents, kindFilter, nodeFilter, nodeLabels, pauseBoundary, paused, query, tab]);
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
  const v3Axes = v3RunAxes(run);
  const v3Contract = v3Axes !== null;
  const running = ["queued", "running", "prepared", "waiting_for_input"].includes(status);
  const failed = ["failed", "error", "interrupted"].includes(status);
  const partial = status === "partial";
  const diagnosticTrial = Boolean(
    run.task_bundle?.diagnostic
    && typeof run.task_bundle.diagnostic === "object"
    && (run.task_bundle.diagnostic as { not_a_formal_delivery?: unknown }).not_a_formal_delivery === true,
  );
  const nodeNames = [...new Set(events.map((item) => eventNodeLabel(item, nodeLabels)).filter(Boolean))];
  const kinds = [...new Set(events.map((item) => item.event_kind).filter(Boolean))];
  const publicArtifacts = artifacts.filter((item) => item.audience !== "diagnostic");
  const deliverables = publicArtifacts.filter((item) => item.audience === "deliverable");
  const qualityBlocked = !v3Contract && run.quality_status === "blocked";
  const deliveryBlocked = v3Contract ? v3Axes.delivery === "blocked" : qualityBlocked;
  const executionProfile = run.task_bundle?.execution_profile as Record<string, unknown> | undefined;
  const executionProfileId = typeof executionProfile?.id === "string" ? executionProfile.id : "";
  const executionProfileLabel = typeof executionProfile?.label === "string" && executionProfile.label.trim()
    ? executionProfile.label
    : "已冻结执行档位";
  const profileDuration = Array.isArray(executionProfile?.expected_duration_minutes)
    ? executionProfile.expected_duration_minutes.map((value) => Number(value)).filter(Number.isFinite)
    : [];
  const reuseEvents = events.filter((item) => String(item.payload.kind || "") === "stage_reused");
  // A stage reused during the same quality-repair pass is not a cache hit.
  // Showing it as one makes a cold run look artificially fast.
  const cachedReuseEvents = reuseEvents.filter((item) => String(item.payload.reuse_source || "") === "cross_run_cache");
  const cachedStageIds = new Set(
    cachedReuseEvents
      .map((item) => String(item.payload.stage_id || item.payload.artifact || "").trim())
      .filter(Boolean),
  );
  const sameRunReuseEvents = reuseEvents.filter((item) => String(item.payload.reuse_source || "").trim() === "same_run_quality_accepted_artifact");
  // A retry can reuse a completed parent node without emitting the stage-level
  // cache event. Treat this as a quality review, never as a fresh rapid run.
  const nodeReuseEvents = events.filter((item) => item.event_type === "node_reused");
  const qualityReviewReuse = nodeReuseEvents.length > 0 || sameRunReuseEvents.length > 0;
  const waitingApprovalNode = status === "waiting_for_input"
    ? summary?.nodes.find((node) => node.status === "waiting_for_input") || currentNode
    : undefined;
  const recoveredAfterRestart = events.some(({ payload }) =>
    payload.source === "checkpoint_projection_rebuild"
    || payload.source === "startup_recovery",
  );

  const cancel = async () => {
    setActionBusy(true);
    try { await api.workbench.taskRuns.cancel(runId); await refresh(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "取消运行失败"); }
    finally { setActionBusy(false); }
  };
  const decideApproval = async (decision: "approve" | "reject", reason: string) => {
    if (!waitingApprovalNode || !reason.trim()) return;
    setActionBusy(true);
    setError("");
    try {
      await api.workbench.taskRuns.decideApproval(runId, waitingApprovalNode.id, {
        decision,
        actor: "local-operator",
        reason: reason.trim(),
        decided_at: new Date().toISOString(),
      });
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交审批决定失败");
    } finally {
      setActionBusy(false);
    }
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
      <StatusBlock label="执行状态" value={executionStatusLabel(status)} tone={status} />
      {v3Axes ? <>
        <StatusBlock label="产物校验" value={taskStatusLabel(taskArtifactValidationLabels, v3Axes.artifactValidation)} tone={v3Axes.artifactValidation} />
        <StatusBlock label="专业治理" value={taskStatusLabel(taskGovernanceLabels, v3Axes.governance)} tone={v3Axes.governance} />
        <StatusBlock label="交付状态" value={taskStatusLabel(taskDeliveryLabels, v3Axes.delivery)} tone={v3Axes.delivery} />
      </> : <>
        <StatusBlock label="质量状态" value={taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")} tone={run.quality_status || "not_checked"} />
        <StatusBlock label="交付状态" value={taskStatusLabel(taskDeliveryLabels, run.delivery_status || "none")} tone={run.delivery_status || "none"} />
      </>}
      <div className="ct-v2-run-metric"><span>耗时</span><RunDuration start={run.started_at || run.runtime?.started_at} end={run.completed_at || run.runtime?.completed_at} active={running} /></div>
      {executionProfileId && <div className="ct-v2-run-metric"><span>执行档位</span><strong>{executionProfileLabel}</strong><small>{qualityReviewReuse ? "基于已验收产物的质量复核，不计入速度型完整运行耗时。" : <>{profileDuration.length === 2 ? `目标 ${profileDuration[0]}-${profileDuration[1]} 分钟` : "已冻结到本次运行"}{cachedStageIds.size ? ` · 跨运行缓存命中 ${cachedStageIds.size} 个阶段（${cachedReuseEvents.length} 次）` : " · 未命中跨运行缓存"}</>}</small></div>}
      <div className="ct-v2-run-metric"><span>当前节点</span><strong>{currentNode ? publicNodeLabel(currentNode) : "等待调度"}</strong></div>
      <div className="ct-v2-run-actions">
        <button type="button" disabled={actionBusy} onClick={() => void discussRun()}><MessageSquareText size={14} />围绕本次运行继续分析</button>
        {running && <button type="button" disabled={actionBusy} onClick={() => void cancel()}><Square size={14} />取消</button>}
      </div>
    </header>
    {(error || v3Axes?.unsupportedVersion || waitingApprovalNode) && <section className="ct-v2-run-notices">
      {error && <div className="ct-v2-run-error" role="alert"><AlertTriangle size={15} />{publicNodeText(error, nodeLabels)}<button aria-label="关闭错误" onClick={() => setError("")}><X size={14} /></button></div>}
      {v3Axes?.unsupportedVersion && <div className="ct-v2-run-error" role="alert"><AlertTriangle size={15} />冻结契约版本不受支持：{v3Axes.unsupportedVersion}。本次运行已阻断，请升级 CodeTalk 后重试。</div>}
      {waitingApprovalNode && <HumanApprovalPanel node={waitingApprovalNode} recovered={recoveredAfterRestart} busy={actionBusy} onDecide={decideApproval} />}
    </section>}

    <section className="ct-v2-run-workspace">
      <div className="ct-v2-run-main">
        <nav className="ct-v2-run-tabs" role="tablist">{tabs.map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? "is-active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>
        {tab === "摘要" ? <RunSummary summary={summary} events={events} nodeLabels={nodeLabels} failed={failed} partial={partial} selectedNodeId={inspectedNode?.id || ""} onSelectNode={setSelectedNodeId} onRetry={() => void retry()} actionBusy={actionBusy} /> : <>
          <div className="ct-v2-event-toolbar"><label><Search size={13} /><input aria-label="搜索运行事件" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索输出" /></label><select aria-label="按节点筛选" value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)}><option value="">全部节点</option>{nodeNames.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="按类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="">全部类型</option>{kinds.map((item) => <option key={item}>{item}</option>)}</select><button title="暂停或继续显示" onClick={togglePaused}>{paused ? <Play size={14} /> : <Pause size={14} />}{paused ? "继续" : "暂停"}</button><button title="自动跟随最新输出" className={autoScroll ? "is-active" : ""} onClick={() => setAutoScroll((value) => !value)}>自动滚动</button><button title="复制当前事件" onClick={() => void navigator.clipboard.writeText(visibleEvents.map((item) => eventClipboardLine(item, nodeLabels)).join("\n"))}><Clipboard size={14} /></button></div>
          <div className="ct-v2-event-viewport" ref={eventViewport} onScroll={(event) => { const target = event.currentTarget; if (target.scrollHeight - target.scrollTop - target.clientHeight > 36) setAutoScroll(false); }}>
            {hasOlderEvents && <button className="ct-v2-event-load-older" type="button" disabled={loadingOlderEvents} onClick={() => void loadOlderEvents()}>{loadingOlderEvents ? "正在加载…" : "加载更早事件"}</button>}
            {paused && <div className="ct-v2-event-empty">显示已冻结在当前时刻，后台运行不受影响。</div>}
            {visibleEvents.length ? (tab === "工具调用" ? pairedToolCalls(visibleEvents).map((item) => <ToolCallRow key={item.id} item={item} nodeLabels={nodeLabels} />) : visibleEvents.map((item) => <EventRow key={item.event_id} item={item} nodeLabels={nodeLabels} />)) : <div className="ct-v2-event-empty">暂无符合条件的公开事件。</div>}
          </div>
        </>}
      </div>
      <NodeInspector node={inspectedNode} nodeLabels={nodeLabels} />
    </section>

    <section className="ct-v2-run-results">
      {failed && <FailurePanel summary={summary} onRetry={() => void retry()} busy={actionBusy} />}
      <section className="ct-v2-run-deliverables"><header><div><h2>{deliveryBlocked ? "受阻产物" : "交付件"}</h2><span>{deliveryBlocked ? `${deliverables.length} 个待修复草稿` : `${deliverables.length} 个可交付文件`}</span></div></header><div>{deliverables.length ? <>{deliveryBlocked && <p>{v3Contract ? "声明产物未通过校验或被阻断：以下文件仅用于查看问题与辅助修复，尚不能作为正式交付。" : "质量门禁未通过：以下文件仅用于查看问题与辅助修复，尚不能作为正式交付。"}</p>}{deliverables.map((item, index) => <ArtifactRow key={artifactRowKey(item, index)} item={item} runId={runId} onOpen={openArtifact} nodeLabels={nodeLabels} />)}</> : <p>运行完成后，用户可下载的最终文件会显示在这里。</p>}</div></section>
      {v3Axes ? <V3StatusPanel axes={v3Axes} /> : <QualityPanel run={run} onRetry={() => void retry()} busy={actionBusy} />}
      <InputConsumptionPanel ledger={run.input_consumption} nodeLabels={nodeLabels} />
      <details className="ct-v2-run-support"><summary>支撑文件与输入快照（{publicArtifacts.length - deliverables.length}）</summary>{publicArtifacts.filter((item) => item.audience !== "deliverable").map((item, index) => <ArtifactRow key={artifactRowKey(item, index)} item={item} runId={runId} onOpen={openArtifact} nodeLabels={nodeLabels} />)}</details>
    </section>

    <button className="ct-v2-diagnostic-trigger" type="button" onClick={() => setDiagnosticsOpen(true)}><Wrench size={14} />技术诊断</button>
    <aside className={`ct-v2-diagnostic-drawer ${diagnosticsOpen ? "is-open" : ""}`} hidden={!diagnosticsOpen} aria-label="技术诊断"><header><h2>技术诊断</h2><button aria-label="关闭技术诊断" onClick={() => setDiagnosticsOpen(false)}><X size={16} /></button></header><a href={`${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/diagnostic-package`}><Download size={14} />下载脱敏诊断包</a><details><summary>运行快照</summary><pre>{publicDiagnosticJson(run, nodeLabels)}</pre></details><details><summary>原始公开事件</summary><pre>{publicDiagnosticJson(events, nodeLabels)}</pre></details><details><summary>诊断产物</summary>{artifacts.filter((item) => item.audience === "diagnostic").map((item, index) => <span key={artifactRowKey(item, index)}>{publicNodeText(item.relative_path, nodeLabels)}</span>)}</details></aside>
    {preview && <div className="ct-v2-artifact-modal" role="dialog" aria-modal="true" aria-label="产物预览"><section><header><FileText size={15} /><strong>{artifactDisplayName(preview.path, nodeLabels)}</strong><a title="下载完整文件" href={artifactDownloadHref(runId, preview.path)}><Download size={15} /></a><button aria-label="关闭产物预览" onClick={() => setPreview(null)}><X size={16} /></button></header>{preview.path.endsWith("test_design_mindmap.json") ? <TestDesignMindmapPreview content={preview.content} /> : <pre>{publicNodeText(preview.content, nodeLabels)}</pre>}{preview.truncated && <p>内容较长，预览已截断，请下载完整文件。</p>}</section></div>}
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
function V3StatusPanel({ axes }: { axes: V3RunAxes }) {
  if (axes.unsupportedVersion) return <section className="ct-v2-run-quality is-blocked" aria-label="运行版本兼容性"><header><AlertTriangle size={18} /><div><h2>不支持的冻结契约版本</h2><strong>版本 {axes.unsupportedVersion}</strong></div></header><p>历史运行仍可查看和下载，但当前部署不能重跑这个冻结版本。请从原工作流版本复制为受支持的 V3 工作流，或联系管理员完成版本迁移。</p></section>;
  return <section className="ct-v2-run-quality" aria-label="V3 运行状态"><header><div><h2>运行状态</h2><strong>{taskStatusLabel(taskDeliveryLabels, axes.delivery)}</strong></div></header><div className="ct-v2-quality-axes"><V3StatusAxis label="执行" value={executionStatusLabel(axes.execution)} tone={axes.execution} /><V3StatusAxis label="产物校验" value={taskStatusLabel(taskArtifactValidationLabels, axes.artifactValidation)} tone={axes.artifactValidation} /><V3StatusAxis label="专业治理" value={taskStatusLabel(taskGovernanceLabels, axes.governance)} tone={axes.governance} /><V3StatusAxis label="交付" value={taskStatusLabel(taskDeliveryLabels, axes.delivery)} tone={axes.delivery} /></div><p>此运行按冻结的 V3 输出契约验收；未启用专业治理时显示“未请求”，不会作为质量失败。</p></section>;
}
function V3StatusAxis({ label, value, tone }: { label: string; value: string; tone: string }) { return <article className={`is-${tone}`}><span>{label}</span><strong>{value}</strong></article>; }
function RunSummary({ summary, events, nodeLabels, failed, partial, selectedNodeId, onSelectNode, onRetry, actionBusy }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; events: WorkbenchTaskRunEvent[]; nodeLabels: NodeLabelMap; failed: boolean; partial: boolean; selectedNodeId: string; onSelectNode: (id: string) => void; onRetry: () => void; actionBusy: boolean }) { const recoveredNodes = summary?.nodes.filter((node) => node.recovered_from_partial) || []; const recovered = recoveredNodes.length > 0; const recoveredLabel = recoveredNodes.map(publicNodeLabel).join("、"); return <div className="ct-v2-run-summary"><section><h2>{failed ? "运行在节点处停止" : partial ? "运行保留了部分结果" : "节点进度"}</h2><div className="ct-v2-node-timeline">{(summary?.nodes || []).map((node) => { const nodeName = publicNodeLabel(node); return <button type="button" key={node.id} className={`is-${node.status || "prepared"} ${selectedNodeId === node.id ? "is-selected" : ""}`} onClick={() => onSelectNode(node.id)} aria-label={`查看节点 ${nodeName}`}><span>{node.status === "completed" || node.status === "success" ? <CheckCircle2 size={15} /> : node.status === "running" ? <Loader2 className="animate-spin" size={15} /> : node.status === "failed" || node.status === "error" || node.status === "interrupted" ? <AlertTriangle size={15} /> : <i />}</span><strong>{nodeName}</strong><small>{node.status_label}</small></button>; })}</div></section><StageProgressPanel events={events} stageProgress={summary?.test_activity_stage_progress} runPartial={partial} recovered={recovered} recoveredLabel={recoveredLabel} onRetry={onRetry} busy={actionBusy} /><section><h2>最新活动</h2>{events.slice(-8).reverse().map((item) => <EventRow key={item.event_id} item={item} nodeLabels={nodeLabels} compact />)}</section></div>; }

function hasFlowEvidenceMetrics(payload: WorkbenchTaskRunEvent["payload"]) {
  return ["entry_point_count", "call_edge_count", "test_reference_count"].some(
    (key) => payload[key] !== undefined && payload[key] !== null,
  );
}

function stageProgressStatusLabel(status?: string) {
  return ({ completed: "已完成", running: "运行中", partial: "部分完成", awaiting_artifacts: "等待产物", failed: "失败", cancelled: "已取消", pending: "等待中", not_requested: "未执行" } as Record<string, string>)[String(status || "")] || "等待中";
}

function StageProgressPanel({ events, stageProgress, runPartial, recovered, recoveredLabel, onRetry, busy }: { events: WorkbenchTaskRunEvent[]; stageProgress?: WorkbenchRunUiSummary["test_activity_stage_progress"]; runPartial: boolean; recovered: boolean; recoveredLabel: string; onRetry: () => void; busy: boolean }) {
  const stageEvents = events.filter((item) => String(item.payload.kind || "").startsWith("stage_"));
  const latest = selectStageProgressEvent(stageEvents, runPartial);
  const kind = String(latest?.payload.kind || "");
  const status = String(latest?.payload.status || "");
  const partial = !recovered && (runPartial || kind === "stage_timed_out" || status === "partial");
  const completed = recovered || kind === "stage_completed" || kind === "stage_reused" || status === "completed";
  const clockMs = useStageClock(Boolean(latest) && !partial && !completed);
  if (!stageEvents.length) return null;
  if (!latest) return null;
  const stageId = String(latest.payload.stage_id || "business_flow");
  const stageArtifact = String(latest.payload.artifact || "");
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
    <header><div><span>{recovered ? recoveredLabel : stageDisplayName(stageId, stageArtifact)}</span><h2>{recovered ? "最终质量审计已接受保留结果" : runPartial ? "工作流已结束，当前最佳结果已保留" : eventMessage(latest)}</h2></div><em>{recovered ? "已完成（使用保留结果）" : stateLabel}</em></header>
    {recovered ? <dl>
      <div><dt>最终状态</dt><dd>已完成，质量门禁已通过</dd></div>
      <div><dt>保留原因</dt><dd>中间模型输出未完整闭合，已由确定性证据和质量修复补全。</dd></div>
      <div><dt>交付建议</dt><dd>可下载交付件，或围绕本次运行继续分析。</dd></div>
    </dl> : <dl>
      <div><dt>调用链证据</dt><dd>{Number(payload.entry_point_count || 0)} 个入口 · {Number(payload.call_edge_count || 0)} 条调用边 · {Number(payload.test_reference_count || 0)} 个测试引用</dd></div>
      <div><dt>当前输出</dt><dd>{Number(payload.output_characters || 0)} 字符</dd></div>
      <div><dt>已运行</dt><dd>{elapsedSeconds} 秒</dd></div>
      <div><dt>最后活动</dt><dd>{new Date(latest.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</dd></div>
      <div><dt>执行器</dt><dd>{String(payload.model || "当前内置模型")}</dd></div>
      <div><dt>尝试</dt><dd>{formatStageAttemptLabel(payload)}</dd></div>
    </dl>}
    {stageProgress?.stages?.length ? <ol className="ct-v2-stage-checklist" aria-label="测试活动阶段状态">{stageProgress.stages.map((stage) => <li key={stage.stage_id} className={`is-${stage.status || "pending"}`}><span /><strong>{stage.name || stageDisplayName(stage.stage_id || "")}</strong><small>{stageProgressStatusLabel(stage.status)}{stage.expected_artifacts?.length ? ` · ${stage.present_artifacts?.length || 0}/${stage.expected_artifacts.length} 产物` : ""}</small></li>)}</ol> : null}
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

function stageDisplayName(value: string, artifact = "") {
  const known = ({ source_analysis: "源码证据", flow_evidence_pack: "调用链证据", flow_outline: "流程骨架", business_flow: "业务流程", sfmea: "SFMEA", black_box_cases: "黑盒用例", breadth_inventory: "广度盘点", developer_explanation: "开发讲解与处置", scenario_expansion: "八源场景扩展", test_design_governance: "测试设计治理", coverage_judge: "覆盖质量门禁", test_design_mindmap: "测试设计脑图", behavior_claim_validation: "独立事实核验", test_strategy: "测试策略", test_design: "测试设计" } as Record<string, string>)[value];
  if (known) return known;
  if (value.startsWith("artifact_")) return ({ "source_analysis.md": "源码分析摘要", "coverage_gap.md": "覆盖缺口与建议" } as Record<string, string>)[artifact] || "补充交付材料";
  return value;
}
function HumanApprovalPanel({ node, recovered, busy, onDecide }: {
  node: WorkbenchRunUiNodeSummary;
  recovered: boolean;
  busy: boolean;
  onDecide: (decision: "approve" | "reject", reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const disabled = busy || !reason.trim();
  return <section className="ct-v2-human-approval" aria-label="人工审批">
    <div><span>{recovered ? "已从检查点恢复 · 等待人工审批" : "等待人工审批"}</span><strong>{publicNodeLabel(node)}</strong><small>{publicNodeText(node.why || node.goal || "此节点需要确认后才能继续。")}</small></div>
    <section className="ct-v2-human-approval-context"><h3>待审批上下文</h3><pre>{publicNodeText(node.approval_context?.summary || "无上游上下文")}</pre>{node.approval_context?.truncated ? <small>内容较长，当前显示有界预览。</small> : null}</section>
    <label>审批原因<input aria-label="审批原因" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="记录本次决定的依据" disabled={busy} /></label>
    <div className="ct-v2-human-approval-actions">
      <button type="button" className="is-primary" disabled={disabled} onClick={() => onDecide("approve", reason)}>{busy ? <Loader2 className="animate-spin" size={14} /> : <CheckCircle2 size={14} />}批准</button>
      <button type="button" className="is-reject" disabled={disabled} onClick={() => onDecide("reject", reason)}>{busy ? <Loader2 className="animate-spin" size={14} /> : <X size={14} />}拒绝</button>
    </div>
  </section>;
}
function NodeInspector({ node, nodeLabels }: { node?: WorkbenchRunUiNodeSummary; nodeLabels: NodeLabelMap }) { return <aside className="ct-v2-node-inspector"><header><span>运行上下文</span><h2>节点详情</h2></header>{node ? <div><strong>{publicNodeLabel(node)}</strong><small>{displayNodeType(node.type || "agent_task")} · {publicNodeText(node.status_label, nodeLabels)}</small><InspectorText label="节点目标" value={displayNodeGoal(node, nodeLabels)} /><InspectorText label="为什么执行" value={publicNodeText(node.why || "由工作流依赖关系调度", nodeLabels)} /><InspectorGroup label="直接依赖" values={referencedNodeLabels(node.depends_on, node.dependency_labels, nodeLabels)} nodeLabels={nodeLabels} /><InspectorInputGroup values={node.received_inputs || []} nodeLabels={nodeLabels} /><InspectorGroup label="Agent / Provider" values={[node.executor_label || node.provider || "系统内置"]} nodeLabels={nodeLabels} /><InspectorGroup label="Skills" values={(node.skills || []).map((item) => item.label || item.id)} nodeLabels={nodeLabels} /><InspectorGroup label="MCP" values={node.mcp_profiles || []} nodeLabels={nodeLabels} /><InspectorGroup label="正在调用的工具" values={node.active_tools || []} nodeLabels={nodeLabels} /><InspectorGroup label="已产生的输出" values={(node.outputs || []).filter((item) => item.status_label === "已生成").map((item) => item.artifact || item.id)} nodeLabels={nodeLabels} /><InspectorGroup label="下一节点" values={referencedNodeLabels(node.next_node_ids, node.next_node_labels, nodeLabels)} nodeLabels={nodeLabels} /><InspectorText label="开始时间" value={formatNodeTime(node.started_at)} /><InspectorText label="节点耗时" value={formatNodeDuration(node.duration_ms)} /></div> : <p>等待调度第一个节点。</p>}</aside>; }
function InspectorGroup({ label, values, nodeLabels }: { label: string; values: string[]; nodeLabels: NodeLabelMap }) { const publicValues = values.map((item) => publicNodeText(item, nodeLabels)).filter(Boolean); return <section><h3>{label}</h3>{publicValues.length ? publicValues.map((item, index) => <span key={`${index}-${item}`}>{item}</span>) : <small>无</small>}</section>; }
function InspectorText({ label, value }: { label: string; value: string }) { return <section><h3>{label}</h3><p>{value || "无"}</p></section>; }
function InspectorInputGroup({ values, nodeLabels }: { values: NonNullable<WorkbenchRunUiNodeSummary["received_inputs"]>; nodeLabels: NodeLabelMap }) { return <section><h3>实际收到的输入</h3>{values.length ? values.map((item) => <div className="ct-v2-inspector-input" key={item.id}><strong>{publicNodeText(item.role || item.id, nodeLabels)}</strong><span>{publicNodeText(item.value_summary || "已绑定", nodeLabels)}</span></div>) : <small>无</small>}</section>; }
function QualityPanel({ run, onRetry, busy }: { run: PreparedWorkbenchTaskRun; onRetry: () => void; busy: boolean }) {
  const quality = run.test_activity_quality;
  const axes = quality?.quality_axes;
  const factSummary = quality?.fact_verification;
  const profileExecution = quality?.profile_execution;
  const blockers = (quality?.issues || []).slice(0, 3);
  return <section className="ct-v2-run-quality">
    <header><div><h2>质量结果</h2><strong>{taskStatusLabel(taskQualityLabels, run.quality_status || "not_checked")}</strong></div><span>{quality?.issue_count ?? 0} 个阻断项</span>{run.quality_status === "blocked" && <button type="button" disabled={busy} onClick={onRetry}><RefreshCw size={14} />{busy ? "正在启动修复" : "修复质量问题并重试"}</button>}</header>
    <div className="ct-v2-quality-axes">
      <QualityAxis label="结构合规率" status={axes?.structure?.status} value={axes?.structure?.score} detail={`${axes?.structure?.issue_count ?? 0} 个结构问题`} />
      <QualityAxis label="事实核验通过率" status={axes?.facts?.status} value={axes?.facts?.pass_rate ?? factSummary?.pass_rate} detail={`${factSummary?.verified ?? 0}/${factSummary?.total ?? 0} 条已验证 · ${factSummary?.contradicted ?? 0} 条冲突 · ${factSummary?.insufficient ?? 0} 条证据不足`} />
      <QualityAxis label="可执行性通过率" status={axes?.executability?.status} value={axes?.executability?.pass_rate} detail={`${axes?.executability?.issue_count ?? 0} 个执行能力问题`} />
      <QualityAxis label="专业覆盖广度" status={axes?.coverage_breadth?.status} value={axes?.coverage_breadth?.score} detail={`${axes?.coverage_breadth?.missing_scenario_count ?? 0} 个待补测试场景`} />
      <QualityAxis label="覆盖处置门禁" status={axes?.coverage_judge?.status} value={axes?.coverage_judge?.score} detail={`${axes?.coverage_judge?.blocking_reasons?.length ?? 0} 个覆盖阻断项`} />
    </div>
    {profileExecution?.profile_id === "deep" ? <section className={`ct-v2-profile-work-proof is-${profileExecution.status || "unknown"}`} aria-label="深度执行工作量证明"><header><span>深度执行证明</span><strong>{profileExecution.status === "passed" ? "已验证" : "待核验"}</strong></header><p>{profileExecution.provider_call_count ?? 0} 次模型调用 · {(profileExecution.output_tokens ?? 0).toLocaleString()} 输出 token · Provider 等待 {formatMilliseconds(profileExecution.provider_wait_ms)}</p><small>{profileExecution.branch_count ?? 0} 个定向探索分支{profileExecution.reused_stage_count ? ` · 复用 ${profileExecution.reused_stage_count} 个阶段` : " · 未复用已完成阶段"}{profileExecution.missing_branch_provider_work?.length ? ` · 缺少 ${profileExecution.missing_branch_provider_work.length} 个分支的模型工作` : ""}{profileExecution.under_evidenced_branches?.length ? ` · ${profileExecution.under_evidenced_branches.length} 个分支证据不足` : ""}</small></section> : null}
    {run.quality_status === "blocked" && <section className="ct-v2-quality-blockers" aria-label="质量阻断原因"><h3>质量阻断原因</h3><ul>{blockers.map((item, index) => <li key={`${item.code || "issue"}-${index}`}><strong>{item.artifact || "交付件"}</strong><span>{item.message || "质量检查发现需要修复的问题"}</span></li>)}</ul>{quality?.recommendations?.length ? <p>下一步：{quality.recommendations[0]}</p> : null}</section>}
    {axes?.coverage_breadth?.warnings?.length ? <details className="ct-v2-quality-coverage-warnings"><summary>查看待补覆盖场景（{axes.coverage_breadth.warnings.length} 项）</summary><ul>{axes.coverage_breadth.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></details> : null}
    <p>{qualityMessage(run)}</p>
  </section>;
}
function QualityAxis({ label, status, value, detail }: { label: string; status?: string; value?: number | null; detail: string }) { const checked = status !== "not_checked" && value !== null && value !== undefined; return <article className={`is-${status || "not_checked"}`}><span>{label}</span><strong>{checked ? `${value}%` : "未检查"}</strong><small>{detail}</small></article>; }
function InputConsumptionPanel({ ledger, nodeLabels }: { ledger?: PreparedWorkbenchTaskRun["input_consumption"]; nodeLabels: NodeLabelMap }) {
  const inputs = ledger?.inputs || [];
  if (!inputs.length) return null;
  return <details className="ct-v2-run-input-consumption"><summary>输入消费记录（{inputs.length} 项）</summary><div>{inputs.map((input) => {
    const activity = (input.stage_consumption || []).filter((item) => item.status && item.status !== "planned");
    return <article key={input.input_id}><header><strong>{publicNodeText(input.label || input.input_id, nodeLabels)}</strong><span>{publicNodeText(input.input_type || "输入", nodeLabels)}</span><small>{activity.length} 个已记录阶段</small></header><p>{publicNodeText(input.summary || "已冻结输入", nodeLabels)}</p><div className="ct-v2-input-consumption-stages">{activity.length ? activity.map((item, index) => <span key={`${input.input_id}-${item.stage_id}-${index}`} className={`is-${item.status || "planned"}`}>{publicNodeText(`${stageDisplayName(item.stage_id || "")}${item.artifact ? ` · ${item.artifact}` : ""}`, nodeLabels)}</span>) : <small>尚未有阶段消费记录</small>}</div></article>;
  })}</div></details>;
}
function FailurePanel({ summary, onRetry, busy }: { summary: PreparedWorkbenchTaskRun["run_ui_summary"]; onRetry: () => void; busy: boolean }) { const failure = summary?.failure; const node = summary?.nodes.find((item) => item.id === failure?.failed_node_id); const nodeName = node ? publicNodeLabel(node) : "运行节点"; const preflightTitle = failure?.preflight_kind === "independent_quality_audit" ? "独立质量核验未就绪" : "执行器启动前检查未通过"; const interrupted = node?.status === "interrupted"; return <section className="ct-v2-run-failure"><AlertTriangle size={18} /><div><h2>{failure?.preflight_blocked ? preflightTitle : interrupted ? `${nodeName}运行已中断` : `${nodeName}执行失败`}</h2><p>{failure?.reasons?.[0] || "执行器未完成当前节点，请查看公开事件或技术诊断。"}</p><dl><div><dt>用户目标阶段</dt><dd>{publicNodeText(failure?.user_goal_stage || nodeName || "当前节点")}</dd></div><div><dt>失败性质</dt><dd>{failure?.failure_class === "configuration" ? "配置问题" : "运行时问题"}</dd></div><div><dt>已保留上游结果</dt><dd>{failure?.preserved_node_labels?.map((value) => publicNodeText(value)).join("、") || "无"}</dd></div><div><dt>重试时复用</dt><dd>{failure?.reuse_node_labels?.map((value) => publicNodeText(value)).join("、") || "无"}</dd></div><div><dt>重试时重跑</dt><dd>{failure?.rerun_node_labels?.map((value) => publicNodeText(value)).join("、") || nodeName}</dd></div><div><dt>推荐操作</dt><dd>{failure?.recommended_action || "查看公开事件后创建新 Attempt。"}</dd></div></dl></div>{failure?.preflight_blocked ? <Link href="/settings"><Wrench size={14} />检查执行器设置</Link> : failure?.can_retry && <button disabled={busy} onClick={onRetry}><RefreshCw size={14} />从失败节点重试</button>}</section>; }
function artifactDownloadHref(runId: string, path: string) { const encoded = path.split("/").map(encodeURIComponent).join("/"); return `${currentApiBase()}/api/workbench/task-runs/${encodeURIComponent(runId)}/artifacts/download/${encoded}`; }
function artifactRowKey(item: WorkbenchTaskArtifact, index: number) { return [item.audience || "artifact", item.kind || "file", item.relative_path || item.path, item.sha256 || item.size_bytes || "unknown", index].join(":"); }
function ArtifactRow({ item, runId, onOpen, nodeLabels }: { item: WorkbenchTaskArtifact; runId: string; onOpen: (path: string) => void; nodeLabels: NodeLabelMap }) { const path = item.relative_path || item.path; const displayName = artifactDisplayName(path, nodeLabels); return <article className="ct-v2-artifact-row"><FileText size={15} /><button onClick={() => void onOpen(path)}><strong>{displayName}</strong><small>{displayName} · {formatBytes(item.size_bytes)}</small></button><a title="下载文件" href={artifactDownloadHref(runId, path)}><Download size={15} /></a></article>; }
function artifactDisplayName(path: string, nodeLabels: NodeLabelMap = new Map()) { const name=path.split("/").pop() || path; return ({"test_design_mindmap.json":"测试设计脑图（结构化）","test_design_mindmap.html":"测试设计脑图（交互版）","test_design_mindmap.svg":"测试设计脑图（评审版）","judge_report.json":"覆盖质量判定"} as Record<string,string>)[name] || publicNodeText(name, nodeLabels); }
function EventRow({ item, nodeLabels, compact = false }: { item: WorkbenchTaskRunEvent; nodeLabels: NodeLabelMap; compact?: boolean }) { const detail = eventDetail(item, nodeLabels); return <article className={`ct-v2-event-row is-${item.event_kind} ${compact ? "is-compact" : ""}`}><time>{new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{eventNodeLabel(item, nodeLabels)}</span><em>{eventKindLabel(item.event_kind)}</em><div><strong>{eventMessage(item, nodeLabels)}</strong>{!compact && detail && <pre>{detail}</pre>}</div></article>; }
type PairedToolCall = { id: string; use?: WorkbenchTaskRunEvent; result?: WorkbenchTaskRunEvent };
function pairedToolCalls(events: WorkbenchTaskRunEvent[]): PairedToolCall[] { const rows: PairedToolCall[] = []; const pending = new Map<string, PairedToolCall[]>(); for (const event of events) { const key = String(event.payload.call_id || event.payload.tool_call_id || event.payload.id || event.payload.tool || event.payload.name || "tool"); if (event.event_kind === "tool_use") { const row = { id: `tool-${event.event_id}`, use: event }; rows.push(row); const queue = pending.get(key) || []; queue.push(row); pending.set(key, queue); continue; } if (event.event_kind === "tool_result") { const row = pending.get(key)?.shift(); if (row) row.result = event; else rows.push({ id: `tool-result-${event.event_id}`, result: event }); } } return rows; }
function ToolCallRow({ item, nodeLabels }: { item: PairedToolCall; nodeLabels: NodeLabelMap }) { const source = item.use || item.result; if (!source) return null; const tool = String(source.payload.tool || source.payload.name || "工具"); const resultSummary = item.result ? eventDetail(item.result, nodeLabels) || eventMessage(item.result, nodeLabels) : "等待工具返回结果"; return <article className={`ct-v2-tool-call ${item.result ? "is-complete" : "is-running"}`}><header><time>{new Date(source.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><strong>{tool}</strong><span>{item.result ? "已完成" : "调用中"}</span></header><p>{item.use ? eventMessage(item.use, nodeLabels) : "收到工具结果"}</p><pre>{resultSummary}</pre></article>; }
function mergeEvents(current: WorkbenchTaskRunEvent[], incoming: WorkbenchTaskRunEvent[], direction: "live" | "older" = "live") { const map = new Map(current.map((item) => [item.event_id, item])); incoming.forEach((item) => map.set(item.event_id, item)); const ordered = [...map.values()].sort((a, b) => a.event_id - b.event_id); return direction === "older" ? ordered.slice(0, MAX_LOADED_EVENTS) : ordered.slice(-MAX_LOADED_EVENTS); }
function applyLifecycleEvents(run: PreparedWorkbenchTaskRun, events: WorkbenchTaskRunEvent[]) {
  // The API's persisted terminal status is authoritative. A terminal run can
  // legitimately contain a later step_failed event (for example, a quality
  // gate that preserved deliverables as partial); letting that event overwrite
  // the persisted partial status turns a quality diagnosis into a fake agent
  // execution failure in the cockpit.
  if (terminalStatuses.has(statusOf(run))) return run;
  const event = [...events].reverse().find((item) => lifecycleEventTypes.has(item.event_type));
  if (!event) return run;
  const status = lifecycleStatus(event.event_type, event.payload);
  if (!status) return run;
  const nodeId = eventNodeId(event);
  const summary = run.run_ui_summary;
  const statusLabel = status === "waiting_for_input"
    ? "等待人工审批"
    : taskStatusLabel(taskExecutionLabels, status);
  const nodes = summary?.nodes.map((node) => {
    const nodeEvent = events.filter((item) => eventNodeId(item) === node.id)
      .reverse()
      .find((item) => nodeLifecycleStatus(item));
    if (!nodeEvent) return node;
    const nodeStatus = nodeLifecycleStatus(nodeEvent);
    const approvalContext = lifecycleApprovalContext(nodeEvent);
    return {
      ...node,
      status: nodeStatus,
      status_label: nodeStatus === "waiting_for_input"
        ? "等待人工审批"
        : taskStatusLabel(taskExecutionLabels, nodeStatus),
      started_at: node.started_at || nodeEvent.created_at,
      completed_at: nodeStatus === "completed"
        ? node.completed_at || nodeEvent.created_at
        : node.completed_at,
      ...(approvalContext ? { approval_context: approvalContext } : {}),
    };
  });
  const currentNode = lifecycleEventNode(
    nodes,
    status,
    nodeId,
    summary?.current_node,
  );
  return {
    ...run,
    execution_status: status,
    runtime: { ...run.runtime, status, started_at: run.runtime?.started_at || (status === "running" ? event.created_at : undefined) },
    started_at: run.started_at || (status === "running" ? event.created_at : undefined),
    run_ui_summary: summary ? {
      ...summary,
      status,
      status_label: statusLabel,
      current_node: currentNode,
      nodes: nodes || summary.nodes,
    } : summary,
  };
}
function lifecycleEventNode(
  nodes: WorkbenchRunUiNodeSummary[] | undefined,
  status: string,
  nodeId: string,
  currentNode: WorkbenchRunUiNodeSummary | undefined,
) {
  if (nodeId) return nodes?.find((node) => node.id === nodeId) || currentNode;
  if (status === "waiting_for_input") {
    return nodes?.find((node) => node.status === "waiting_for_input")
      || nodes?.find(
        (node) => node.type === "human_approval" && !terminalStatuses.has(node.status || ""),
      )
      || currentNode;
  }
  return currentNode;
}
function nodeLifecycleStatus(event: WorkbenchTaskRunEvent) {
  if (["step_started", "node_started"].includes(event.event_type)) return "running";
  if (["step_completed", "node_completed", "node_reused"].includes(event.event_type)) return "completed";
  if (["waiting_for_input", "node_waiting"].includes(event.event_type)) return "waiting_for_input";
  if (["step_failed", "node_failed"].includes(event.event_type)) {
    return String(event.payload.status || "").toLowerCase() === "interrupted"
      ? "interrupted"
      : "failed";
  }
  return "";
}
function lifecycleApprovalContext(event: WorkbenchTaskRunEvent): WorkbenchRunUiNodeSummary["approval_context"] | undefined {
  const value = event.payload.approval_context;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const context = value as Record<string, unknown>;
  const summary = typeof context.summary === "string" ? context.summary : undefined;
  const sha256 = typeof context.sha256 === "string" ? context.sha256 : undefined;
  const truncated = typeof context.truncated === "boolean" ? context.truncated : undefined;
  if (summary === undefined && sha256 === undefined && truncated === undefined) return undefined;
  return { summary, sha256, truncated };
}
function lifecycleStatus(eventType: string, payload: WorkbenchTaskRunEvent["payload"] = {}) {
  if (["queued"].includes(eventType)) return "queued";
  if (["running", "step_started", "node_started", "step_completed", "node_completed"].includes(eventType)) return "running";
  if (["waiting_for_input", "node_waiting"].includes(eventType)) return "waiting_for_input";
  if (["completed", "partial", "failed", "error", "cancelled", "interrupted", "quality_blocked"].includes(eventType)) return eventType;
  if (["step_failed", "node_failed", "provider_readiness_blocked"].includes(eventType)) {
    const reported = String(payload.status || "").toLowerCase();
    if (reported === "quality_blocked") return "quality_blocked";
    if (reported === "interrupted") return "interrupted";
    return "failed";
  }
  return "";
}
function executionStatusLabel(status: string) {
  return status === "waiting_for_input"
    ? "等待人工审批"
    : taskStatusLabel(taskExecutionLabels, status);
}
function statusOf(run: PreparedWorkbenchTaskRun) { return String(run.execution_status || run.runtime?.status || run.status || "prepared").toLowerCase(); }
type V3RunAxes = { execution: string; artifactValidation: string; governance: string; delivery: string; unsupportedVersion?: string };
function v3RunAxes(run: PreparedWorkbenchTaskRun): V3RunAxes | null {
  const record = run as PreparedWorkbenchTaskRun & Record<string, unknown>;
  const snapshot = record.workflow_snapshot as Record<string, unknown> | undefined;
  const bundle = record.task_bundle as Record<string, unknown> | undefined;
  const version = record.compiled_contract_version ?? snapshot?.compiled_contract_version ?? bundle?.compiled_contract_version;
  if (version === undefined || version === null || version === "") return null;
  if (typeof version !== "number" || version !== 3) return {
    execution: statusOf(run),
    artifactValidation: "failed",
    governance: "not_requested",
    delivery: "blocked",
    unsupportedVersion: String(version),
  };
  return {
    execution: statusOf(run),
    artifactValidation: String(record.artifact_validation_status || "not_started"),
    governance: String(record.governance_status || "not_requested"),
    delivery: String(record.delivery_status || "pending"),
  };
}
function eventNodeId(item: WorkbenchTaskRunEvent) { return String(item.payload.step_id || item.payload.node_id || ""); }
function eventNodeLabel(item: WorkbenchTaskRunEvent, nodeLabels: NodeLabelMap) {
  const nodeId = eventNodeId(item);
  const mapped = nodeId ? nodeLabels.get(nodeId) : "";
  return mapped || publicNodeText(item.payload.node_label, nodeLabels) || eventKindNodeLabel(item);
}
function eventKindNodeLabel(item: WorkbenchTaskRunEvent) {
  if (["waiting_for_input", "node_waiting", "human_approval_decided"].includes(item.event_type)) return "人工审批";
  if (["tool_use", "tool_result"].includes(item.event_kind)) return "工具调用";
  return eventNodeId(item) ? "工作流节点" : "系统";
}
function eventMessage(item: WorkbenchTaskRunEvent, nodeLabels: NodeLabelMap = new Map()) {
  const message = String(item.payload.user_message || item.payload.message || eventTypeLabel(item.event_type));
  const translated = ({ "run completed": "运行已结束", "node blocked": "节点因上游门禁阻断" } as Record<string, string>)[message.toLowerCase()] || message;
  const stageLabels: Record<string, string> = {
    source_analysis: "源码证据",
    flow_evidence_pack: "调用链证据",
    flow_outline: "流程骨架",
    business_flow: "业务流程",
    sfmea: "SFMEA",
    black_box_cases: "黑盒用例",
    behavior_claim_validation: "独立事实核验",
  };
  return publicNodeText(Object.entries(stageLabels).reduce((value, [stageId, label]) => value.replaceAll(stageId, label), translated), nodeLabels);
}
function eventDetail(item: WorkbenchTaskRunEvent, nodeLabels: NodeLabelMap = new Map()) { const value = item.payload.delta ?? item.payload.text ?? item.payload.output ?? item.payload.error ?? item.payload.detail ?? ""; const text = typeof value === "string" ? value : value ? JSON.stringify(value, null, 2) : ""; return publicNodeText(text, nodeLabels); }
function eventClipboardLine(item: WorkbenchTaskRunEvent, nodeLabels: NodeLabelMap) { return `[${new Date(item.created_at).toLocaleTimeString("zh-CN", { hour12: false })}] ${eventNodeLabel(item, nodeLabels)} ${eventMessage(item, nodeLabels)} ${eventDetail(item, nodeLabels)}`.trim(); }
function eventKindLabel(kind: string) { return ({ status: "状态", done: "完成", artifact: "产物", output: "输出", error: "错误", thinking: "思考", reasoning: "推理", diagnostic: "诊断", trace: "跟踪", tool_use: "工具调用", tool_result: "工具结果" } as Record<string, string>)[kind] || kind; }
function eventTypeLabel(type: string) { return ({ queued: "已进入运行队列", running: "运行已开始", node_started: "节点开始执行", step_started: "节点开始执行", node_completed: "节点执行完成", step_completed: "节点执行完成", node_checkpoint_committed: "节点进度已持久保存", v3_status_updated: "运行状态已同步", run_completed: "本轮执行已结束", waiting_for_input: "等待人工审批", node_waiting: "节点等待人工审批", step_failed: "节点执行失败", quality_blocked: "执行完成，质量待修复", node_reused: "已复用已完成节点", completed: "运行已完成", partial: "运行保留了部分结果", cancelled: "运行已取消", artifact_created: "产物已生成", agent_output: "执行器产生新输出" } as Record<string, string>)[type] || type.replaceAll("_", " "); }
function qualityMessage(run: PreparedWorkbenchTaskRun) { const status = run.quality_status || "not_checked"; if (status === "passed") return "结构、事实和可执行性门禁已通过；请同时确认本次交付的覆盖范围。"; if (status === "warning") return "核心交付件可下载，但本次仅覆盖已声明范围；请查看待补覆盖场景后决定是否升级为深度型。"; if (status === "blocked") return "质量门禁未通过，请先修复阻断项。"; if (status === "pending") return "正在检查产物完整性和质量。"; return "本次运行尚未执行质量检查。"; }
function formatNodeTime(value?: string) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "尚未开始"; }
function formatNodeDuration(value?: number) { if (!value) return "尚未完成"; const seconds = Math.floor(value / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function formatDuration(start?: string, end?: string, nowMs = Date.now()) { if (!start) return "—"; const milliseconds = Math.max(0, new Date(end || nowMs).getTime() - new Date(start).getTime()); const seconds = Math.floor(milliseconds / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function formatMilliseconds(value?: number) { const milliseconds = Math.max(0, Number(value) || 0); const seconds = Math.floor(milliseconds / 1000); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
function buildNodeLabelMap(nodes: WorkbenchRunUiNodeSummary[]): NodeLabelMap { return new Map(nodes.map((node) => [node.id, publicNodeLabel(node)])); }
function publicNodeLabel(node: Pick<WorkbenchRunUiNodeSummary, "id" | "label" | "type">) { const label = String(node.label || "").trim(); return label && label !== node.id && !isInternalWorkflowNodeId(label) ? displayNodeName(label) : nodeKindLabel(node.type); }
function nodeKindLabel(kind?: string) { return ({ human_approval: "人工审批", agent_task: "智能任务", builtin_llm: "模型任务", llm_task: "模型任务", validator: "校验节点", governance: "治理节点", evidence_validate: "证据校验", report_render: "报告生成", tool: "工具节点" } as Record<string, string>)[String(kind || "")] || "工作流节点"; }
function referencedNodeLabels(ids: string[] | undefined, labels: string[] | undefined, nodeLabels: NodeLabelMap) { const references = ids?.length ? ids : labels || []; return references.map((id, index) => nodeLabels.get(id) || publicNodeText(labels?.[index], nodeLabels) || "工作流节点"); }
function publicNodeText(value: unknown, nodeLabels: NodeLabelMap = new Map()) { const text = String(value || "").trim(); if (!text) return ""; const labeled = [...nodeLabels.entries()].reduce((result, [nodeId, label]) => result.replaceAll(nodeId, label), text); return labeled.replace(/\b(?:node|agent|validator|governance|approval|human_approval|step|task_run|profile|input|output|port|contract)_[A-Za-z0-9_-]+\b/g, "工作流节点").replace(/\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/gi, "工作流节点"); }
function isInternalWorkflowNodeId(value: string) { return /^(?:node|agent|validator|governance|approval|human_approval|step|input|output|port|contract)_[A-Za-z0-9_-]+$/.test(value) || /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value); }
function publicDiagnosticJson(value: unknown, nodeLabels: NodeLabelMap) { return publicNodeText(JSON.stringify(value, null, 2), nodeLabels); }
function displayNodeName(value: string) { return ({ analyze_source_flow: "源码驱动测试分析", validate_evidence: "源码证据校验", render_report: "汇总报告生成" } as Record<string, string>)[value] || publicNodeText(value); }
function displayNodeType(value: string) { return ({ agent_task: "智能分析", builtin_llm: "模型任务", llm_task: "模型任务", evidence_validate: "证据校验", report_render: "报告生成", human_approval: "人工审批", validator: "校验节点", governance: "治理节点", tool: "工具节点", subagent: "子任务" } as Record<string, string>)[value] || "工作流节点"; }
function displayNodeGoal(node: WorkbenchRunUiNodeSummary, nodeLabels: NodeLabelMap = new Map()) {
  if (node.id === "analyze_source_flow") return "先检查可用的 GitNexus 和 CGC 产物，再读取本地源码与测试证据，生成代码证据、外部可观察流程、SFMEA 和可执行黑盒测试用例。";
  const goal = publicNodeText(node.goal || "完成当前工作流阶段", nodeLabels).replace(/\s+/g, " ").trim();
  return goal.length > 180 ? `${goal.slice(0, 180)}…` : goal;
}
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }

"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  Bot,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  FilePlus2,
  FileText,
  FolderOpen,
  Loader2,
  MessageSquarePlus,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  PlayCircle,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  Trash2,
  User,
} from "lucide-react";
import { BASE as API_BASE, api } from "@/lib/api";
import type { AgentRuntime, AIContextReference, AIConversation, AIMessage, AIRunEvent, Workspace } from "@/lib/types";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

const QUICK_ACTIONS = [
  "解释这个测试设计背后的风险判断",
  "补充黑盒边界条件和异常路径",
  "把结论整理成可执行测试用例",
  "生成下一轮复跑计划",
];
const RAIL_VISIBLE_LIMIT = 24;
const MOBILE_RAIL_VISIBLE_LIMIT = 8;
const EVIDENCE_EXCERPT_PREVIEW_CHARS = 120;

function eventContent(event: AIRunEvent): string {
  const value = event.payload.content;
  return typeof value === "string" ? value : "";
}

function eventDiagnosticText(event: AIRunEvent): string {
  const value = event.payload.content ?? event.payload.message ?? event.payload.detail ?? event.payload.status;
  return typeof value === "string" ? value : "";
}

function eventKind(event: AIRunEvent): string {
  const value = event.payload.kind ?? event.payload.channel ?? event.payload.type;
  return typeof value === "string" ? value : "";
}

function actionTextField(action: AIMessage["actions"][number], field: string): string {
  const value = (action as Record<string, unknown>)[field];
  return typeof value === "string" ? value : "";
}

function actionId(action: AIMessage["actions"][number]): string {
  return actionTextField(action, "id");
}

function actionLabel(action: AIMessage["actions"][number]): string {
  return actionTextField(action, "label");
}

function actionHref(action: AIMessage["actions"][number]): string {
  return actionTextField(action, "href");
}

function resolvedActionHref(action: AIMessage["actions"][number]): string {
  const href = actionHref(action);
  if (href.startsWith("/api/")) return `${API_BASE}${href}`;
  return href;
}

function actionKind(action: AIMessage["actions"][number]): string {
  return actionTextField(action, "kind");
}

function isDiagnosticEvent(event: AIRunEvent): boolean {
  return ["diagnostic", "thinking", "reasoning", "trace"].includes(eventKind(event));
}

function agentProcessDiagnosticsFromEvents(events: AIRunEvent[]): string[] {
  const diagnostics: string[] = [];
  for (const event of events) {
    if (event.event_type === "status") {
      diagnostics.push(eventDiagnosticText(event));
    } else if (event.event_type === "delta" && isDiagnosticEvent(event)) {
      diagnostics.push(eventDiagnosticText(event));
    } else if (event.event_type === "error") {
      diagnostics.push(eventError(event));
    }
  }
  return diagnostics.map(redactDiagnosticText).filter(Boolean).slice(-12);
}

function eventError(event: AIRunEvent): string {
  const value = event.payload.error;
  return typeof value === "string" ? redactDiagnosticText(value) : "";
}

function threadWorkspaceId(thread: AIConversation | null): string {
  if (!thread) return "global";
  if (thread.workspace_id && thread.workspace_id !== "global") return thread.workspace_id;
  if (thread.scope_type === "workspace") return thread.scope_id;
  if (thread.scope_type === "module") return thread.scope_id.split(":")[0] ?? "global";
  return "global";
}

function publicWorkspaceLabel(workspace: Workspace | null, thread: AIConversation | null): string {
  const id = workspace?.id ?? threadWorkspaceId(thread);
  if (id && id !== "global") return `workspace:${id}`;
  return thread?.memory_namespace ?? "global";
}

function uniqueReferences(messages: AIMessage[]): AIContextReference[] {
  const map = new Map<string, AIContextReference>();
  for (const msg of messages) {
    for (const ref of msg.references ?? []) {
      map.set(`${ref.source_type}:${ref.source_id}`, ref);
    }
  }
  return Array.from(map.values()).slice(0, 12);
}

function sourceLocationLabel(ref: AIContextReference): string {
  if (ref.source_type === "workbench_task_artifact") {
    const taskRunId = typeof ref.metadata?.task_run_id === "string" ? ref.metadata.task_run_id.trim() : "";
    const sourceId = ref.source_id.trim();
    const artifactName =
      taskRunId && sourceId.startsWith(`${taskRunId}/`) ? sourceId.slice(taskRunId.length + 1) : ref.title.trim();
    return [taskRunId, artifactName].filter(Boolean).join(" · ");
  }
  if (ref.source_type !== "workspace_source") return "";
  const path = ref.metadata?.path;
  if (typeof path !== "string" || !path.trim()) return "";
  const start = ref.metadata?.start_line;
  const end = ref.metadata?.end_line;
  const startLine = typeof start === "number" && Number.isFinite(start) ? Math.max(1, Math.trunc(start)) : null;
  const endLine = typeof end === "number" && Number.isFinite(end) ? Math.max(1, Math.trunc(end)) : null;
  if (startLine && endLine && endLine !== startLine) return `${path}:L${startLine}-L${endLine}`;
  if (startLine) return `${path}:L${startLine}`;
  return path;
}

function isSafeRelativeReferencePath(path: string): boolean {
  const normalized = path.trim();
  if (!normalized) return false;
  if (normalized.startsWith("/") || normalized.startsWith("\\") || /^[A-Za-z]:[\\/]/.test(normalized)) return false;
  const parts = normalized.split(/[\\/]+/);
  return parts.every((part) => part !== "" && part !== "." && part !== "..");
}

function sourceReferenceHref(ref: AIContextReference): string {
  if (ref.source_type !== "workspace_source") return "";
  const workspaceId = ref.metadata?.workspace_id;
  const path = ref.metadata?.path;
  if (typeof workspaceId !== "string" || !workspaceId.trim()) return "";
  if (typeof path !== "string" || !path.trim()) return "";
  if (!isSafeRelativeReferencePath(path)) return "";
  const start = ref.metadata?.start_line;
  const startLine = typeof start === "number" && Number.isFinite(start) ? Math.max(1, Math.trunc(start)) : null;
  const query = new URLSearchParams({
    tab: "source",
    sourcePath: path,
    ...(startLine ? { line: String(startLine) } : {}),
  });
  return `/workspaces/${encodeURIComponent(workspaceId)}?${query.toString()}`;
}

function artifactReferenceHref(ref: AIContextReference): string {
  if (ref.source_type !== "workbench_task_artifact") return "";
  const taskRunId = typeof ref.metadata?.task_run_id === "string" ? ref.metadata.task_run_id.trim() : "";
  if (!taskRunId) return "";
  const sourceId = ref.source_id.trim();
  const artifactPath =
    sourceId && sourceId.startsWith(`${taskRunId}/`) ? sourceId.slice(taskRunId.length + 1) : ref.title.trim();
  if (!isSafeRelativeReferencePath(artifactPath)) return "";
  const encodedPath = artifactPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/artifacts/content/${encodedPath}`;
}

function compactEvidenceExcerpt(excerpt: string): { preview: string; remainder: string } {
  const text = redactDiagnosticText(String(excerpt || "")).replace(/\s+/g, " ").trim();
  if (text.length <= EVIDENCE_EXCERPT_PREVIEW_CHARS) return { preview: text, remainder: "" };
  return {
    preview: `${text.slice(0, EVIDENCE_EXCERPT_PREVIEW_CHARS - 1).trimEnd()}…`,
    remainder: text.slice(EVIDENCE_EXCERPT_PREVIEW_CHARS).trim(),
  };
}

function EvidenceReferenceCard({ refItem }: { refItem: AIContextReference }) {
  const sourceLocation = sourceLocationLabel(refItem);
  const sourceHref = sourceReferenceHref(refItem);
  const artifactHref = artifactReferenceHref(refItem);
  const excerpt = compactEvidenceExcerpt(refItem.excerpt ?? "");
  return (
    <div className="ct-ai-ref">
      <div className="flex items-center justify-between gap-2">
        <span>{refItem.title}</span>
        <code>{refItem.source_type}</code>
      </div>
      {refItem.source_type === "workspace_source" && sourceLocation && (
        <div className="ct-ai-ref__meta">
          <span>源码位置</span>
          <code>{sourceLocation}</code>
          {sourceHref && <Link href={sourceHref}>打开源码</Link>}
        </div>
      )}
      {refItem.source_type === "workbench_task_artifact" && sourceLocation && (
        <div className="ct-ai-ref__meta">
          <span>任务产物</span>
          <code>{sourceLocation}</code>
          {artifactHref && <Link href={artifactHref}>打开产物</Link>}
        </div>
      )}
      {excerpt.preview && (
        <>
          <p>{excerpt.preview}</p>
          {excerpt.remainder && (
            <details className="ct-ai-ref__excerpt">
              <summary>展开证据片段</summary>
              <p>{excerpt.remainder}</p>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function AgentProcessDisclosure({ diagnostics }: { diagnostics: string[] }) {
  const [open, setOpen] = useState(false);
  const visibleDiagnostics = diagnostics.map(redactDiagnosticText).filter(Boolean).slice(-12);
  if (visibleDiagnostics.length === 0) return null;
  return (
    <details
      className="ct-agent-process"
      data-testid="agent-process-disclosure"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>Agent 过程</span>
        <em>默认折叠 · {visibleDiagnostics.length} 条</em>
      </summary>
      {open && (
        <div>
          {visibleDiagnostics.map((item, index) => (
            <p key={`${index}-${item}`}>{item}</p>
          ))}
        </div>
      )}
    </details>
  );
}

function safeFilename(value: string): string {
  const trimmed = value
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return trimmed || "ai-thread";
}

function redactDiagnosticText(value: string): string {
  return value
    .replace(/(\b(?:api[-_]?key|token|access[-_]?token|secret|password)=)(['"]?)([^\s"']+)(['"]?)/gi, "$1$2<redacted>$4")
    .replace(/((?:"|')?(?:api[-_]?key|token|access[-_]?token|secret|password)(?:"|')?\s*:\s*)(["']?)([^\s"',}]+)(["']?)/gi, "$1$2<redacted>$4")
    .replace(/(--?(?:api[-_]?key|token|access[-_]?token|secret|password)(?:\s+|=))(['"]?)([^\s"']+)(['"]?)/gi, "$1$2<redacted>$4")
    .replace(/(Authorization:\s*Bearer\s+)[^\s"']+/gi, "$1<redacted>")
    .replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g, "<redacted>");
}

function buildThreadMarkdown(conversation: AIConversation | null, messages: AIMessage[]): string {
  const title = conversation?.title ?? "AI 调查线程";
  const lines = [
    `# ${title}`,
    "",
    `- 线程 ID: ${conversation?.id ?? "unknown"}`,
    `- 范围: ${conversation?.scope_type ?? "unknown"} / ${conversation?.scope_id ?? "unknown"}`,
    `- 记忆命名空间: ${conversation?.memory_namespace ?? "global"}`,
    `- 导出时间: ${new Date().toISOString()}`,
    "",
  ];

  if (conversation?.latest_run?.status === "failed" && conversation.latest_run.error) {
    lines.push("## 最近失败");
    lines.push("");
    lines.push(redactDiagnosticText(conversation.latest_run.error));
    lines.push("");
  }

  for (const message of messages) {
    lines.push(`## ${message.role === "user" ? "用户" : message.role === "assistant" ? "AI" : "系统"}`);
    lines.push("");
    if (message.created_at) {
      lines.push(`- 时间: ${redactDiagnosticText(message.created_at)}`);
      lines.push("");
    }
    lines.push(message.content ? redactDiagnosticText(message.content) : "_空消息_");
    if (message.references?.length) {
      lines.push("");
      lines.push("### 证据引用");
      for (const ref of message.references) {
        const location = sourceLocationLabel(ref);
        const sourceHref = sourceReferenceHref(ref);
        const artifactHref = artifactReferenceHref(ref);
        lines.push(`- ${redactDiagnosticText(ref.title)} (${ref.source_type}:${ref.source_id})`);
        if (location) {
          const label = ref.source_type === "workbench_task_artifact" ? "任务产物" : "源码位置";
          lines.push(`  - ${label}: ${redactDiagnosticText(location)}`);
        }
        if (sourceHref) lines.push(`  - 源码链接: ${redactDiagnosticText(sourceHref)}`);
        if (artifactHref) lines.push(`  - 产物链接: ${redactDiagnosticText(artifactHref)}`);
        if (ref.excerpt) lines.push(`  - ${redactDiagnosticText(ref.excerpt)}`);
      }
    }
    lines.push("");
  }

  return lines.join("\n");
}

export default function AIThreadPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const [conversation, setConversation] = useState<AIConversation | null>(null);
  const [messages, setMessages] = useState<AIMessage[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [threads, setThreads] = useState<AIConversation[]>([]);
  const [agentRuntimes, setAgentRuntimes] = useState<AgentRuntime[]>([]);
  const [savingRuntime, setSavingRuntime] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [creatingSiblingThread, setCreatingSiblingThread] = useState(false);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [streamingRunId, setStreamingRunId] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [streamingDiagnostics, setStreamingDiagnostics] = useState<string[]>([]);
  const [contextOpen, setContextOpen] = useState(true);
  const [generationDiagnosticsOpen, setGenerationDiagnosticsOpen] = useState(false);
  const [railProjectQuery, setRailProjectQuery] = useState("");
  const [railThreadQuery, setRailThreadQuery] = useState("");
  const [mobileRail, setMobileRail] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const cancellingRef = useRef(false);
  const creatingSiblingThreadRef = useRef(false);
  const deletingThreadRef = useRef<string | null>(null);
  const readerRef = useRef<HTMLElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const detachedScrollTopRef = useRef(0);
  const programmaticScrollRef = useRef(false);
  const streamingActiveRef = useRef(false);

  const references = useMemo(() => uniqueReferences(messages), [messages]);
  const workspaceId = threadWorkspaceId(conversation);
  const workspace = workspaces.find((item) => item.id === workspaceId) ?? null;
  const activeRuntime = agentRuntimes.find((item) => item.id === conversation?.agent_runtime_id) ?? null;
  const normalizedProjectQuery = railProjectQuery.trim().toLowerCase();
  const normalizedThreadQuery = railThreadQuery.trim().toLowerCase();
  const matchingRailProjects = useMemo(
    () =>
      workspaces.filter((project) => {
        if (!normalizedProjectQuery) return true;
        return `${project.name} ${project.id} ${project.repo_path}`.toLowerCase().includes(normalizedProjectQuery);
      }),
    [normalizedProjectQuery, workspaces],
  );
  const railVisibleLimit = mobileRail ? MOBILE_RAIL_VISIBLE_LIMIT : RAIL_VISIBLE_LIMIT;
  const railProjects = useMemo(
    () => matchingRailProjects.slice(0, railVisibleLimit),
    [matchingRailProjects, railVisibleLimit],
  );
  const workspaceThreads = useMemo(
    () => threads.filter((thread) => threadWorkspaceId(thread) === workspaceId),
    [threads, workspaceId],
  );
  const matchingThreads = useMemo(
    () =>
      workspaceThreads.filter((thread) => {
        if (!normalizedThreadQuery) return true;
        return `${thread.title} ${thread.id} ${thread.status}`.toLowerCase().includes(normalizedThreadQuery);
      }),
    [normalizedThreadQuery, workspaceThreads],
  );
  const visibleThreads = useMemo(
    () => matchingThreads.slice(0, railVisibleLimit),
    [matchingThreads, railVisibleLimit],
  );
  const hiddenProjectCount = Math.max(0, matchingRailProjects.length - railProjects.length);
  const hiddenThreadCount = Math.max(0, matchingThreads.length - visibleThreads.length);
  const materialCount = workspace?.materials?.length ?? 0;
  const reportCount = workspace?.reports?.length ?? 0;
  const latestRun = conversation?.latest_run ?? null;
  const latestRunError =
    latestRun?.status === "failed" && latestRun.error
      ? redactDiagnosticText(latestRun.error)
      : "";
  const visibleError = error || latestRunError;
  const isActuallyRunning = Boolean(
    streamingRunId &&
      latestRun?.id === streamingRunId &&
      ["queued", "running"].includes(latestRun?.status ?? ""),
  );
  const composerDisabled = sending || isActuallyRunning;
  const threadNavigationBusy =
    savingRuntime || cancelling || creatingSiblingThread || Boolean(deletingThreadId) || isActuallyRunning;
  const lastUserMessage = useMemo(
    () => [...messages].reverse().find((message) => message.role === "user") ?? null,
    [messages],
  );
  const canRetryLatestFailure = Boolean(latestRunError && lastUserMessage && !sending && !isActuallyRunning);
  const canExportThread = messages.length > 0 && !isActuallyRunning;
  const latestReferences = references.slice(0, 4);
  const hiddenReferenceCount = Math.max(0, references.length - latestReferences.length);
  const runStatusLabel = isActuallyRunning
    ? "生成中"
    : latestRun?.status === "failed"
      ? "失败"
      : latestRun?.status === "completed"
        ? "已完成"
        : conversation?.status ?? "ready";
  const auditSummary = [
    `状态 ${runStatusLabel}`,
    `证据 ${references.length}`,
    `材料 ${materialCount}`,
    `报告 ${reportCount}`,
  ].join(" · ");

  const loadInitialPage = useCallback(async () => {
    setError(null);
    const [conv, msgResult, workspaceItems, runtimeResult] = await Promise.all([
      api.aiConversations.get(conversationId),
      api.aiConversations.messages(conversationId),
      api.workspaces.list(),
      api.settings.listAgentRuntimes().catch(() => ({ items: [] as AgentRuntime[] })),
    ]);
    setConversation(conv);
    setMessages(msgResult.items);
    setWorkspaces(workspaceItems);
    setAgentRuntimes(runtimeResult.items);
    const projectId = threadWorkspaceId(conv);
    const threadResult = await api.aiConversations.list(
      projectId === "global" ? { limit: 50 } : { workspace_id: projectId, limit: 50 },
    );
    setThreads(threadResult.items);
    if (conv.latest_run?.id) {
      const eventResult = await api.aiConversations
        .events(conversationId, { run_id: conv.latest_run.id, limit: 200, process_only: true })
        .catch(() => ({ items: [] as AIRunEvent[] }));
      setStreamingDiagnostics(agentProcessDiagnosticsFromEvents(eventResult.items));
    } else {
      setStreamingDiagnostics([]);
    }
    if (conv.latest_run?.status === "queued" || conv.latest_run?.status === "running") {
      setStreamingRunId(conv.latest_run.id);
    }
  }, [conversationId]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 760px)");
    const syncRailDensity = () => setMobileRail(query.matches);
    syncRailDensity();
    query.addEventListener("change", syncRailDensity);
    return () => query.removeEventListener("change", syncRailDensity);
  }, []);

  const refreshRunStatus = useCallback(async () => {
    const nextConversation = await api.aiConversations.get(conversationId);
    setConversation(nextConversation);
    return nextConversation;
  }, [conversationId]);

  const refreshMessagesAfterDone = useCallback(async () => {
    const [nextConversation, msgResult] = await Promise.all([
      api.aiConversations.get(conversationId),
      api.aiConversations.messages(conversationId),
    ]);
    setConversation(nextConversation);
    setMessages(msgResult.items);
    return nextConversation;
  }, [conversationId]);

  const streamRun = useCallback(
    async (runId: string, cursor = 0) => {
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;
      setStreamingDiagnostics([]);
      try {
        const res = await api.aiConversations.stream(conversationId, cursor, abort.signal);
        if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!abort.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const event = JSON.parse(line.slice(6)) as AIRunEvent;
            if (event.run_id !== runId) continue;
            if (event.event_type === "status") {
              const content = eventDiagnosticText(event);
              setStreamingDiagnostics((prev) => [...prev, redactDiagnosticText(content)].filter(Boolean).slice(-12));
            }
            if (event.event_type === "delta") {
              const content = eventContent(event);
              if (isDiagnosticEvent(event)) {
                const diagnostic = eventDiagnosticText(event);
                setStreamingDiagnostics((prev) => [...prev, redactDiagnosticText(diagnostic)].filter(Boolean).slice(-12));
              } else {
                setStreamingContent((prev) => prev + content);
              }
            }
            if (event.event_type === "done" || event.event_type === "error") {
              if (event.event_type === "error") {
                setError(eventError(event) || "AI 生成失败，请检查模型配置后重试。");
              }
              setStreamingRunId(null);
              await refreshMessagesAfterDone();
              setStreamingContent("");
              return;
            }
          }
        }
        if (!abort.signal.aborted) {
          const nextConversation = await refreshRunStatus().catch(() => null);
          const nextRun = nextConversation?.latest_run;
          if (
            nextRun?.id === runId &&
            nextRun.status !== "queued" &&
            nextRun.status !== "running"
          ) {
            setStreamingRunId(null);
            await refreshMessagesAfterDone();
            setStreamingContent("");
          }
        }
      } catch (exc) {
        if (!abort.signal.aborted) {
          setError(exc instanceof Error ? exc.message : "订阅生成状态失败");
          const nextConversation = await refreshRunStatus().catch(() => null);
          const nextRun = nextConversation?.latest_run;
          if (
            nextRun?.id === runId &&
            nextRun.status !== "queued" &&
            nextRun.status !== "running"
          ) {
            setStreamingRunId(null);
            await refreshMessagesAfterDone().catch(() => {});
            setStreamingContent("");
          }
        }
      }
    },
    [conversationId, refreshMessagesAfterDone, refreshRunStatus],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadInitialPage()
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "加载线程失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [loadInitialPage]);

  useEffect(() => {
    if (!streamingRunId) return;
    void streamRun(streamingRunId, 0);
    return () => abortRef.current?.abort();
  }, [streamingRunId, streamRun]);

  useEffect(() => {
    if (!streamingRunId) return;
    const timer = window.setInterval(() => {
      void api.aiConversations
        .get(conversationId)
        .then(async (nextConversation) => {
          setConversation(nextConversation);
          if (nextConversation.latest_run?.id !== streamingRunId) return;
          if (nextConversation.latest_run.status === "queued" || nextConversation.latest_run.status === "running") return;
          setStreamingRunId(null);
          await refreshMessagesAfterDone();
          setStreamingContent("");
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [conversationId, refreshMessagesAfterDone, streamingRunId]);

  useEffect(() => {
    streamingActiveRef.current = Boolean(streamingRunId || streamingContent);
  }, [streamingContent, streamingRunId]);

  const updateReaderStickiness = useCallback(() => {
    const reader = readerRef.current;
    if (!reader) return;
    const distanceFromBottom = reader.scrollHeight - reader.scrollTop - reader.clientHeight;
    const nearBottom = distanceFromBottom < 96;
    const hasReadableContent = messages.length > 0 || Boolean(streamingContent);
    if (programmaticScrollRef.current) {
      autoScrollRef.current = true;
      setShowJumpToLatest(false);
      return;
    }
    autoScrollRef.current = nearBottom;
    if (!nearBottom) {
      detachedScrollTopRef.current = reader.scrollTop;
    }
    setShowJumpToLatest(!nearBottom && hasReadableContent);
  }, [messages.length, streamingContent]);

  const detachAutoScroll = useCallback(() => {
    const reader = readerRef.current;
    if (reader) {
      detachedScrollTopRef.current = reader.scrollTop;
    }
    autoScrollRef.current = false;
    if (streamingRunId || streamingContent) setShowJumpToLatest(true);
  }, [streamingContent, streamingRunId]);

  const handleReaderKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (!["ArrowUp", "PageUp", "Home"].includes(event.key)) return;
      detachAutoScroll();
    },
    [detachAutoScroll],
  );

  useEffect(() => {
    const reader = readerRef.current;
    if (!reader) return undefined;
    const handleNativeWheel = (event: WheelEvent) => {
      if (event.deltaY >= 0) return;
      const nextScrollTop = Math.max(0, reader.scrollTop + event.deltaY);
      event.preventDefault();
      reader.scrollTop = nextScrollTop;
      detachedScrollTopRef.current = nextScrollTop;
      autoScrollRef.current = false;
      if (streamingActiveRef.current) setShowJumpToLatest(true);
    };
    reader.addEventListener("wheel", handleNativeWheel, { passive: false, capture: true });
    return () => reader.removeEventListener("wheel", handleNativeWheel, { capture: true });
  }, [loading]);

  const jumpToLatest = useCallback((behavior: ScrollBehavior = "smooth", protectScrollHandler = false) => {
    programmaticScrollRef.current = protectScrollHandler;
    autoScrollRef.current = true;
    setShowJumpToLatest(false);
    const reader = readerRef.current;
    if (reader) {
      reader.scrollTo({ top: reader.scrollHeight, behavior });
    } else {
      bottomRef.current?.scrollIntoView({ behavior, block: "end" });
    }
    window.requestAnimationFrame(() => {
      const nextReader = readerRef.current;
      if (nextReader && (protectScrollHandler || autoScrollRef.current)) {
        nextReader.scrollTop = nextReader.scrollHeight;
      }
      programmaticScrollRef.current = false;
    });
  }, []);

  useLayoutEffect(() => {
    const reader = readerRef.current;
    if (autoScrollRef.current) {
      jumpToLatest(streamingRunId || streamingContent ? "auto" : "smooth");
    } else if (reader && (messages.length > 0 || streamingContent)) {
      const targetScrollTop = detachedScrollTopRef.current;
      reader.scrollTop = targetScrollTop;
      window.requestAnimationFrame(() => {
        if (!autoScrollRef.current && readerRef.current === reader) {
          reader.scrollTop = targetScrollTop;
          const distanceFromBottom = reader.scrollHeight - reader.scrollTop - reader.clientHeight;
          setShowJumpToLatest(distanceFromBottom >= 96);
        }
      });
    }
  }, [jumpToLatest, messages.length, streamingContent, streamingRunId]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending || isActuallyRunning) return;
    await sendText(text);
  };

  const sendText = async (text: string) => {
    setSending(true);
    setError(null);
    setInput("");
    setStreamingContent("");
    setStreamingDiagnostics([]);
    setGenerationDiagnosticsOpen(false);
    autoScrollRef.current = true;
    setShowJumpToLatest(false);
    try {
      const result = await api.aiConversations.send(conversationId, text);
      setMessages((prev) => [...prev, result.message]);
      setConversation((prev) =>
        prev
          ? {
              ...prev,
              status: "running",
              latest_run: result.run,
            }
          : prev,
      );
      setStreamingRunId(result.run.id);
      setContextOpen(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "发送失败");
      setInput(text);
    } finally {
      setSending(false);
    }
  };

  const retryLatestFailure = async () => {
    const text = lastUserMessage?.content.trim();
    if (!text || !canRetryLatestFailure) return;
    await sendText(text);
  };

  const exportThreadMarkdown = () => {
    if (!canExportThread) return;
    const markdown = buildThreadMarkdown(conversation, messages);
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${safeFilename(conversation?.title ?? "ai-thread")}-${conversationId}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const cancel = async () => {
    if (cancellingRef.current) return;
    cancellingRef.current = true;
    setCancelling(true);
    try {
      abortRef.current?.abort();
      await api.aiConversations.cancel(conversationId).catch(() => {});
      setStreamingRunId(null);
      setStreamingContent("");
      setStreamingDiagnostics([]);
      await refreshMessagesAfterDone().catch(() => {});
    } finally {
      cancellingRef.current = false;
      setCancelling(false);
    }
  };

  const changeRuntime = async (value: string) => {
    if (!conversation || savingRuntime || isActuallyRunning) return;
    setSavingRuntime(true);
    setError(null);
    try {
      const updated = await api.aiConversations.update(conversation.id, {
        runtime_type: value === "builtin_llm" ? "builtin_llm" : "agent_runtime",
        agent_runtime_id: value === "builtin_llm" ? null : value,
      });
      setConversation(updated);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "切换执行器失败");
    } finally {
      setSavingRuntime(false);
    }
  };

  const createSiblingThread = async () => {
    if (!workspace || threadNavigationBusy || creatingSiblingThreadRef.current) return;
    creatingSiblingThreadRef.current = true;
    setCreatingSiblingThread(true);
    try {
      const next = await api.aiConversations.create({
        scope_type: "workspace",
        scope_id: workspace.id,
        workspace_id: workspace.id,
        memory_namespace: `workspace:${workspace.id}`,
        title: `${workspace.name} · 新调查`,
        initial_context: {
          workspace_id: workspace.id,
          project_name: workspace.name,
          memory_namespace: `workspace:${workspace.id}`,
        },
      });
      router.push(`/ai/${next.id}`);
    } finally {
      creatingSiblingThreadRef.current = false;
      setCreatingSiblingThread(false);
    }
  };

  const deleteThread = async (thread: AIConversation) => {
    if (thread.status === "running" || thread.latest_run?.status === "running" || thread.latest_run?.status === "queued") {
      setError("当前线程仍在生成中，请先停止后再删除。");
      return;
    }
    if (deletingThreadRef.current) return;
    const confirmed = window.confirm(`删除线程“${thread.title}”？这会删除该线程的消息和运行记录。`);
    if (!confirmed) return;
    deletingThreadRef.current = thread.id;
    setDeletingThreadId(thread.id);
    setError(null);
    try {
      await api.aiConversations.delete(thread.id);
      const nextThreads = threads.filter((item) => item.id !== thread.id);
      setThreads(nextThreads);
      if (thread.id === conversationId) {
        const fallback = nextThreads.find((item) => threadWorkspaceId(item) === workspaceId);
        router.push(fallback ? `/ai/${fallback.id}` : "/ai");
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "删除线程失败");
    } finally {
      deletingThreadRef.current = null;
      setDeletingThreadId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className={`ct-codex-ai ${contextOpen ? "is-context-open" : ""}`}>
      <aside className="ct-codex-ai__rail">
        {threadNavigationBusy ? (
          <span className="ct-codex-ai__back is-disabled" role="link" aria-disabled="true">
            <ChevronLeft size={16} />
            项目与线程
          </span>
        ) : (
          <Link href="/ai" className="ct-codex-ai__back">
            <ChevronLeft size={16} />
            项目与线程
          </Link>
        )}
        <div className="ct-codex-ai__project">
          <span>当前项目</span>
          <strong>{workspace?.name ?? "未绑定项目"}</strong>
          <small>{publicWorkspaceLabel(workspace, conversation)}</small>
        </div>
        <div className="ct-codex-ai__rail-group">
          <div className="ct-codex-ai__rail-label">
            <FolderOpen size={13} />
            项目
          </div>
          <input
            className="ct-codex-ai__rail-search"
            value={railProjectQuery}
            onChange={(event) => setRailProjectQuery(event.target.value)}
            placeholder="搜索项目"
            aria-label="搜索 AI 项目"
            disabled={threadNavigationBusy}
          />
          <div className="ct-codex-ai__project-list">
            {railProjects.map((project) => {
              const projectRowClass = `ct-codex-ai__project-row ${project.id === workspace?.id ? "is-active" : ""}`;
              const projectRowContent = (
                <>
                  <span>{project.name}</span>
                  <em>{project.reports.length + project.materials.length}</em>
                </>
              );
              return threadNavigationBusy ? (
                <span
                  key={project.id}
                  className={`${projectRowClass} is-disabled`}
                  role="link"
                  aria-disabled="true"
                >
                  {projectRowContent}
                </span>
              ) : (
                <Link key={project.id} href="/ai" className={projectRowClass}>
                  {projectRowContent}
                </Link>
              );
            })}
            {hiddenProjectCount > 0 && (
              <span className="ct-codex-ai__rail-more">
                已收起 {hiddenProjectCount} 个项目，输入关键字筛选
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          className="ct-codex-ai__new"
          onClick={createSiblingThread}
          disabled={!workspace || threadNavigationBusy}
        >
          <MessageSquarePlus size={15} />
          新建线程
        </button>
        <div className="ct-codex-ai__rail-label">
          <MessageSquareText size={13} />
          对话
        </div>
        <input
          className="ct-codex-ai__rail-search"
          value={railThreadQuery}
          onChange={(event) => setRailThreadQuery(event.target.value)}
          placeholder="搜索线程"
          aria-label="搜索 AI 线程"
          disabled={threadNavigationBusy}
        />
        <div className="ct-codex-ai__thread-list">
          {visibleThreads.map((thread) => {
            const threadClass = `ct-codex-ai__thread ${thread.id === conversationId ? "is-active" : ""}`;
            const threadContent = (
              <>
                <MessageSquareText size={14} />
                <span>{thread.title}</span>
                {thread.status === "running" && <Loader2 size={12} className="animate-spin" />}
              </>
            );
            return threadNavigationBusy ? (
              <div key={thread.id} className="ct-codex-ai__thread-row">
                <span
                  className={`${threadClass} is-disabled`}
                  role="link"
                  aria-disabled="true"
                >
                  {threadContent}
                </span>
              </div>
            ) : (
              <div key={thread.id} className="ct-codex-ai__thread-row">
                <Link href={`/ai/${thread.id}`} className={threadClass}>
                  {threadContent}
                </Link>
                <button
                  type="button"
                  className="ct-codex-ai__thread-delete"
                  onClick={() => void deleteThread(thread)}
                  disabled={deletingThreadId === thread.id}
                  title="删除线程"
                  aria-label={`删除线程 ${thread.title}`}
                >
                  {deletingThreadId === thread.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                </button>
              </div>
            );
          })}
          {hiddenThreadCount > 0 && (
            <span className="ct-codex-ai__rail-more">
              已收起 {hiddenThreadCount} 条线程，输入关键字筛选
            </span>
          )}
        </div>
      </aside>

      <main className="ct-codex-ai__main">
        <header className="ct-codex-ai__topbar">
          <div>
            <span>{conversation?.scope_type} / {conversation?.scope_id}</span>
            <h1>{conversation?.title ?? "AI 调查线程"}</h1>
          </div>
          <select
            value={conversation?.runtime_type === "agent_runtime" ? conversation.agent_runtime_id ?? "" : "builtin_llm"}
            onChange={(event) => void changeRuntime(event.target.value)}
            disabled={savingRuntime || isActuallyRunning}
            aria-label="当前 AI 执行器"
          >
            {agentRuntimes.map((runtime) => (
              <option key={runtime.id} value={runtime.id} disabled={!runtime.enabled}>
                {runtime.name}{runtime.enabled ? "" : "（已停用）"}
              </option>
            ))}
            <option value="builtin_llm">内置模型</option>
          </select>
          <button type="button" onClick={() => setContextOpen((value) => !value)}>
            {contextOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
            环境
          </button>
          <button type="button" onClick={exportThreadMarkdown} disabled={!canExportThread} title="导出 AI 线程为 Markdown">
            <Download size={17} />
            导出
          </button>
        </header>

        {visibleError && (
          <div className="ct-codex-ai__error" role="alert">
            <AlertCircle size={16} />
            <span>{visibleError}</span>
            {canRetryLatestFailure && (
              <button type="button" onClick={() => void retryLatestFailure()}>
                <RotateCcw size={14} />
                重试上一条
              </button>
            )}
            {visibleError.includes("未配置活跃的聊天模型") && (
              <Link href="/settings">去设置执行器</Link>
            )}
          </div>
        )}

        <div className="ct-codex-ai__reader-shell">
          <section
            ref={readerRef}
            className="ct-codex-ai__reader"
            tabIndex={0}
            onScroll={updateReaderStickiness}
            onKeyDown={handleReaderKeyDown}
            onTouchMove={detachAutoScroll}
            aria-label="AI 线程对话内容"
          >
            {messages.length === 0 && !streamingContent ? (
              <div className="ct-codex-ai__empty">
                <Sparkles size={32} />
                <p>直接提问。这个线程会持续保存，并只围绕当前项目命名空间召回记忆。</p>
              </div>
            ) : (
              messages.map((message) => (
                <article key={message.id} className={`ct-codex-message ${message.role === "user" ? "is-user" : ""}`}>
                  <div className="ct-codex-message__avatar">
                    {message.role === "user" ? <User size={15} /> : <Bot size={15} />}
                  </div>
                  <div className="ct-codex-message__content">
                    <span>{message.role === "user" ? "你" : "CodeTalk AI"}</span>
                    <div>
                      {message.role === "assistant" ? (
                        <MarkdownRenderer content={redactDiagnosticText(message.content)} enableNumericCitations={false} variant="ai" />
                      ) : (
                        <p className="whitespace-pre-wrap">{redactDiagnosticText(message.content)}</p>
                      )}
                    </div>
                    {message.actions?.some((action) => resolvedActionHref(action)) && (
                      <div className="ct-codex-message__actions">
                        {message.actions
                          .filter((action) => resolvedActionHref(action))
                          .map((action) => (
                            <a
                              key={`${message.id}-${actionId(action) || resolvedActionHref(action)}`}
                              href={resolvedActionHref(action)}
                              download={actionKind(action) === "download" ? true : undefined}
                            >
                              <Download size={14} />
                              {actionLabel(action) || "下载产物"}
                            </a>
                          ))}
                      </div>
                    )}
                  </div>
                </article>
              ))
            )}

            {streamingContent && (
              <article className="ct-codex-message">
                <div className="ct-codex-message__avatar">
                  <Bot size={15} />
                </div>
                <div className="ct-codex-message__content">
                  <span className="inline-flex items-center gap-2">
                    CodeTalk AI <Loader2 size={12} className="animate-spin" />
                  </span>
                  <div>
                    <MarkdownRenderer content={redactDiagnosticText(streamingContent)} enableNumericCitations={false} variant="ai" />
                  </div>
                </div>
              </article>
            )}
            <div ref={bottomRef} />
          </section>
          <AgentProcessDisclosure diagnostics={streamingDiagnostics} />
        </div>
        {showJumpToLatest && (
          <button type="button" className="ct-codex-ai__jump" onClick={() => jumpToLatest("auto", true)}>
            跳到最新回复
          </button>
        )}

        <div className="ct-codex-composer">
          <textarea
            value={input}
            name="ai-thread-message"
            aria-label="AI 线程消息"
            autoComplete="off"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="像 Codex 一样继续追问代码、需求、测试设计、复跑策略..."
            rows={3}
            disabled={composerDisabled}
          />
          <div className="ct-codex-composer__footer">
            <div>
              {QUICK_ACTIONS.slice(0, 3).map((action) => (
                <button
                  key={action}
                  type="button"
                  onClick={() => setInput(action)}
                  disabled={composerDisabled}
                >
                  {action}
                </button>
              ))}
            </div>
            {streamingRunId ? (
              <button className="ct-codex-send is-secondary" type="button" onClick={cancel} disabled={cancelling}>
                {cancelling ? <Loader2 size={15} className="animate-spin" /> : <Square size={15} />}
                停止
              </button>
            ) : (
              <button className="ct-codex-send" type="button" onClick={send} disabled={!input.trim() || sending}>
                {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                发送
              </button>
            )}
          </div>
        </div>
      </main>

      <aside className="ct-codex-ai__context">
        <button type="button" className="ct-codex-ai__context-toggle" onClick={() => setContextOpen(false)}>
          <ChevronRight size={16} />
          收起
        </button>
        <section>
          <h2>
            <Database size={16} />
            环境信息
          </h2>
          <div className="ct-ai-env-card">
            <div>
              <span>项目</span>
              <strong>{workspace?.name ?? "未绑定项目"}</strong>
            </div>
            <div>
              <span>记忆命名空间</span>
              <code>{conversation?.memory_namespace ?? "global"}</code>
            </div>
            <div>
              <span>线程状态</span>
              <em>{streamingRunId ? "生成中" : conversation?.status ?? "ready"}</em>
            </div>
            <div>
              <span>执行器</span>
              <strong>
                {activeRuntime
                  ? `${activeRuntime.name}${activeRuntime.enabled ? "" : "（已停用）"}`
                  : conversation?.runtime_type === "agent_runtime"
                    ? "未找到执行器"
                    : "内置模型"}
              </strong>
            </div>
          </div>
        </section>
        <section>
          <h2>
            <FilePlus2 size={16} />
            项目材料
          </h2>
          <div className="ct-ai-file-panel">
            <div>
              <strong>{materialCount}</strong>
              <span>材料</span>
            </div>
            <div>
              <strong>{reportCount}</strong>
              <span>报告</span>
            </div>
          </div>
          <div className="ct-ai-side-actions">
            {workspace ? (
              threadNavigationBusy ? (
                <span className="ct-ai-action is-disabled" role="link" aria-disabled="true">
                  <FilePlus2 size={15} />
                  添加/管理文件
                </span>
              ) : (
                <Link href={`/workspaces/${workspace.id}`} className="ct-ai-action">
                  <FilePlus2 size={15} />
                  添加/管理文件
                </Link>
              )
            ) : (
              threadNavigationBusy ? (
                <span className="ct-ai-action is-disabled" role="link" aria-disabled="true">
                  <FilePlus2 size={15} />
                  新建项目并添加文件
                </span>
              ) : (
                <Link href="/workspaces/new" className="ct-ai-action">
                  <FilePlus2 size={15} />
                  新建项目并添加文件
                </Link>
              )
            )}
            {threadNavigationBusy ? (
              <span className="ct-ai-action is-disabled" role="link" aria-disabled="true">
                <PlayCircle size={15} />
                运行智能体任务
              </span>
            ) : (
              <Link href="/workbench" className="ct-ai-action">
                <PlayCircle size={15} />
                运行智能体任务
              </Link>
            )}
          </div>
        </section>
        <section>
          <h2>
            <FileText size={16} />
            证据链
          </h2>
          {references.length === 0 ? (
            <p className="ct-ai-side-empty">还没有引用。发送问题后，系统会按当前项目优先召回源码、输入材料，再补充报告、记忆和语义用例。</p>
          ) : (
            <div className="grid gap-3">
              {latestReferences.map((ref) => (
                <EvidenceReferenceCard key={`${ref.source_type}:${ref.source_id}`} refItem={ref} />
              ))}
              {hiddenReferenceCount > 0 && (
                <details className="ct-ai-disclosure">
                  <summary>展开其余 {hiddenReferenceCount} 条证据</summary>
                  <div className="grid gap-3 pt-3">
                    {references.slice(latestReferences.length).map((ref) => (
                      <EvidenceReferenceCard key={`${ref.source_type}:${ref.source_id}`} refItem={ref} />
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </section>
        <section>
          <h2>
            <Database size={16} />
            执行轨迹
          </h2>
          <details className="ct-ai-disclosure">
            <summary>{auditSummary}</summary>
            <div className="ct-ai-audit-list">
              <div>
                <span>回答正文</span>
                <strong>{messages.filter((message) => message.role === "assistant").length} 条</strong>
              </div>
              <div>
                <span>当前执行器</span>
                <strong>
                  {activeRuntime
                    ? `${activeRuntime.name}${activeRuntime.enabled ? "" : "（已停用）"}`
                    : conversation?.runtime_type === "agent_runtime"
                      ? "未找到执行器"
                      : "内置模型"}
                </strong>
              </div>
              <div>
                <span>源码/材料优先</span>
                <strong>{workspace ? "已绑定工作区" : "未绑定工作区"}</strong>
              </div>
              {latestRun?.id && (
                <div>
                  <span>最近运行</span>
                  <code>{latestRun.id}</code>
                </div>
              )}
            </div>
          </details>
          <details className="ct-ai-disclosure">
            <summary>{visibleError ? "诊断详情：有错误，可展开查看" : "诊断详情：默认折叠"}</summary>
            <div className="ct-ai-diagnostic">
              {visibleError ? (
                <p>{visibleError}</p>
              ) : (
                <p>没有需要展开的错误日志。原始 agent 事件仅用于诊断，不混入正文。</p>
              )}
            </div>
          </details>
          {streamingDiagnostics.length > 0 && (
            <details
              className="ct-ai-disclosure"
              open={generationDiagnosticsOpen}
              onToggle={(event) => setGenerationDiagnosticsOpen(event.currentTarget.open)}
            >
              <summary>生成诊断：默认折叠</summary>
              {generationDiagnosticsOpen && (
                <div className="ct-ai-diagnostic">
                  {streamingDiagnostics.map((item, index) => (
                    <p key={`${index}-${item}`}>{item}</p>
                  ))}
                </div>
              )}
            </details>
          )}
        </section>
        <section>
          <h2>
            <Database size={16} />
            记忆动作
          </h2>
          <div className="ct-ai-side-actions">
            {["沉淀到当前项目记忆", "加入测试设计", "生成复跑建议"].map((action) => (
              <button
                key={action}
                type="button"
                className="ct-ai-action"
                onClick={() => setInput(action)}
                disabled={composerDisabled}
              >
                {action}
              </button>
            ))}
          </div>
        </section>
      </aside>
    </div>
  );
}

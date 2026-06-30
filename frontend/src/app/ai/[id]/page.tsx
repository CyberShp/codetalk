"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  User,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AgentRuntime, AIContextReference, AIConversation, AIMessage, AIRunEvent, Workspace } from "@/lib/types";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

const QUICK_ACTIONS = [
  "解释这个测试设计背后的风险判断",
  "补充黑盒边界条件和异常路径",
  "把结论整理成可执行测试用例",
  "生成下一轮复跑计划",
];

function eventContent(event: AIRunEvent): string {
  const value = event.payload.content;
  return typeof value === "string" ? value : "";
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

function uniqueReferences(messages: AIMessage[]): AIContextReference[] {
  const map = new Map<string, AIContextReference>();
  for (const msg of messages) {
    for (const ref of msg.references ?? []) {
      map.set(`${ref.source_type}:${ref.source_id}`, ref);
    }
  }
  return Array.from(map.values()).slice(0, 12);
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
    lines.push(message.content ? redactDiagnosticText(message.content) : "_空消息_");
    if (message.references?.length) {
      lines.push("");
      lines.push("### 证据引用");
      for (const ref of message.references) {
        lines.push(`- ${redactDiagnosticText(ref.title)} (${ref.source_type}:${ref.source_id})`);
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
  const [creatingSiblingThread, setCreatingSiblingThread] = useState(false);
  const [streamingRunId, setStreamingRunId] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [contextOpen, setContextOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const creatingSiblingThreadRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const references = useMemo(() => uniqueReferences(messages), [messages]);
  const workspaceId = threadWorkspaceId(conversation);
  const workspace = workspaces.find((item) => item.id === workspaceId) ?? null;
  const activeRuntime = agentRuntimes.find((item) => item.id === conversation?.agent_runtime_id) ?? null;
  const railProjects = useMemo(() => workspaces.slice(0, 8), [workspaces]);
  const visibleThreads = useMemo(
    () => threads.filter((thread) => threadWorkspaceId(thread) === workspaceId).slice(0, 50),
    [threads, workspaceId],
  );
  const materialCount = workspace?.materials?.length ?? 0;
  const reportCount = workspace?.reports?.length ?? 0;
  const latestRunError =
    conversation?.latest_run?.status === "failed" && conversation.latest_run.error
      ? redactDiagnosticText(conversation.latest_run.error)
      : "";
  const visibleError = error || latestRunError;
  const composerDisabled = sending || Boolean(streamingRunId);
  const threadNavigationBusy = savingRuntime || creatingSiblingThread || Boolean(streamingRunId);
  const lastUserMessage = useMemo(
    () => [...messages].reverse().find((message) => message.role === "user") ?? null,
    [messages],
  );
  const canRetryLatestFailure = Boolean(latestRunError && lastUserMessage && !sending && !streamingRunId);
  const canExportThread = messages.length > 0 && !streamingRunId;

  const load = useCallback(async () => {
    setError(null);
    const [conv, msgResult, workspaceItems, runtimeResult] = await Promise.all([
      api.aiConversations.get(conversationId),
      api.aiConversations.messages(conversationId),
      api.workspaces.list(),
      api.settings.listAgentRuntimes({ enabled: true }).catch(() => ({ items: [] as AgentRuntime[] })),
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
    if (conv.latest_run?.status === "queued" || conv.latest_run?.status === "running") {
      setStreamingRunId(conv.latest_run.id);
    }
  }, [conversationId]);

  const streamRun = useCallback(
    async (runId: string, cursor = 0) => {
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;
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
            if (event.event_type === "delta") {
              setStreamingContent((prev) => prev + eventContent(event));
            }
            if (event.event_type === "done" || event.event_type === "error") {
              if (event.event_type === "error") {
                setError(eventError(event) || "AI 生成失败，请检查模型配置后重试。");
              }
              setStreamingRunId(null);
              setStreamingContent("");
              await load();
              return;
            }
          }
        }
        if (!abort.signal.aborted) {
          setStreamingRunId(null);
          setStreamingContent("");
        }
        await load();
      } catch (exc) {
        if (!abort.signal.aborted) {
          setError(exc instanceof Error ? exc.message : "订阅生成状态失败");
          setStreamingRunId(null);
          setStreamingContent("");
          await load().catch(() => {});
        }
      }
    },
    [conversationId, load],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
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
  }, [load]);

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
          if (nextConversation.latest_run?.id !== streamingRunId) return;
          if (nextConversation.latest_run.status === "queued" || nextConversation.latest_run.status === "running") return;
          setStreamingRunId(null);
          setStreamingContent("");
          await load();
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [conversationId, load, streamingRunId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingContent]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending || streamingRunId) return;
    await sendText(text);
  };

  const sendText = async (text: string) => {
    setSending(true);
    setError(null);
    setInput("");
    setStreamingContent("");
    try {
      const result = await api.aiConversations.send(conversationId, text);
      setMessages((prev) => [...prev, result.message]);
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
    abortRef.current?.abort();
    await api.aiConversations.cancel(conversationId).catch(() => {});
    setStreamingRunId(null);
    setStreamingContent("");
    await load().catch(() => {});
  };

  const changeRuntime = async (value: string) => {
    if (!conversation || savingRuntime || streamingRunId) return;
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
          <small>{workspace?.repo_path ?? conversation?.memory_namespace ?? "global"}</small>
        </div>
        <div className="ct-codex-ai__rail-group">
          <div className="ct-codex-ai__rail-label">
            <FolderOpen size={13} />
            项目
          </div>
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
              <span
                key={thread.id}
                className={`${threadClass} is-disabled`}
                role="link"
                aria-disabled="true"
              >
                {threadContent}
              </span>
            ) : (
              <Link key={thread.id} href={`/ai/${thread.id}`} className={threadClass}>
                {threadContent}
              </Link>
            );
          })}
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
            disabled={savingRuntime || Boolean(streamingRunId)}
            aria-label="当前 AI 执行器"
          >
            {agentRuntimes.map((runtime) => (
              <option key={runtime.id} value={runtime.id}>
                {runtime.name}
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

        <section className="ct-codex-ai__reader">
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
                      <MarkdownRenderer content={message.content} enableNumericCitations={false} />
                    ) : (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    )}
                  </div>
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
                  <MarkdownRenderer content={streamingContent} enableNumericCitations={false} />
                </div>
              </div>
            </article>
          )}
          <div ref={bottomRef} />
        </section>

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
              <button className="ct-codex-send is-secondary" type="button" onClick={cancel}>
                <Square size={15} />
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
              <strong>{activeRuntime?.name ?? (conversation?.runtime_type === "agent_runtime" ? "未找到执行器" : "内置模型")}</strong>
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
            本轮引用
          </h2>
          {references.length === 0 ? (
            <p className="ct-ai-side-empty">还没有引用。发送问题后，系统会按当前项目召回报告、记忆和语义用例。</p>
          ) : (
            <div className="grid gap-3">
              {references.map((ref) => (
                <div key={`${ref.source_type}:${ref.source_id}`} className="ct-ai-ref">
                  <div className="flex items-center justify-between gap-2">
                    <span>{ref.title}</span>
                    <code>{ref.source_type}</code>
                  </div>
                  <p>{ref.excerpt}</p>
                </div>
              ))}
            </div>
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

"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Loader2,
  FolderOpen,
  RefreshCw,
  FileText,
  Paperclip,
  ChevronDown,
  ChevronRight,
  BarChart2,
  MessageSquare,
  Send,
  Bot,
  User,
  Upload,
  Trash2,
  Sparkles,
  Crosshair,
  Download,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace, WorkspaceReportMeta, WorkspaceChatMessage, ChatMode, EmbeddingStatus } from "@/lib/types";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

type Tab = "reports" | "materials" | "chat";

function IndexBadge({ indexed }: { indexed: number }) {
  if (indexed === 1) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-green-400/10 text-green-400">
        <CheckCircle2 size={12} />
        已索引
      </span>
    );
  }
  if (indexed === -1) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400">
        <XCircle size={12} />
        索引失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-400/10 text-amber-400">
      <Loader2 size={12} className="animate-spin" />
      索引中
    </span>
  );
}

function AnalyzeBadge({
  status,
  progress,
}: {
  status: string | null;
  progress: number;
}) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-blue-400/10 text-blue-400">
        <BarChart2 size={12} />
        报告已生成
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-400/10 text-indigo-400">
        <Loader2 size={12} className="animate-spin" />
        分析中 {progress}%
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400">
        <XCircle size={12} />
        分析失败
      </span>
    );
  }
  return null;
}

function ReportCard({ report, wsId }: { report: WorkspaceReportMeta; wsId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const LABELS: Record<string, string> = {
    module_map: "项目与模块地图",
    business_flow: "关键业务流程分析",
    source_reading: "源码定向阅读记录",
    test_design: "测试设计输入",
    requirements: "需求与设计理解",
    traceability: "需求-设计-代码追踪",
  };

  const handleToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && content === null && !loadingContent) {
      setLoadingContent(true);
      try {
        const full = await api.workspaces.report(wsId, report.id);
        setContent(full.content);
      } catch {
        setContent("（内容加载失败）");
      } finally {
        setLoadingContent(false);
      }
    }
  };

  return (
    <div className="rounded-lg border border-outline-variant/30 bg-surface-container-low overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-container transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-primary shrink-0" />
          <span className="font-medium text-sm text-on-surface">
            {LABELS[report.report_type] ?? report.report_type}
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={16} className="text-on-surface-variant" />
        ) : (
          <ChevronRight size={16} className="text-on-surface-variant" />
        )}
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t border-outline-variant/20">
          {loadingContent ? (
            <div className="flex justify-center mt-3">
              <Loader2 size={16} className="animate-spin text-primary" />
            </div>
          ) : (
            <pre className="mt-3 text-xs text-on-surface-variant whitespace-pre-wrap leading-relaxed font-mono overflow-auto max-h-[500px]">
              {content ?? "（暂无内容）"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function ChatPanel({ wsId, indexed }: { wsId: string; indexed: number }) {
  const [messages, setMessages] = useState<WorkspaceChatMessage[]>([]);
  const [streamingContent, setStreamingContent] = useState("");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatMode>("freeqa");
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const userNearBottom = useRef(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Fix 1 (frontend): block chat until workspace is indexed
  const canChat = indexed === 1;

  useEffect(() => {
    api.workspaces
      .chatHistory(wsId)
      .then(setMessages)
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [wsId]);

  const handleChatScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    userNearBottom.current =
      el.scrollHeight - (el.scrollTop + el.clientHeight) < 80;
  }, []);

  useEffect(() => {
    if (userNearBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, streamingContent]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming || !canChat) return;

    setInput("");
    setStreaming(true);
    setStreamingContent("");

    // Fix 3 (frontend): immediately show user bubble before waiting for SSE
    const userBubble: WorkspaceChatMessage = {
      id: `local-${Date.now()}`,
      workspace_id: wsId,
      mode,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userBubble]);

    try {
      const res = await api.workspaces.chatStream(wsId, text, mode);
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(body || `HTTP ${res.status}`);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6)) as { content: string; done: boolean; error?: string };
            if (evt.error) {
              setStreamingContent((p) => p + `\n\n⚠️ ${evt.error}`);
              break;
            }
            if (evt.done) break;
            if (evt.content) {
              accumulated += evt.content;
              setStreamingContent(accumulated);
            }
          } catch {
            continue;
          }
        }
      }

      // Reload history to get persisted messages
      const updated = await api.workspaces.chatHistory(wsId).catch(() => messages);
      setMessages(updated);
    } catch (e: unknown) {
      setMessages((prev) => [
        ...prev,
        {
          id: "err-" + Date.now(),
          workspace_id: wsId,
          mode,
          role: "assistant",
          content: `⚠️ ${e instanceof Error ? e.message : "发送失败"}`,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setStreaming(false);
      setStreamingContent("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-[600px]">
      {/* Header row: mode toggle + chat export */}
      <div className="flex items-center gap-2 mb-3">
        <button
          onClick={() => window.open(api.workspaces.chatExportUrl(wsId), "_blank")}
          disabled={messages.length === 0}
          title="导出对话记录（Markdown）"
          className="ml-auto flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg border border-outline-variant/30 text-on-surface-variant hover:bg-surface-container hover:text-on-surface disabled:opacity-30 disabled:cursor-not-allowed transition-colors shrink-0"
        >
          <Download size={12} />
          导出
        </button>
      </div>
      {/* Mode toggle */}
      <div className="flex gap-2 mb-3">
        {([
          { m: "freeqa" as ChatMode, Icon: Sparkles, label: "自由问答", desc: "基于代码片段轻量问答" },
          { m: "targeted" as ChatMode, Icon: Crosshair, label: "结构化分析", desc: "结合材料与报告深度分析" },
        ]).map(({ m, Icon, label, desc }) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`flex-1 flex items-center gap-2.5 px-3 py-2 rounded-lg border transition-all text-left ${
              mode === m
                ? "bg-primary/10 border-primary/40 shadow-sm"
                : "border-outline-variant/30 hover:border-primary/20 hover:bg-surface-container-high/30"
            }`}
          >
            <Icon size={15} className={mode === m ? "text-primary" : "text-on-surface-variant/60"} />
            <div>
              <div className={`text-xs font-medium leading-tight ${mode === m ? "text-primary" : "text-on-surface-variant"}`}>
                {label}
              </div>
              <div className="text-[10px] leading-tight text-on-surface-variant/50 mt-0.5">{desc}</div>
            </div>
          </button>
        ))}
      </div>

      {/* Message list */}
      <div onScroll={handleChatScroll} className="flex-1 overflow-y-auto rounded-xl border border-outline-variant/20 bg-surface-container-low p-4 space-y-4">
        {loadingHistory ? (
          <div className="flex justify-center py-8">
            <Loader2 size={18} className="animate-spin text-primary" />
          </div>
        ) : messages.length === 0 && !streamingContent ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-on-surface-variant/50">
            <MessageSquare size={32} />
            <p className="text-sm">向代码库提问，获取智能分析</p>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <React.Fragment key={msg.id}>
                <div
                  className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  {msg.role === "assistant" && (
                    <div className="shrink-0 w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center mt-0.5">
                      <Bot size={13} className="text-primary" />
                    </div>
                  )}
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${
                      msg.role === "user"
                        ? "bg-primary text-on-primary rounded-tr-sm"
                        : "bg-surface-container rounded-tl-sm text-on-surface"
                    }`}
                  >
                    {msg.role === "assistant" ? (
                      <div className="prose-sm">
                        <MarkdownRenderer content={msg.content} enableNumericCitations={false} />
                      </div>
                    ) : (
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                    )}
                  </div>
                  {msg.role === "user" && (
                    <div className="shrink-0 w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center mt-0.5">
                      <User size={13} className="text-primary" />
                    </div>
                  )}
                </div>
                {msg.role === "user" && (
                  <div className="flex justify-end mt-0.5 pr-8">
                    <span className="inline-flex items-center gap-0.5 text-[10px] text-on-surface-variant/40">
                      {msg.mode === "targeted" ? <Crosshair size={9} /> : <Sparkles size={9} />}
                      {msg.mode === "targeted" ? "结构化" : "自由"}
                    </span>
                  </div>
                )}
              </React.Fragment>
            ))}

            {streamingContent && (
              <div className="flex gap-2.5 justify-start">
                <div className="shrink-0 w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center mt-0.5">
                  <Bot size={13} className="text-primary" />
                </div>
                <div className="max-w-[85%] rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm bg-surface-container text-on-surface">
                  <div className="prose-sm">
                    <MarkdownRenderer content={streamingContent} enableNumericCitations={false} />
                  </div>
                  <span className="inline-block w-1.5 h-4 bg-primary animate-pulse ml-0.5 rounded-sm" />
                </div>
              </div>
            )}

            {streaming && !streamingContent && (
              <div className="flex gap-2.5 justify-start">
                <div className="shrink-0 w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center mt-0.5">
                  <Bot size={13} className="text-primary" />
                </div>
                <div className="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-surface-container">
                  <Loader2 size={14} className="animate-spin text-primary" />
                </div>
              </div>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Fix 1 (frontend): gate banner when not yet indexed */}
      {!canChat && (
        <div className="mt-3 px-4 py-2.5 rounded-xl bg-amber-400/10 border border-amber-400/20 text-xs text-amber-500">
          {indexed === 0 ? "工作空间正在索引中，完成后可开始对话" : "工作空间索引失败，请重新索引后再对话"}
        </div>
      )}

      {/* Input area */}
      <div className="mt-3 flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={canChat
            ? mode === "freeqa"
              ? "轻量问答，基于代码片段回答… (Enter 发送)"
              : "深度分析，结合材料与报告… (Enter 发送)"
            : "等待索引完成后可对话…"}
          disabled={streaming || !canChat}
          rows={2}
          className="flex-1 resize-none rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary/50 disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || streaming || !canChat}
          className="self-end flex items-center gap-1.5 px-4 py-2 text-sm rounded-xl bg-primary text-on-primary hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
        >
          {streaming ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          发送
        </button>
      </div>
    </div>
  );
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const wsId = params.id;

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("reports");
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeProgress, setAnalyzeProgress] = useState(0);
  const [analyzeStatus, setAnalyzeStatus] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatus | null>(null);

  const pollIndexRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAnalyzeRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hasLoadedRef = useRef(false);
  const toggleVersion = useRef<Record<string, number>>({});

  const loadWorkspace = useCallback(async () => {
    try {
      const ws = await api.workspaces.get(wsId);
      setWorkspace(ws);
      setAnalyzeStatus(ws.analyze_status);
      setAnalyzeProgress(ws.analyze_progress);
      if (!hasLoadedRef.current) {
        hasLoadedRef.current = true;
        setLoading(false);
      }
      return ws;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "加载失败");
      setLoading(false);
      return null;
    }
  }, [wsId]);

  const startIndexPoll = useCallback(
    (ws: Workspace) => {
      if (ws.indexed !== 0) return;
      if (pollIndexRef.current) return;

      pollIndexRef.current = setInterval(async () => {
        try {
          const s = await api.workspaces.indexStatus(wsId);
          if (s.indexed !== 0) {
            clearInterval(pollIndexRef.current!);
            pollIndexRef.current = null;
            await loadWorkspace();
          } else {
            setWorkspace((prev) =>
              prev ? { ...prev, indexed: s.indexed, index_job: s.index_job } : prev,
            );
          }
        } catch {
          // ignore transient poll errors
        }
      }, 3000);
    },
    [wsId, loadWorkspace],
  );

  const startAnalyzePoll = useCallback(() => {
    if (pollAnalyzeRef.current) return;

    pollAnalyzeRef.current = setInterval(async () => {
      try {
        const s = await api.workspaces.analyzeStatus(wsId);
        setAnalyzeStatus(s.analyze_status);
        setAnalyzeProgress(s.analyze_progress);

        if (s.analyze_status !== "running") {
          clearInterval(pollAnalyzeRef.current!);
          pollAnalyzeRef.current = null;
          setAnalyzing(false);
          await loadWorkspace();
        }
      } catch {
        // ignore
      }
    }, 5000);
  }, [wsId, loadWorkspace]);

  useEffect(() => {
    loadWorkspace().then((ws) => {
      if (!ws) return;
      startIndexPoll(ws);
      if (ws.analyze_status === "running") {
        setAnalyzing(true);
        startAnalyzePoll();
      }
    });
    api.workspaces.embeddingStatus(wsId).then(setEmbeddingStatus).catch(() => {});

    return () => {
      if (pollIndexRef.current) clearInterval(pollIndexRef.current);
      if (pollAnalyzeRef.current) clearInterval(pollAnalyzeRef.current);
    };
  }, [wsId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAnalyze = async () => {
    if (!workspace) return;
    setAnalyzing(true);
    setAnalyzeStatus("running");
    setAnalyzeProgress(0);
    try {
      await api.workspaces.analyze(wsId);
      startAnalyzePoll();
    } catch (e: unknown) {
      setAnalyzing(false);
      setAnalyzeStatus(workspace.analyze_status);
      alert(e instanceof Error ? e.message : "启动分析失败");
    }
  };

  const handleReindex = async () => {
    if (!workspace) return;
    setReindexing(true);
    try {
      await api.workspaces.reindex(wsId);
      setWorkspace((prev) => (prev ? { ...prev, indexed: 0 } : prev));
      startIndexPoll({ ...workspace, indexed: 0 });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "重新索引失败");
    } finally {
      setReindexing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  if (error || !workspace) {
    return (
      <div className="max-w-3xl mx-auto">
        <Link
          href="/workspaces"
          className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
        >
          <ArrowLeft size={16} />
          返回工作空间列表
        </Link>
        <div className="rounded-lg bg-error/10 border border-error/20 px-4 py-3 text-sm text-error">
          {error ?? "工作空间不存在"}
        </div>
      </div>
    );
  }

  const canAnalyze = workspace.indexed === 1 && analyzeStatus !== "running";

  return (
    <div className="max-w-5xl mx-auto">
      <Link
        href="/workspaces"
        className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
      >
        <ArrowLeft size={16} />
        返回工作空间列表
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <FolderOpen size={28} className="text-primary shrink-0" />
          <div>
            <h1 className="text-2xl font-bold text-on-surface">{workspace.name}</h1>
            <p className="text-sm text-on-surface-variant mt-0.5">{workspace.repo_path}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <IndexBadge indexed={workspace.indexed} />
              <AnalyzeBadge status={analyzeStatus} progress={analyzeProgress} />
            </div>
          </div>
        </div>

        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={handleReindex}
              disabled={reindexing || workspace.indexed === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-outline-variant/40 text-on-surface-variant hover:bg-surface-container disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw size={13} className={reindexing ? "animate-spin" : ""} />
              重新索引
            </button>

            <button
              onClick={handleAnalyze}
              disabled={!canAnalyze}
              className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-primary text-on-primary hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity font-medium"
            >
              {analyzing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <BarChart2 size={14} />
              )}
              生成报告
            </button>
          </div>
          {(() => {
            const activeCount = workspace.materials.filter((m) => m.is_active).length;
            if (activeCount === 0) return null;
            return (
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs text-on-surface-variant flex items-center gap-1">
                  <Paperclip size={11} />
                  {activeCount} 个活跃材料将参与分析
                </span>
                {embeddingStatus && (
                  <span className={`text-xs flex items-center gap-1 ${embeddingStatus.rag_ready ? "text-green-400" : "text-on-surface-variant/60"}`}>
                    <Sparkles size={11} />
                    {embeddingStatus.rag_ready
                      ? `RAG 就绪 (${embeddingStatus.total_chunks} 分块)`
                      : "RAG 未启用"}
                  </span>
                )}
                {activeCount > 0 && (!embeddingStatus || !embeddingStatus.rag_ready) && (
                  <button
                    type="button"
                    onClick={async () => {
                      await api.workspaces.triggerEmbedding(wsId);
                      setTimeout(() => {
                        api.workspaces.embeddingStatus(wsId).then(setEmbeddingStatus).catch(() => {});
                      }, 3000);
                    }}
                    className="text-xs text-primary hover:underline"
                  >
                    嵌入材料
                  </button>
                )}
              </div>
            );
          })()}
        </div>
      </div>

      {/* Analysis progress bar */}
      {analyzeStatus === "running" && (
        <div className="mb-6">
          <div className="flex items-center justify-between text-xs text-on-surface-variant mb-1">
            <span>分析进度</span>
            <span>{analyzeProgress}%</span>
          </div>
          <div className="h-1.5 bg-surface-container rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-500"
              style={{ width: `${analyzeProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-outline-variant/20">
        {(["reports", "materials", "chat"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-on-surface-variant hover:text-on-surface"
            }`}
          >
            {t === "reports" ? (
              <FileText size={14} />
            ) : t === "materials" ? (
              <Paperclip size={14} />
            ) : (
              <MessageSquare size={14} />
            )}
            {t === "reports"
              ? `报告 (${workspace.reports.length})`
              : t === "materials"
                ? `材料 (${workspace.materials.length})`
                : "对话"}
          </button>
        ))}
      </div>

      {/* Reports tab */}
      {tab === "reports" && (
        <div>
          {workspace.reports.some((r) => r.status === "completed") && (
            <div className="flex items-center gap-2 mb-4">
              <span className="text-xs text-on-surface-variant">导出报告：</span>
              {(["md", "docx", "xml"] as const).map((fmt) => (
                <button
                  key={fmt}
                  onClick={() => window.open(api.workspaces.exportUrl(wsId, fmt), "_blank")}
                  className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border border-outline-variant/30 text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors uppercase"
                >
                  <Download size={11} />
                  {fmt}
                </button>
              ))}
            </div>
          )}
          {workspace.reports.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
              <FileText size={36} className="text-on-surface-variant/30" />
              <p className="text-on-surface-variant text-sm">
                {workspace.indexed === 1
                  ? "尚未生成报告，点击「生成报告」开始分析"
                  : workspace.indexed === 0
                    ? "等待索引完成后可生成报告"
                    : "索引失败，请重新索引后生成报告"}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {workspace.reports.map((report) => (
                <ReportCard key={report.id} report={report} wsId={wsId} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Materials tab */}
      {tab === "materials" && (
        <div className="space-y-4">
          <label className="flex items-center justify-center gap-2 px-4 py-3 rounded-lg border-2 border-dashed border-outline-variant/40 bg-surface-container-low hover:border-primary/50 hover:bg-primary/5 cursor-pointer transition-colors">
            <Upload size={18} className="text-primary" />
            <span className="text-sm text-on-surface-variant">点击上传材料（需求文档、设计文档等）</span>
            <input
              type="file"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                try {
                  const mat = await api.workspaces.uploadMaterial(wsId, file);
                  setWorkspace((prev) =>
                    prev ? { ...prev, materials: [...prev.materials, mat] } : prev
                  );
                } catch {
                  /* upload failed — silent for now */
                }
                e.target.value = "";
              }}
            />
          </label>

          {workspace.materials.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-36 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
              <Paperclip size={36} className="text-on-surface-variant/30" />
              <p className="text-on-surface-variant text-sm">尚未上传任何材料</p>
            </div>
          ) : (
            <div className="space-y-2">
              {workspace.materials.map((mat) => (
                <div
                  key={mat.id}
                  className="flex items-center gap-3 px-4 py-3 rounded-lg border border-outline-variant/30 bg-surface-container-low group"
                >
                  <input
                    type="checkbox"
                    checked={mat.is_active}
                    title={mat.is_active ? "已激活（参与对话上下文）" : "已停用（不参与对话）"}
                    onChange={async (e) => {
                      const next = e.target.checked;
                      const ver = (toggleVersion.current[mat.id] = (toggleVersion.current[mat.id] ?? 0) + 1);
                      setWorkspace((prev) =>
                        prev
                          ? { ...prev, materials: prev.materials.map((m) => m.id === mat.id ? { ...m, is_active: next } : m) }
                          : prev
                      );
                      try {
                        await api.workspaces.toggleMaterial(wsId, mat.id, next);
                      } catch {
                        if (toggleVersion.current[mat.id] !== ver) return;
                        setWorkspace((prev) =>
                          prev
                            ? { ...prev, materials: prev.materials.map((m) => m.id === mat.id ? { ...m, is_active: !next } : m) }
                            : prev
                        );
                      }
                    }}
                    className="w-4 h-4 accent-primary shrink-0 cursor-pointer"
                  />
                  <Paperclip size={16} className={mat.is_active ? "text-primary shrink-0" : "text-on-surface-variant/40 shrink-0"} />
                  <div className="min-w-0 flex-1">
                    <p className={`text-sm truncate ${mat.is_active ? "text-on-surface" : "text-on-surface-variant/50"}`}>{mat.filename}</p>
                    <p className="text-xs text-on-surface-variant mt-0.5">{mat.content_type}</p>
                  </div>
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await api.workspaces.deleteMaterial(wsId, mat.id);
                        setWorkspace((prev) =>
                          prev
                            ? { ...prev, materials: prev.materials.filter((m) => m.id !== mat.id) }
                            : prev
                        );
                      } catch {
                        /* delete failed */
                      }
                    }}
                    className="p-1.5 rounded-md text-on-surface-variant/50 hover:text-error hover:bg-error/10 opacity-0 group-hover:opacity-100 transition-all"
                    title="删除材料"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Chat tab */}
      {tab === "chat" && <ChatPanel wsId={wsId} indexed={workspace.indexed} />}
    </div>
  );
}

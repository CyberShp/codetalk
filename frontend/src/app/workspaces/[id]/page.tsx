"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
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
  Square,
  Bot,
  User,

  Trash2,
  Sparkles,
  Crosshair,
  FileSearch,
  Download,
  Terminal,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  Workspace,
  WorkspaceReportMeta,
  WorkspaceVersion,
  TaskStep,
  ChatMode,
  EmbeddingStatus,
  WorkspaceModule,
  WorkspaceSourceFile,
  WorkspaceSourceSearchMatch,
} from "@/lib/types";
import { useWsChat } from "@/lib/chatContext";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import AnalysisTaskModal from "@/components/workspaces/AnalysisTaskModal";

type Tab = "reports" | "materials" | "chat" | "source" | "logs";

function IndexBadge({
  indexed,
  lastIndexError,
  indexProgress = 0,
}: {
  indexed: number;
  lastIndexError?: string | null;
  indexProgress?: number;
}) {
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
      <span
        className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-400/10 text-red-400 cursor-help"
        title={lastIndexError ?? "索引失败"}
      >
        <XCircle size={12} />
        索引失败{lastIndexError ? " ⓘ" : ""}
      </span>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-400/10 text-amber-400">
        <Loader2 size={12} className="animate-spin" />
        索引中{indexProgress > 0 ? ` ${indexProgress}%` : ""}
      </span>
      {indexProgress > 0 && (
        <div className="w-24 h-1.5 bg-amber-400/20 rounded-full overflow-hidden">
          <div
            className="h-full bg-amber-400 rounded-full transition-all duration-500"
            style={{ width: `${indexProgress}%` }}
          />
        </div>
      )}
    </div>
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
  if (status === "partial") {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-400/10 text-amber-400">
        <BarChart2 size={12} />
        部分完成
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

function ReportCard({
  report,
  wsId,
  onContinue,
}: {
  report: WorkspaceReportMeta;
  wsId: string;
  onContinue: (report: WorkspaceReportMeta) => void;
}) {
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

  const [loadError, setLoadError] = useState<string | null>(null);
  const displayTitle = report.title?.trim() || LABELS[report.report_type] || report.report_type;

  const handleToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    // Re-fetch when opening if we have neither content nor a prior error, so a
    // failed first load can be retried instead of silently staying blank.
    if (next && content === null && !loadingContent) {
      setLoadingContent(true);
      setLoadError(null);
      try {
        const full = await api.workspaces.report(wsId, report.id);
        // Distinguish "loaded but empty" (failed/partial report) from "loaded
        // with body" so the card never looks like it just didn't open.
        setContent(full.content ?? "");
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setLoadError(`内容加载失败：${msg}`);
      } finally {
        setLoadingContent(false);
      }
    }
  };

  return (
    <div className="ct-interactive-card rounded-lg border border-outline-variant/30 bg-surface-container-low overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-container transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-primary shrink-0" />
          <span className="font-medium text-sm text-on-surface">
            {displayTitle}
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={16} className="text-on-surface-variant" />
        ) : (
          <ChevronRight size={16} className="text-on-surface-variant" />
        )}
      </button>
      {expanded && (
        <div className="ct-reveal px-4 pb-4 border-t border-outline-variant/20">
          {loadingContent ? (
            <div className="flex justify-center mt-3">
              <Loader2 size={16} className="animate-spin text-primary" />
            </div>
          ) : loadError ? (
            <div className="mt-3 text-xs text-error">
              {loadError}
              <button
                onClick={handleToggle}
                className="ml-2 underline hover:text-on-surface"
              >
                重试
              </button>
            </div>
          ) : content !== null && content.trim() === "" ? (
            <div className="mt-3 text-xs text-on-surface-variant">
              {`该报告无正文内容（状态：${report.status}）。可能因截断或证据不足被标记为 partial/failed。`}
            </div>
          ) : (
            <div className="ct-reveal mt-3 max-h-[560px] overflow-auto rounded-lg border border-[#d7e5f3] bg-[#f6f9fc] p-4 shadow-sm">
              <MarkdownRenderer
                content={content ?? "（暂无内容）"}
                enableNumericCitations={false}
                variant="report"
              />
            </div>
          )}
          <button
            type="button"
            onClick={() => onContinue(report)}
            className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-on-primary shadow-sm transition-opacity hover:opacity-90"
          >
            <MessageSquare size={13} />
            围绕此报告继续追问
          </button>
        </div>
      )}
    </div>
  );
}

function SourceSearchPanel({ wsId, indexed }: { wsId: string; indexed: number }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<WorkspaceSourceSearchMatch[]>([]);
  const [selected, setSelected] = useState<WorkspaceSourceSearchMatch | null>(null);
  const [file, setFile] = useState<WorkspaceSourceFile | null>(null);
  const [searching, setSearching] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSearch = indexed === 1;

  const runSearch = async () => {
    const q = query.trim();
    if (!q || !canSearch || searching) return;
    setSearching(true);
    setError(null);
    setSelected(null);
    setFile(null);
    try {
      const response = await api.workspaces.sourceSearch(wsId, q, 30);
      setResults(response.matches);
      if (response.matches.length === 0) {
        setError("未找到匹配的源码文件或内容");
      }
    } catch (e) {
      setResults([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  };

  const openMatch = async (match: WorkspaceSourceSearchMatch) => {
    setSelected(match);
    setLoadingFile(true);
    setError(null);
    try {
      const content = await api.workspaces.sourceFile(wsId, match.path, match.line ?? undefined, 120);
      setFile(content);
    } catch (e) {
      setFile(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFile(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="flex-1 flex items-center gap-2 px-3 py-2 rounded-lg border border-outline-variant/40 bg-surface-container-low focus-within:border-primary/50 transition-colors">
          <Search size={14} className="text-on-surface-variant/50 shrink-0" />
          <input
            aria-label="源码搜索"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void runSearch();
            }}
            placeholder={canSearch ? "搜索路径或内容，例如 lib/nvmf、test/nvmf、spdk_nvmf_connect" : "索引完成后可搜索源码"}
            disabled={!canSearch || searching}
            className="flex-1 bg-transparent text-sm text-on-surface outline-none placeholder:text-on-surface-variant/40 disabled:opacity-50"
          />
        </label>
        <button
          type="button"
          onClick={runSearch}
          disabled={!query.trim() || !canSearch || searching}
          className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-on-primary text-sm font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
        >
          {searching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          搜索源码
        </button>
      </div>

      {!canSearch && (
        <div className="rounded-lg border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-xs text-amber-500">
          工作空间索引完成后可搜索源码文件。
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-error/20 bg-error/10 px-4 py-3 text-xs text-error whitespace-pre-wrap">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(280px,380px)_1fr]">
        <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low overflow-hidden">
          <div className="px-4 py-3 border-b border-outline-variant/20 text-xs text-on-surface-variant">
            搜索结果 ({results.length})
          </div>
          {results.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-on-surface-variant/50">
              <FileSearch size={32} />
              <p className="text-sm">输入路径或符号后搜索</p>
            </div>
          ) : (
            <div className="max-h-[560px] overflow-auto divide-y divide-outline-variant/10">
              {results.map((match, index) => (
                <button
                  key={`${match.path}:${match.line ?? "path"}:${index}`}
                  type="button"
                  onClick={() => void openMatch(match)}
                  className={`w-full px-4 py-3 text-left transition-colors hover:bg-surface-container ${
                    selected?.path === match.path && selected?.line === match.line ? "bg-primary/10" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <FileText size={14} className="text-primary shrink-0" />
                    <span className="min-w-0 flex-1 truncate font-data text-xs text-on-surface">
                      {match.path}
                    </span>
                    <span className="shrink-0 rounded-full border border-outline-variant/30 px-1.5 py-0.5 text-[10px] text-on-surface-variant">
                      {match.match_type === "path" ? "路径" : `L${match.line}`}
                    </span>
                  </div>
                  <p className="mt-1 max-h-8 overflow-hidden text-xs text-on-surface-variant">
                    {match.text || match.path}
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-outline-variant/20">
            <div className="min-w-0">
              <div className="truncate font-data text-xs text-on-surface">
                {file?.path ?? selected?.path ?? "未打开文件"}
              </div>
              {file && (
                <div className="text-[11px] text-on-surface-variant">
                  {file.start_line}-{file.end_line} / {file.total_lines} 行
                </div>
              )}
            </div>
            {loadingFile && <Loader2 size={14} className="animate-spin text-primary shrink-0" />}
          </div>
          {file ? (
            <pre className="max-h-[560px] overflow-auto p-4 text-xs leading-relaxed text-on-surface bg-[#f6f9fc] font-data whitespace-pre-wrap">
              {file.content}
            </pre>
          ) : (
            <div className="flex flex-col items-center justify-center h-64 gap-3 text-on-surface-variant/50">
              <FileText size={32} />
              <p className="text-sm">点击搜索结果打开源码片段</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- legacy workspace chat retained for compatibility while AI threads are the primary UI.
function ChatPanel({
  wsId,
  indexed,
  lastIndexError,
  reports,
}: {
  wsId: string;
  indexed: number;
  lastIndexError?: string | null;
  reports: WorkspaceReportMeta[];
}) {
  const { messages, streaming, streamingContent, loadingHistory, init, send, stop } =
    useWsChat(wsId);
  const [input, setInput] = useState("");
  const hasCompletedReports = reports.some((report) => report.status === "completed");
  const [selectedMode, setSelectedMode] = useState<ChatMode>("freeqa");
  const [modeTouched, setModeTouched] = useState(false);
  const mode: ChatMode = modeTouched ? selectedMode : hasCompletedReports ? "report_qa" : selectedMode;
  const [modules, setModules] = useState<WorkspaceModule[]>([]);
  const [selectedModule, setSelectedModule] = useState<string | null>(null);
  const userNearBottom = useRef(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const canChat = indexed === 1;

  useEffect(() => { void init(); }, [init]);

  useEffect(() => {
    if (indexed === 1) {
      api.workspaces.modules(wsId).then(setModules).catch(() => {});
    }
  }, [wsId, indexed]);

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
    await send(text, mode, selectedModule ?? undefined);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-240px)] min-h-[520px]">
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
          { m: "report_qa" as ChatMode, Icon: FileSearch, label: "报告追问", desc: "报告 + 代码 + 记忆" },
          { m: "freeqa" as ChatMode, Icon: Sparkles, label: "自由问答", desc: "代码片段 + 记忆" },
          { m: "targeted" as ChatMode, Icon: Crosshair, label: "结构化分析", desc: "材料 + 报告 + 记忆" },
        ]).map(({ m, Icon, label, desc }) => (
          <button
            key={m}
            onClick={() => {
              setModeTouched(true);
              setSelectedMode(m);
            }}
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

      {/* Module selector — shown when indexed and GitNexus clusters are available */}
      {canChat && modules.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          <span className="text-[10px] text-on-surface-variant/50 shrink-0">聚焦模块：</span>
          <button
            onClick={() => setSelectedModule(null)}
            className={`px-2 py-0.5 text-[11px] rounded-full border transition-colors ${
              selectedModule === null
                ? "bg-primary/10 border-primary/40 text-primary"
                : "border-outline-variant/30 text-on-surface-variant/70 hover:border-primary/20"
            }`}
          >
            全部
          </button>
          {modules.map((m) => (
            <button
              key={m.id}
              onClick={() => setSelectedModule(selectedModule === m.id ? null : m.id)}
              className={`px-2 py-0.5 text-[11px] rounded-full border transition-colors ${
                selectedModule === m.id
                  ? "bg-primary/10 border-primary/40 text-primary"
                  : "border-outline-variant/30 text-on-surface-variant/70 hover:border-primary/20"
              }`}
            >
              {m.name}
            </button>
          ))}
        </div>
      )}

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
            {messages.map((msg, index) => {
              const previousUser = [...messages.slice(0, index)]
                .reverse()
                .find((candidate) => candidate.role === "user");
              const content = msg.content.trim();
              const failedAssistantMessage =
                content.includes("⚠️ 发送失败") || content.includes("⚠️ 生成失败");
              const canRetryMessage =
                msg.role === "assistant" && failedAssistantMessage && previousUser !== undefined;
              return (
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
                {canRetryMessage && (
                  <div className="flex justify-start mt-1 pl-8">
                    <button
                      onClick={() => {
                        if (previousUser) void send(previousUser.content, previousUser.mode, selectedModule ?? undefined);
                      }}
                      disabled={streaming || !canChat}
                      className="inline-flex items-center gap-1 rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface disabled:opacity-50"
                    >
                      <RefreshCw size={11} />
                      重试
                    </button>
                  </div>
                )}
                {msg.role === "user" && (
                  <div className="flex justify-end mt-0.5 pr-8">
                    <span className="inline-flex items-center gap-0.5 text-[10px] text-on-surface-variant/40">
                      {msg.mode === "targeted" ? (
                        <Crosshair size={9} />
                      ) : msg.mode === "report_qa" ? (
                        <FileSearch size={9} />
                      ) : (
                        <Sparkles size={9} />
                      )}
                      {msg.mode === "targeted" ? "结构化" : msg.mode === "report_qa" ? "报告追问" : "自由"}
                    </span>
                  </div>
                )}
              </React.Fragment>
              );
            })}

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
          {indexed === -1 && lastIndexError && (
            <div className="mt-1 text-amber-200/90 whitespace-pre-wrap">
              {lastIndexError}
            </div>
          )}
        </div>
      )}

      {canChat && hasCompletedReports && mode === "freeqa" && (
        <div className="mt-3 px-4 py-2.5 rounded-xl bg-amber-400/10 border border-amber-400/20 text-xs text-amber-500">
          自由问答不会加载已生成报告或材料；报告后追问请切换到「报告追问」。
        </div>
      )}

      {/* Input area */}
      <div className="mt-3 flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={canChat
            ? mode === "report_qa"
              ? "追问已生成报告，必要时结合代码片段… (Enter 发送)"
              : mode === "freeqa"
              ? "轻量问答，基于代码片段回答… (Enter 发送)"
              : "深度分析，结合材料与报告… (Enter 发送)"
            : "等待索引完成后可对话…"}
          disabled={streaming || !canChat}
          rows={2}
          className="flex-1 resize-none rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary/50 disabled:opacity-50"
        />
        {streaming ? (
          <button
            onClick={stop}
            className="self-end flex items-center gap-1.5 px-4 py-2 text-sm rounded-xl border border-outline-variant/40 text-on-surface hover:bg-surface-container transition-colors"
          >
            <Square size={14} />
            停止
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!input.trim() || !canChat}
            className="self-end flex items-center gap-1.5 px-4 py-2 text-sm rounded-xl bg-primary text-on-primary hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
          >
            <Send size={14} />
            发送
          </button>
        )}
      </div>
    </div>
  );
}

function AIThreadBridge({
  workspace,
  opening,
  onOpenWorkspace,
}: {
  workspace: Workspace;
  opening: boolean;
  onOpenWorkspace: () => void;
}) {
  const completedReports = workspace.reports.filter((report) => report.status === "completed").length;
  const activeMaterials = workspace.materials.filter((material) => material.is_active).length;

  return (
    <div className="ct-workspace-ai-bridge">
      <div>
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-outline-variant/70 bg-white/80 px-3 py-1 text-xs font-semibold text-on-surface-variant shadow-sm">
          <Sparkles size={14} />
          持续 AI 调查
        </div>
        <h2>在宽屏 AI 线程中继续分析</h2>
        <p>
          工作空间继续负责材料、索引和报告生成；追问开发修改、测试思路、需求文档和报告结论时，统一进入可恢复的 AI 调查线程。
        </p>
      </div>
      <div className="ct-workspace-ai-bridge__stats">
        <span>{completedReports} 份完成报告</span>
        <span>{activeMaterials} 个活跃材料</span>
        <span>{workspace.indexed === 1 ? "索引就绪" : "等待索引"}</span>
      </div>
      <button
        type="button"
        onClick={onOpenWorkspace}
        disabled={opening}
        className="inline-flex w-fit items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary shadow-[0_18px_36px_rgba(15,23,42,0.18)] transition-all hover:-translate-y-0.5 hover:shadow-[0_24px_48px_rgba(15,23,42,0.22)] disabled:translate-y-0 disabled:opacity-50"
      >
        {opening ? <Loader2 size={16} className="animate-spin" /> : <MessageSquare size={16} />}
        打开工作空间 AI 线程
      </button>
    </div>
  );
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const wsId = params.id;

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("reports");
  const [materialPath, setMaterialPath] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeProgress, setAnalyzeProgress] = useState(0);
  const [analyzeStatus, setAnalyzeStatus] = useState<string | null>(null);
  const [indexProgress, setIndexProgress] = useState(0);
  const [reindexing, setReindexing] = useState(false);
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatus | null>(null);
  const [showAnalysisModal, setShowAnalysisModal] = useState(false);
  const [versions, setVersions] = useState<WorkspaceVersion[]>([]);
  const [selectedVersionTaskId, setSelectedVersionTaskId] = useState<string | null>(null);
  const [logSteps, setLogSteps] = useState<TaskStep[]>([]);
  const [logElapsedSecs, setLogElapsedSecs] = useState(0);
  const [currentAnalysisTaskId, setCurrentAnalysisTaskId] = useState<string | null>(null);
  const [openingConversation, setOpeningConversation] = useState(false);

  const pollIndexRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAnalyzeRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastAnalysisTaskIdRef = useRef<string | null>(null);
  const selectedVersionTaskIdRef = useRef<string | null>(null);
  const hasLoadedRef = useRef(false);
  const toggleVersion = useRef<Record<string, number>>({});
  const wsLogRef = useRef<WebSocket | null>(null);
  const lastLogStepTimeRef = useRef<number | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Keep ref in sync so WS cleanup can read current value even after re-render
  selectedVersionTaskIdRef.current = selectedVersionTaskId;

  const loadWorkspace = useCallback(async () => {
    try {
      const ws = await api.workspaces.get(wsId);
      setWorkspace(ws);
      setAnalyzeStatus(ws.analyze_status);
      setAnalyzeProgress(ws.analyze_progress);
      setIndexProgress(ws.index_progress ?? 0);
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
            setIndexProgress(0);
            await loadWorkspace();
          } else {
            setIndexProgress(s.index_progress ?? 0);
            setWorkspace((prev) =>
              prev ? { ...prev, indexed: s.indexed, index_job: s.index_job, index_progress: s.index_progress ?? 0 } : prev,
            );
          }
        } catch {
          // ignore transient poll errors
        }
      }, 3000);
    },
    [wsId, loadWorkspace],
  );

  const loadVersions = useCallback(async () => {
    try {
      const v = await api.workspaces.versions(wsId);
      setVersions(v);
      setSelectedVersionTaskId((prev) => prev ?? v[0]?.task_id ?? null);
    } catch { /* ignore */ }
  }, [wsId]);

  const startAnalyzePoll = useCallback(() => {
    if (pollAnalyzeRef.current) return;

    pollAnalyzeRef.current = setInterval(async () => {
      try {
        const s = await api.workspaces.analyzeStatus(wsId);
        setAnalyzeStatus(s.analyze_status);
        setAnalyzeProgress(s.analyze_progress);
        if (s.task_id) {
          if (!lastAnalysisTaskIdRef.current) {
            // First poll that returns a task_id — immediately show the new version tab
            void loadVersions();
            setSelectedVersionTaskId(s.task_id);
          }
          setCurrentAnalysisTaskId(s.task_id);
          lastAnalysisTaskIdRef.current = s.task_id;
        }

        if (s.analyze_status !== "running") {
          clearInterval(pollAnalyzeRef.current!);
          pollAnalyzeRef.current = null;
          setAnalyzing(false);
          await loadWorkspace();
          await loadVersions();
          const pinTaskId = s.task_id ?? lastAnalysisTaskIdRef.current;
          if (pinTaskId) {
            setSelectedVersionTaskId(pinTaskId);
          }
        }
      } catch {
        // ignore
      }
    }, 5000);
  }, [wsId, loadWorkspace, loadVersions]);

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
    loadVersions();

    return () => {
      if (pollIndexRef.current) clearInterval(pollIndexRef.current);
      if (pollAnalyzeRef.current) clearInterval(pollAnalyzeRef.current);
    };
  }, [wsId]); // eslint-disable-line react-hooks/exhaustive-deps

  // F2: live execution-log stream.
  // WS opens first so no events are dropped during the history HTTP fetch.
  // History then merges into the already-accumulating live state via dedup+sort.
  useEffect(() => {
    if (analyzeStatus !== "running" || !currentAnalysisTaskId || typeof window === "undefined") return;
    // User navigated away to a historical task — don't pollute its logSteps with live events
    if (selectedVersionTaskId !== null && selectedVersionTaskId !== currentAnalysisTaskId) return;
    let live = true;
    const taskId = currentAnalysisTaskId;
    const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100";

    const ws = new WebSocket(apiBase.replace(/^http/, "ws") + `/ws/tasks/${taskId}/logs`);
    wsLogRef.current = ws;
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string);
        if (msg.type === "event" && msg.timestamp && msg.step) {
          lastLogStepTimeRef.current = Date.now();
          setLogElapsedSecs(0);
          setLogSteps((prev) => {
            if (prev.some((s) => s.timestamp === msg.timestamp && s.step === msg.step)) return prev;
            return [...prev, {
              timestamp: msg.timestamp, progress: msg.progress, step: msg.step,
              event_type: msg.event_type, phase: msg.phase, target: msg.target,
              detail: msg.detail, level: msg.level,
            }];
          });
        }
      } catch { /* ignore malformed WS messages */ }
    };
    ws.onerror = () => ws.close();
    // onclose fires for both network drop (onerror→close) and the intentional
    // close in the cleanup below.  live is set false before that close(), so
    // only an unexpected mid-run drop reaches the backfill here.
    ws.onclose = () => {
      if (!live) return;
      api.tasks.steps(taskId)
        .then((allSteps) => { if (live && allSteps.length > 0) setLogSteps(allSteps); })
        .catch(() => {});
    };

    // After WS is registered, backfill from steps.jsonl and merge with any
    // live events already received while the fetch was in flight.
    api.tasks.steps(taskId)
      .then((history) => {
        if (!live || history.length === 0) return;
        const historyTs = new Date(history[history.length - 1].timestamp).getTime();
        // Don't overwrite a more-recent live timestamp that arrived while history was loading
        lastLogStepTimeRef.current = Math.max(lastLogStepTimeRef.current ?? 0, historyTs);
        setLogSteps((prev) => {
          const merged = [...history];
          for (const s of prev) {
            if (!merged.some((h) => h.timestamp === s.timestamp && h.step === s.step)) merged.push(s);
          }
          return merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        });
      })
      .catch(() => { /* history unavailable; live events continue unaffected */ });

    return () => {
      live = false; // set before ws.close() so onclose skips its backfill
      wsLogRef.current?.close();
      wsLogRef.current = null;
      // Explicit final backfill for the analysis-completion case (analyzeStatus
      // left "running"), where ws.onclose was skipped because live was already false.
      // Guard: skip if user navigated away — historical effect will load the correct task.
      api.tasks.steps(taskId)
        .then((allSteps) => {
          if (allSteps.length > 0 && selectedVersionTaskIdRef.current === taskId) {
            setLogSteps(allSteps);
          }
        })
        .catch(() => {});
    };
  }, [analyzeStatus, currentAnalysisTaskId, selectedVersionTaskId]);

  // F2: stopwatch — tick every second while running
  useEffect(() => {
    if (analyzeStatus !== "running") return;
    const timer = setInterval(() => {
      if (lastLogStepTimeRef.current !== null) {
        setLogElapsedSecs(Math.floor((Date.now() - lastLogStepTimeRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [analyzeStatus]);

  // F2: when viewing a historical version (or a non-current task while running), replay its steps.jsonl
  useEffect(() => {
    // Only skip when user is actively watching the live log of the currently-running task
    if (selectedVersionTaskId === currentAnalysisTaskId && analyzeStatus === "running") return;
    if (!selectedVersionTaskId || selectedVersionTaskId === "__legacy__") {
      setLogSteps([]);
      return;
    }
    let cancelled = false;
    api.tasks.steps(selectedVersionTaskId)
      .then((s) => { if (!cancelled) setLogSteps(s); })
      .catch(() => { if (!cancelled) setLogSteps([]); });
    return () => { cancelled = true; };
  }, [analyzeStatus, selectedVersionTaskId, currentAnalysisTaskId]);

  // F2: auto-scroll the log tab to the latest entry
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logSteps]);

  const handleAnalyze = () => {
    if (!workspace) return;
    setShowAnalysisModal(true);
  };

  const handleAnalysisStarted = () => {
    setShowAnalysisModal(false);
    setAnalyzing(true);
    setAnalyzeStatus("running");
    setAnalyzeProgress(0);
    setCurrentAnalysisTaskId(null);
    lastAnalysisTaskIdRef.current = null;
    setLogSteps([]);
    setSelectedVersionTaskId(null);
    startAnalyzePoll();
    void loadVersions();
  };

  const handleReindex = async () => {
    if (!workspace) return;
    setReindexing(true);
    try {
      await api.workspaces.reindex(wsId);
      setIndexProgress(0);
      setWorkspace((prev) => (prev ? { ...prev, indexed: 0 } : prev));
      startIndexPoll({ ...workspace, indexed: 0 });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "重新索引失败");
    } finally {
      setReindexing(false);
    }
  };

  const openConversation = async ({
    scopeType,
    scopeId,
    title,
    initialContext,
  }: {
    scopeType: "workspace" | "report";
    scopeId: string;
    title: string;
    initialContext?: Record<string, unknown>;
  }) => {
    setOpeningConversation(true);
    try {
      const conversation = await api.aiConversations.createForScope({
        scope_type: scopeType,
        scope_id: scopeId,
        workspace_id: wsId,
        memory_namespace: `workspace:${wsId}`,
        title,
        initial_context: {
          ...(initialContext ?? {}),
          workspace_id: wsId,
          memory_namespace: `workspace:${wsId}`,
        },
      });
      router.push(`/ai/${conversation.id}`);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "创建 AI 线程失败");
    } finally {
      setOpeningConversation(false);
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

  // Reports with no task_id are pre-versioning legacy rows.
  const legacyReports = workspace.reports.filter((r) => r.task_id === null);
  // "__legacy__" sentinel → show only null-task_id reports (visible even when versioned reports exist).
  const displayReports =
    selectedVersionTaskId === "__legacy__"
      ? legacyReports
      : selectedVersionTaskId
        ? workspace.reports.filter((r) => r.task_id === selectedVersionTaskId)
        : workspace.reports;

  return (
    <div className="w-full px-4 xl:px-6">
      <Link
        href="/workspaces"
        className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
      >
        <ArrowLeft size={16} />
        返回工作空间列表
      </Link>

      {/* Header */}
      <div className="ct-reveal flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <FolderOpen size={28} className="text-primary shrink-0" />
          <div>
            <h1 className="text-2xl font-bold text-on-surface">{workspace.name}</h1>
            <p className="text-sm text-on-surface-variant mt-0.5">{workspace.repo_path}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <IndexBadge indexed={workspace.indexed} lastIndexError={workspace.last_index_error} indexProgress={indexProgress} />
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
              className="ct-progress-fill h-full rounded-full transition-all duration-500"
              style={{ width: `${analyzeProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="ct-reveal ct-reveal-delay-1 flex gap-1 mb-6 border-b border-outline-variant/20">
        {(["reports", "materials", "chat", "source", "logs"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`relative flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-on-surface-variant hover:text-on-surface"
            }`}
          >
            {t === "reports" ? (
              <FileText size={14} />
            ) : t === "materials" ? (
              <Paperclip size={14} />
            ) : t === "chat" ? (
              <MessageSquare size={14} />
            ) : t === "source" ? (
              <FileSearch size={14} />
            ) : (
              <Terminal size={14} />
            )}
            {t === "reports"
              ? `报告 (${displayReports.length})`
              : t === "materials"
                ? `材料 (${workspace.materials.length})`
                : t === "chat"
                  ? "AI线程"
                  : t === "source"
                    ? "源码搜索"
                    : "执行日志"}
            {tab === t && (
              <span className="absolute inset-x-3 -bottom-0.5 h-0.5 rounded-full bg-primary shadow-[0_0_10px_rgba(15,125,184,0.55)]" />
            )}
          </button>
        ))}
      </div>

      {/* Reports tab */}
      {tab === "reports" && (
        <div>
          {(versions.length > 0 || legacyReports.length > 0) && (
            <div className="flex items-center gap-2 mb-4">
              <span className="text-xs text-on-surface-variant shrink-0">版本：</span>
              <select
                value={selectedVersionTaskId ?? ""}
                onChange={(e) => setSelectedVersionTaskId(e.target.value || null)}
                className="text-xs rounded-lg border border-outline-variant/30 bg-surface-container-low text-on-surface px-2 py-1 outline-none focus:border-primary/50 max-w-full"
              >
                {versions.map((v, i) => (
                  <option key={v.task_id} value={v.task_id}>
                    {new Date(v.created_at).toLocaleString()} · {v.status}{i === 0 ? "（最新）" : ""}
                  </option>
                ))}
                {legacyReports.length > 0 && (
                  <option value="__legacy__">早期报告（未版本化）</option>
                )}
              </select>
            </div>
          )}
          {(() => {
              const exportTaskId =
                selectedVersionTaskId ??
                versions[0]?.task_id ??
                (legacyReports.length > 0 ? "__legacy__" : null);
              return displayReports.some((r) => r.status === "completed") ? (
                <div className="flex items-center gap-2 mb-4">
                  <span className="text-xs text-on-surface-variant">
                    导出当前版本：
                  </span>
                  {(["md", "docx", "xml"] as const).map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => window.open(api.workspaces.exportUrl(wsId, fmt, exportTaskId), "_blank")}
                      title={`仅导出当前选择版本的已完成报告（${fmt.toUpperCase()}）`}
                      className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border border-outline-variant/30 text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors uppercase"
                    >
                      <Download size={11} />
                      {fmt}
                    </button>
                  ))}
                </div>
              ) : null;
            })()}
          {displayReports.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
              <FileText size={36} className="text-on-surface-variant/30" />
              <p className="text-on-surface-variant text-sm">
                {(() => {
                  if (selectedVersionTaskId === "__legacy__") return "该版本暂无报告";
                  if (selectedVersionTaskId) {
                    const ver = versions.find((v) => v.task_id === selectedVersionTaskId);
                    return `该版本暂无报告${ver ? `（任务状态：${ver.status}）` : ""}`;
                  }
                  if (workspace.indexed === 1) return "尚未生成报告，点击「生成报告」开始分析";
                  if (workspace.indexed === 0) return "等待索引完成后可生成报告";
                  return "索引失败，请重新索引后生成报告";
                })()}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {displayReports.map((report) => (
                <ReportCard
                  key={`${report.task_id ?? "legacy"}:${report.id ?? report.report_type}`}
                  report={report}
                  wsId={wsId}
                  onContinue={(item) =>
                    void openConversation({
                      scopeType: "report",
                      scopeId: item.id,
                      title: `${item.title?.trim() || item.report_type} · AI 追问`,
                      initialContext: {
                        workspace_id: wsId,
                        report_type: item.report_type,
                        task_id: item.task_id,
                      },
                    })
                  }
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Logs tab — live execution events (running) or historical replay (version) */}
      {tab === "logs" && (
        <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low p-4 h-[calc(100vh-300px)] min-h-[400px] overflow-y-auto font-data text-xs">
          {logSteps.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-on-surface-variant">
              <Terminal size={32} className="opacity-30" />
              <p>{analyzeStatus === "running" ? "等待执行事件…" : "该版本暂无执行日志"}</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {logSteps.map((s, i) => (
                <div key={`${s.timestamp}-${i}`} className="ct-log-line flex items-start gap-2 px-1.5 py-0.5">
                  <span className="text-on-surface-variant/40 shrink-0 tabular-nums">
                    {new Date(s.timestamp).toLocaleTimeString()}
                  </span>
                  <span className={`shrink-0 ${s.level === "error" ? "text-red-400" : "text-primary/70"}`}>
                    {s.event_type ?? "·"}
                  </span>
                  <span className="text-on-surface break-words">{s.step}</span>
                  {analyzeStatus === "running" && i === logSteps.length - 1 && (
                    <span className="ml-auto tabular-nums text-on-surface-variant/50 shrink-0">{logElapsedSecs}s</span>
                  )}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          )}
        </div>
      )}

      {/* Materials tab */}
      {tab === "materials" && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <div className="flex-1 flex items-center gap-2 px-3 py-2 rounded-lg border border-outline-variant/40 bg-surface-container-low focus-within:border-primary/50 transition-colors">
              <Paperclip size={14} className="text-on-surface-variant/50 shrink-0" />
              <input
                type="text"
                value={materialPath}
                onChange={(e) => setMaterialPath(e.target.value)}
                placeholder="输入文件绝对路径（需求文档、设计文档等）"
                className="flex-1 bg-transparent text-sm text-on-surface outline-none placeholder:text-on-surface-variant/40"
                onKeyDown={async (e) => {
                  if (e.key !== "Enter") return;
                  const path = materialPath.trim();
                  if (!path) return;
                  try {
                    const mat = await api.workspaces.uploadMaterial(wsId, path);
                    setWorkspace((prev) =>
                      prev ? { ...prev, materials: [...prev.materials, mat] } : prev
                    );
                    setMaterialPath("");
                  } catch { /* silent */ }
                }}
              />
            </div>
            <button
              onClick={async () => {
                const path = materialPath.trim();
                if (!path) return;
                try {
                  const mat = await api.workspaces.uploadMaterial(wsId, path);
                  setWorkspace((prev) =>
                    prev ? { ...prev, materials: [...prev.materials, mat] } : prev
                  );
                  setMaterialPath("");
                } catch { /* silent */ }
              }}
              disabled={!materialPath.trim()}
              className="px-3 py-2 rounded-lg bg-primary/10 text-primary text-sm font-medium hover:bg-primary/20 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              添加
            </button>
          </div>

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
      {tab === "chat" && (
        <AIThreadBridge
          workspace={workspace}
          opening={openingConversation}
          onOpenWorkspace={() =>
            void openConversation({
              scopeType: "workspace",
              scopeId: wsId,
              title: `${workspace.name} · AI 调查线程`,
              initialContext: {
                repo_path: workspace.repo_path,
                completed_reports: workspace.reports.filter((report) => report.status === "completed").length,
              },
            })
          }
        />
      )}

      {tab === "source" && (
        <SourceSearchPanel
          wsId={wsId}
          indexed={workspace.indexed}
        />
      )}

      <AnalysisTaskModal
        wsId={wsId}
        open={showAnalysisModal}
        onClose={() => setShowAnalysisModal(false)}
        onStarted={handleAnalysisStarted}
      />
    </div>
  );
}

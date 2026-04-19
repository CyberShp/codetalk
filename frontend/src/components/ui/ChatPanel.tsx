"use client";

import { Bot, ChevronDown, Loader2, Plus, Send, Square, Trash2, User } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatEngine } from "@/hooks/useChatEngine";
import { api, type ChatSession } from "@/lib/api";
import MarkdownRenderer from "./MarkdownRenderer";

function checkResearchComplete(text: string): boolean {
  return (
    text.includes("## Final Conclusion") ||
    text.includes("## 最终结论") ||
    text.includes("# Final Conclusion") ||
    text.includes("# 最终结论")
  );
}

interface ChatPanelProps {
  engine: ChatEngine;
  /** Required for session persistence. Without it, sessions are disabled. */
  repoId?: string;
  /** Additional CSS classes for the outer container. */
  className?: string;
}

export default function ChatPanel({ engine, repoId, className = "" }: ChatPanelProps) {
  const {
    messages,
    setMessages,
    input,
    setInput,
    isStreaming,
    isAutoResearching,
    deepResearch,
    setDeepResearch,
    researchIteration,
    researchStatus,
    scrollRef,
    inputRef,
    handleSend,
    handleStop,
  } = engine;

  const busy = isStreaming || isAutoResearching;

  // Session management state
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [showSessionList, setShowSessionList] = useState(false);
  const sessionListRef = useRef<HTMLDivElement>(null);

  // Auto-save debounce ref
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);

  // Keep ref in sync
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // Load session list on mount (if repoId provided)
  useEffect(() => {
    if (!repoId) return;
    api.repos.chat.sessions.list(repoId).then(setSessions).catch((e) => {
      console.warn("Chat sessions load failed:", e instanceof Error ? e.message : e);
    });
  }, [repoId]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (sessionListRef.current && !sessionListRef.current.contains(e.target as Node)) {
        setShowSessionList(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Auto-save: debounce 2s after messages change, only when session exists
  useEffect(() => {
    if (!repoId) return;
    if (messages.length <= 1) return; // Only welcome message — skip

    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      const payload = messages
        .filter((m) => m.id !== "welcome")
        .map(({ role, content }) => ({ role, content }));

      const sid = activeSessionIdRef.current;
      if (sid) {
        // Update existing session
        try {
          const updated = await api.repos.chat.sessions.update(repoId, sid, { messages: payload });
          setSessions((prev) => prev.map((s) => (s.id === sid ? updated : s)));
        } catch {}
      } else {
        // Auto-create a new session
        try {
          const created = await api.repos.chat.sessions.create(repoId, { messages: payload });
          setActiveSessionId(created.id);
          setSessions((prev) => [created, ...prev]);
        } catch {}
      }
    }, 2000);

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [messages, repoId]);

  const handleLoadSession = useCallback(
    async (session: ChatSession) => {
      if (!repoId) return;
      setShowSessionList(false);
      setActiveSessionId(session.id);
      try {
        const full = await api.repos.chat.sessions.get(repoId, session.id);
        setMessages([
          {
            id: "welcome",
            role: "assistant",
            content: "你好！我是 CodeTalks AI 助手。你可以问我关于这个代码仓库的任何问题 — 架构设计、逻辑流、实现细节等。",
          },
          ...full.messages.map((m, i) => ({
            id: `session-${i}`,
            role: m.role as "user" | "assistant",
            content: m.content,
          })),
        ]);
      } catch {
        // Session load failed — leave current messages intact
        setActiveSessionId(null);
      }
    },
    [repoId, setMessages],
  );

  const handleNewSession = useCallback(() => {
    setActiveSessionId(null);
    setMessages([
      {
        id: "welcome",
        role: "assistant",
        content: "你好！我是 CodeTalks AI 助手。你可以问我关于这个代码仓库的任何问题 — 架构设计、逻辑流、实现细节等。",
      },
    ]);
    setShowSessionList(false);
  }, [setMessages]);

  const handleDeleteSession = useCallback(
    async (session: ChatSession, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!repoId) return;
      try {
        await api.repos.chat.sessions.delete(repoId, session.id);
        setSessions((prev) => prev.filter((s) => s.id !== session.id));
        if (activeSessionIdRef.current === session.id) {
          handleNewSession();
        }
      } catch {}
    },
    [repoId, handleNewSession],
  );

  const activeSession = sessions.find((s) => s.id === activeSessionId);

  return (
    <div className={`flex flex-col h-full overflow-hidden ${className}`}>
      {/* breathe keyframes — scoped here, no tailwind.config change needed */}
      <style>{`
        @keyframes breathe {
          0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
          50%       { transform: scaleX(1);   opacity: 1;   }
        }
      `}</style>

      {/* Session bar — only when repoId provided */}
      {repoId && (
        <div className="relative flex items-center gap-1 px-3 py-1.5 border-b border-white/5 bg-black/10 shrink-0" ref={sessionListRef}>
          <button
            onClick={() => setShowSessionList((v) => !v)}
            className="flex-1 flex items-center gap-1.5 min-w-0 text-left rounded px-2 py-1 hover:bg-white/5 transition-colors"
          >
            <span className="text-[9px] font-mono text-on-surface-variant/50 truncate flex-1">
              {activeSession?.title ?? "New Session"}
            </span>
            <ChevronDown size={10} className={`shrink-0 text-on-surface-variant/40 transition-transform ${showSessionList ? "rotate-180" : ""}`} />
          </button>
          <button
            onClick={handleNewSession}
            className="p-1 rounded text-on-surface-variant/40 hover:text-primary hover:bg-white/5 transition-all"
            title="新建会话"
          >
            <Plus size={12} />
          </button>

          {/* Session dropdown */}
          {showSessionList && (
            <div className="absolute top-full left-0 right-0 z-50 mt-0.5 bg-[#0D0D0F]/95 backdrop-blur border border-white/10 rounded-md shadow-xl max-h-48 overflow-y-auto">
              {sessions.length === 0 ? (
                <div className="px-3 py-2 text-[9px] text-on-surface-variant/40 font-mono">暂无历史会话</div>
              ) : (
                sessions.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => handleLoadSession(s)}
                    className={`flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-white/5 transition-colors group ${
                      s.id === activeSessionId ? "bg-primary/5" : ""
                    }`}
                  >
                    <span className="flex-1 text-[9px] font-mono truncate text-on-surface-variant/70">
                      {s.title ?? "Untitled"}
                    </span>
                    <button
                      onClick={(e) => handleDeleteSession(s, e)}
                      className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-on-surface-variant/40 hover:text-tertiary transition-all"
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}

      {/* Research Phase Ribbon — only visible during deep research */}
      {deepResearch && researchIteration > 0 && (
        <div className="flex h-1 shrink-0">
          {(["Plan", "R1", "R2", "R3", "Done"] as const).map((label, i) => (
            <div
              key={label}
              className={`flex-1 transition-all duration-500 ${
                i < researchIteration - 1
                  ? "bg-gradient-to-r from-primary to-secondary"
                  : i === researchIteration - 1
                    ? "bg-primary/50 animate-pulse"
                    : "bg-white/5"
              }`}
              title={`${label}${
                i < researchIteration - 1
                  ? " ✓"
                  : i === researchIteration - 1
                    ? " (进行中)"
                    : ""
              }`}
            />
          ))}
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin [mask-image:linear-gradient(to_bottom,transparent,black_20px,black_calc(100%-20px),transparent)]"
      >
        {messages.map((m) => {
          const isConcl =
            m.role === "assistant" && checkResearchComplete(m.content);
          return (
            <div
              key={m.id}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] flex gap-2 ${
                  m.role === "user" ? "flex-row-reverse" : "flex-row"
                }`}
              >
                <div
                  className={`w-6 h-6 rounded flex items-center justify-center shrink-0 ${
                    m.role === "user" ? "bg-secondary/20" : "bg-primary/20"
                  }`}
                >
                  {m.role === "user" ? (
                    <User size={12} className="text-secondary" />
                  ) : (
                    <Bot size={12} className="text-primary" />
                  )}
                </div>
                <div
                  className={`p-3 rounded-lg text-xs leading-relaxed ${
                    m.role === "user"
                      ? "bg-secondary/5 text-on-surface border border-secondary/10"
                      : isConcl
                        ? "bg-primary/[0.05] text-on-surface-variant shadow-[0_0_20px_rgba(164,230,255,0.08)]"
                        : "bg-white/[0.03] text-on-surface-variant border border-white/5"
                  }`}
                >
                  {m.role === "assistant" && m.content ? (
                    <div className="prose prose-invert prose-xs max-w-none [&_p]:my-1 [&_pre]:my-2 [&_ul]:my-1">
                      <MarkdownRenderer content={m.content} />
                    </div>
                  ) : m.role === "assistant" && !m.content ? (
                    <span className="flex items-center gap-2 text-on-surface-variant/50">
                      <Loader2 size={12} className="animate-spin" />
                      思考中...
                    </span>
                  ) : (
                    m.content
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Auto-research status bar — Synaptic Pulse */}
      {isAutoResearching && (
        <div className="px-4 py-2 border-t border-white/5 bg-black/10 shrink-0">
          <span className="text-[9px] font-mono text-primary/70 tracking-widest">
            {researchStatus}
          </span>
          <div className="mt-1.5 h-0.5 rounded-full bg-primary/10 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-primary to-secondary rounded-full"
              style={{
                animation: "breathe 2s ease-in-out infinite",
                transformOrigin: "left",
              }}
            />
          </div>
        </div>
      )}

      {/* Input row */}
      <div className="p-4 border-t border-white/5 bg-black/20 shrink-0">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
            placeholder="询问代码库..."
            disabled={busy}
            className="flex-1 h-9 text-xs bg-surface-container-lowest/50 text-on-surface font-data px-4 py-2 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container disabled:opacity-50"
          />

          {/* DEEP Research Toggle */}
          <button
            onClick={() => setDeepResearch((v) => !v)}
            disabled={busy}
            className={`h-9 px-2.5 rounded-md text-[9px] font-mono tracking-wider transition-all duration-300 shrink-0 disabled:opacity-30 ${
              deepResearch
                ? "bg-primary/15 text-primary shadow-[0_0_12px_rgba(164,230,255,0.15)]"
                : "bg-white/[0.03] text-on-surface-variant/40 hover:text-on-surface-variant/70"
            }`}
            title="深度研究模式：自动进行 5 轮迭代深入分析"
          >
            DEEP
          </button>

          {busy ? (
            <button
              onClick={handleStop}
              className="w-9 h-9 flex items-center justify-center rounded-md bg-tertiary text-on-primary hover:bg-tertiary/90 transition-colors shrink-0"
              title="停止生成"
            >
              <Square size={14} />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="w-9 h-9 flex items-center justify-center rounded-md bg-primary text-on-primary hover:bg-primary/90 transition-colors shrink-0 disabled:opacity-40"
            >
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import GlassPanel from "./GlassPanel";
import MarkdownRenderer from "./MarkdownRenderer";
import { api } from "@/lib/api";
import { Send, MessageSquare, X, Bot, User, Loader2, Square } from "lucide-react";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface Props {
  repoId: string;
  /** File paths from the currently viewed wiki page. Undefined = global context. */
  currentPageFilePaths?: string[];
}

export default function FloatingChat({ repoId, currentPageFilePaths }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "你好！我是 CodeTalks AI 助手。你可以问我关于这个代码仓库的任何问题 — 架构设计、逻辑流、实现细节等。",
    },
  ]);
  const [isStreaming, setIsStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isOpen]);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() || isStreaming) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input.trim(),
    };
    const assistantId = (Date.now() + 1).toString();

    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "" },
    ]);
    setInput("");
    setIsStreaming(true);

    const history = [...messages.filter((m) => m.id !== "welcome"), userMsg].map(
      (m) => ({ role: m.role, content: m.content }),
    );

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const response = await api.repos.chat.stream(
        repoId,
        history,
        { includedFiles: currentPageFilePaths },
        controller.signal,
      );

      if (!response.ok) {
        const errText = await response.text().catch(() => "");
        throw new Error(errText || `HTTP ${response.status}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        setMessages((prev) => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last.id === assistantId) {
            msgs[msgs.length - 1] = { ...last, content: last.content + text };
          }
          return msgs;
        });
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        setMessages((prev) => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last.id === assistantId && !last.content) {
            msgs[msgs.length - 1] = {
              ...last,
              content: "> 已停止生成。",
            };
          }
          return msgs;
        });
        return;
      }
      setMessages((prev) => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last.id === assistantId && !last.content) {
          msgs[msgs.length - 1] = {
            ...last,
            content: `> ⚠️ ${(e as Error).message || "连接失败，请稍后重试。"}`,
          };
        }
        return msgs;
      });
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, messages, repoId, currentPageFilePaths]);

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {isOpen && (
        <div
          className="mb-4 w-80 sm:w-96 animate-in fade-in slide-in-from-bottom-6 duration-500 ease-out rounded-t-2xl overflow-hidden outline outline-1 outline-white/10"
          style={{ height: "min(500px, calc(100vh - 7rem))" }}
        >
          <GlassPanel className="h-full flex flex-col overflow-hidden shadow-[0_-20px_80px_-20px_rgba(0,0,0,0.8)] border-none bg-[#0D0D0F]/90 backdrop-blur-2xl">
            {/* Cyber Cap Header */}
            <div className="relative h-11 shrink-0 flex items-center justify-between px-4 bg-black/40 border-b border-white/5">
              {/* Top scan line */}
              <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-primary/50 to-transparent" />

              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-primary shadow-lg shadow-primary/60 animate-pulse" />
                <h3 className="text-[10px] font-mono font-bold uppercase tracking-[0.2em] text-on-surface/70">
                  Neural Link <span className="text-primary/50">v2.5</span>
                </h3>
              </div>

              <div className="flex items-center gap-3">
                <span className="text-[9px] font-mono text-on-surface-variant/40 hidden sm:block italic">
                  {currentPageFilePaths?.length
                    ? `${currentPageFilePaths.length} files in scope`
                    : "GLOBAL_CONTEXT"}
                </span>
                <button
                  onClick={() => setIsOpen(false)}
                  className="p-1 rounded-md text-on-surface-variant hover:text-on-surface hover:bg-white/5 transition-all"
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            {/* Messages */}
            <div
              ref={scrollRef}
              className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin [mask-image:linear-gradient(to_bottom,transparent,black_20px,black_calc(100%-20px),transparent)]"
            >
              {messages.map((m) => (
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
              ))}
            </div>

            {/* Input */}
            <div className="p-4 border-t border-white/5 bg-black/20">
              <div className="flex gap-2">
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
                  placeholder="询问代码库..."
                  disabled={isStreaming}
                  className="flex-1 h-9 text-xs bg-surface-container-lowest/50 text-on-surface font-data px-4 py-2 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container disabled:opacity-50"
                />
                {isStreaming ? (
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
          </GlassPanel>
        </div>
      )}

      {/* Toggle Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-14 h-14 rounded-full flex items-center justify-center shadow-lg transition-all duration-300 ${
          isOpen
            ? "bg-on-surface text-surface rotate-90 scale-90"
            : "bg-primary text-on-primary hover:scale-110 hover:shadow-primary/20"
        }`}
      >
        {isOpen ? <X size={24} /> : <MessageSquare size={24} />}
        {!isOpen && (
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-secondary rounded-full border-2 border-surface animate-bounce" />
        )}
      </button>
    </div>
  );
}

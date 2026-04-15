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
  taskId: string;
}

export default function FloatingChat({ taskId }: Props) {
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

      const response = await api.chat.stream(taskId, history, controller.signal);

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
  }, [input, isStreaming, messages, taskId]);

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {isOpen && (
        <div className="mb-4 w-80 sm:w-96 animate-in fade-in slide-in-from-bottom-4 duration-300">
          <GlassPanel className="h-[500px] flex flex-col overflow-hidden shadow-2xl border-primary/20 bg-surface-container-high/90">
            {/* Header */}
            <div className="p-4 border-b border-outline-variant flex items-center justify-between bg-primary/5">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
                <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface">
                  AI 助手
                </h3>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="text-on-surface-variant hover:text-on-surface transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Messages */}
            <div
              ref={scrollRef}
              className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-thin"
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
                          ? "bg-secondary/10 text-on-surface border border-secondary/20"
                          : "bg-surface-container-lowest text-on-surface-variant border border-outline-variant/30"
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
            <div className="p-4 border-t border-outline-variant bg-surface-container-low/50">
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

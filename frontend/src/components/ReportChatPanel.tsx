"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Send, MessageCircle, Bot } from "lucide-react";
import { api } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

export default function ReportChatPanel({ taskId }: { taskId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState("");
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const userNearBottom = useRef(true);

  useEffect(() => {
    api.tasks.chatHistory(taskId).then(setMessages).catch(() => {});
  }, [taskId]);

  const handleScroll = useCallback(() => {
    const el = chatContainerRef.current;
    if (!el) return;
    userNearBottom.current =
      el.scrollHeight - (el.scrollTop + el.clientHeight) < 80;
  }, []);

  useEffect(() => {
    if (userNearBottom.current && chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [messages, streamContent]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: ChatMessage = {
      id: Date.now(),
      task_id: taskId,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);
    setStreamContent("");

    try {
      const res = await fetch(api.tasks.chatUrl(taskId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok || !res.body) throw new Error("request failed");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const payload = JSON.parse(line.slice(6)) as { content?: string; done: boolean; error?: string };
            if (payload.done) {
              if (payload.error) {
                setMessages((prev) => [
                  ...prev,
                  {
                    id: Date.now() + 1,
                    task_id: taskId,
                    role: "assistant",
                    content: `> ⚠ ${payload.error}`,
                    created_at: new Date().toISOString(),
                  },
                ]);
                setStreamContent("");
              } else {
                setMessages((prev) => [
                  ...prev,
                  {
                    id: Date.now() + 1,
                    task_id: taskId,
                    role: "assistant",
                    content: accumulated,
                    created_at: new Date().toISOString(),
                  },
                ]);
                setStreamContent("");
              }
            } else {
              accumulated += payload.content ?? "";
              setStreamContent(accumulated);
            }
          } catch {
            // ignore malformed SSE lines
          }
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          task_id: taskId,
          role: "assistant",
          content: "抱歉，发生了网络错误，请稍后重试。",
          created_at: new Date().toISOString(),
        },
      ]);
      setStreamContent("");
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, taskId]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const sanitizeContent = (content: string) =>
    content.replace(
      /^#{1,6}\s+(.*(?:Error|Exception|Traceback|error|exception|failed|Failed).*)$/gm,
      "**$1**",
    );

  return (
    <div className="flex flex-col h-full bg-surface-container rounded-xl border border-outline-variant/20">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20 shrink-0">
        <MessageCircle size={16} className="text-primary" />
        <span className="text-sm font-medium text-on-surface">AI 助手</span>
      </div>

      {/* Messages */}
      <div ref={chatContainerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-4 py-3 space-y-4 min-h-0">
        {messages.length === 0 && !streaming && (
          <div className="flex flex-col items-center justify-center h-full text-center py-8">
            <Bot size={32} className="text-on-surface-variant/40 mb-3" />
            <p className="text-sm text-on-surface-variant/70">有关此报告的问题，欢迎提问</p>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "user" ? (
              <div className="max-w-[85%] px-3 py-2 bg-primary text-on-primary rounded-xl rounded-tr-sm text-sm leading-relaxed">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[95%] px-3 py-2 bg-surface-container-high rounded-xl rounded-tl-sm text-sm">
                <MarkdownRenderer content={sanitizeContent(msg.content)} enableNumericCitations={false} />
              </div>
            )}
          </div>
        ))}

        {/* Streaming indicator */}
        {streaming && (
          <div className="flex justify-start">
            <div className="max-w-[95%] px-3 py-2 bg-surface-container-high rounded-xl rounded-tl-sm text-sm">
              {streamContent ? (
                <MarkdownRenderer content={sanitizeContent(streamContent)} enableNumericCitations={false} />
              ) : (
                <div className="flex items-center gap-1 py-1">
                  <span className="w-1.5 h-1.5 bg-on-surface-variant/60 rounded-full animate-bounce [animation-delay:0ms]" />
                  <span className="w-1.5 h-1.5 bg-on-surface-variant/60 rounded-full animate-bounce [animation-delay:150ms]" />
                  <span className="w-1.5 h-1.5 bg-on-surface-variant/60 rounded-full animate-bounce [animation-delay:300ms]" />
                </div>
              )}
            </div>
          </div>
        )}

      </div>

      {/* Input */}
      <div className="shrink-0 px-4 py-3 border-t border-outline-variant/20">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="提问关于此报告的问题..."
            rows={2}
            disabled={streaming}
            className="flex-1 resize-none text-sm bg-surface-container-high rounded-lg px-3 py-2 text-on-surface placeholder:text-on-surface-variant/50 border border-outline-variant/20 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/40 disabled:opacity-60"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || streaming}
            className="p-2.5 rounded-lg bg-primary text-on-primary hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
          >
            <Send size={16} />
          </button>
        </div>
        <p className="text-[11px] text-on-surface-variant/50 mt-1.5">
          Enter 发送 · Shift+Enter 换行
        </p>
      </div>
    </div>
  );
}

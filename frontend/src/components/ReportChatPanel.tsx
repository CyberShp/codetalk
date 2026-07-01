"use client";

import { useState, useEffect, useRef, useCallback, useLayoutEffect } from "react";
import { Send, MessageCircle, Bot } from "lucide-react";
import { useTaskChat } from "@/lib/taskChatContext";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";

export default function ReportChatPanel({ taskId }: { taskId: string }) {
  const { messages, streaming, streamingContent, loadingHistory, init, send } =
    useTaskChat(taskId);
  const [input, setInput] = useState("");
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const detachedScrollTopRef = useRef(0);

  useEffect(() => { void init(); }, [init]);

  const handleScroll = useCallback(() => {
    const el = chatContainerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - (el.scrollTop + el.clientHeight) < 80;
    autoScrollRef.current = nearBottom;
    if (!nearBottom) {
      detachedScrollTopRef.current = el.scrollTop;
    }
    setShowJumpToLatest(!nearBottom && (streaming || Boolean(streamingContent)));
  }, [streaming, streamingContent]);

  const detachAutoScroll = useCallback(() => {
    const el = chatContainerRef.current;
    if (el) detachedScrollTopRef.current = el.scrollTop;
    autoScrollRef.current = false;
    if (streaming || streamingContent) setShowJumpToLatest(true);
  }, [streaming, streamingContent]);

  const jumpToLatest = useCallback(() => {
    const el = chatContainerRef.current;
    autoScrollRef.current = true;
    setShowJumpToLatest(false);
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  useLayoutEffect(() => {
    const el = chatContainerRef.current;
    if (!el) return;
    if (autoScrollRef.current) {
      el.scrollTop = el.scrollHeight;
      return;
    }
    if (streaming || streamingContent) {
      const target = detachedScrollTopRef.current;
      el.scrollTop = target;
      window.requestAnimationFrame(() => {
        if (!autoScrollRef.current && chatContainerRef.current === el) {
          el.scrollTop = target;
        }
      });
    }
  }, [messages, streaming, streamingContent]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    autoScrollRef.current = true;
    setShowJumpToLatest(false);
    await send(text);
  }, [input, streaming, send]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
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
      <div className="relative flex-1 min-h-0">
        <div
          ref={chatContainerRef}
          onScroll={handleScroll}
          onWheelCapture={(event) => {
            if (event.deltaY < 0) detachAutoScroll();
          }}
          onTouchMove={detachAutoScroll}
          className="h-full overflow-y-auto overscroll-contain px-4 py-3 space-y-4"
          aria-label="报告 AI 助手对话内容"
        >
        {loadingHistory ? (
          <div className="flex justify-center py-8">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          </div>
        ) : messages.length === 0 && !streaming ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-8">
            <Bot size={32} className="text-on-surface-variant/40 mb-3" />
            <p className="text-sm text-on-surface-variant/70">有关此报告的问题，欢迎提问</p>
          </div>
        ) : (
          <>
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
                    <MarkdownRenderer
                      content={sanitizeContent(msg.content)}
                      enableNumericCitations={false}
                    />
                  </div>
                )}
              </div>
            ))}

            {/* Streaming indicator */}
            {streaming && (
              <div className="flex justify-start">
                <div className="max-w-[95%] px-3 py-2 bg-surface-container-high rounded-xl rounded-tl-sm text-sm">
                  {streamingContent ? (
                    <MarkdownRenderer
                      content={sanitizeContent(streamingContent)}
                      enableNumericCitations={false}
                    />
                  ) : (
                    <div className="py-1 text-xs text-on-surface-variant" aria-live="polite">
                      正在生成回答...
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
        </div>
        {showJumpToLatest && (
          <button
            type="button"
            onClick={jumpToLatest}
            className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-outline-variant/30 bg-surface-container-high px-3 py-1.5 text-xs font-medium text-on-surface shadow-sm hover:bg-surface-container-highest"
          >
            跳到最新回复
          </button>
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
            onClick={() => void handleSend()}
            disabled={!input.trim() || streaming}
            className="p-2.5 rounded-lg bg-primary text-on-primary hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
            aria-label="发送"
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

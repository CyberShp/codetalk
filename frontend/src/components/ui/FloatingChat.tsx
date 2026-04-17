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

const RESEARCH_STATUS: Record<number, string> = {
  1: ">> ANALYZING_STRUCTURE...",
  2: ">> LINKING_CONTEXT...",
  3: ">> DEEP_INSPECTION...",
  4: ">> CROSS_REFERENCING...",
  5: ">> SYNTHESIZING...",
};

function checkResearchComplete(text: string): boolean {
  return (
    text.includes("## Final Conclusion") ||
    text.includes("## 最终结论") ||
    text.includes("# Final Conclusion") ||
    text.includes("# 最终结论")
  );
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
  const [deepResearch, setDeepResearch] = useState(false);
  const [researchIteration, setResearchIteration] = useState(0);
  const [isAutoResearching, setIsAutoResearching] = useState(false);
  const [researchStatus, setResearchStatus] = useState("");

  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Ref-tracked values for use inside async callbacks where state may be stale
  const messagesRef = useRef(messages);
  const researchIterationRef = useRef(0);
  // Tracks the ID of the assistant message currently being streamed — used by the
  // catch block to write error/abort text to the right bubble without relying on
  // "last bubble" heuristics (which break when a new request starts mid-unwind).
  const activeAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  });

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
    // Only abort the controller — do NOT touch isStreaming / isAutoResearching here.
    // Unlocking the input while handleSend() is still unwinding (AbortError path)
    // creates a window where the user can start a new request, causing the old
    // request's catch/finally to overwrite the new request's placeholder message
    // and null out its abort controller.  handleSend's finally block is the sole
    // owner of cleanup.
    abortRef.current?.abort();
  }, []);

  // Stream one API request, appending chunks to the given assistant message ID.
  // Returns the full accumulated response text.
  const streamSingle = useCallback(
    async (
      history: { role: string; content: string }[],
      assistantMsgId: string,
      controller: AbortController,
      drFlag: boolean,
    ): Promise<string> => {
      const response = await api.repos.chat.stream(
        repoId,
        history,
        { includedFiles: currentPageFilePaths, deepResearch: drFlag },
        controller.signal,
      );
      if (!response.ok) {
        const errText = await response.text().catch(() => "");
        throw new Error(errText || `HTTP ${response.status}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let full = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        full += chunk;
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last.id === assistantMsgId) {
            next[next.length - 1] = { ...last, content: last.content + chunk };
          }
          return next;
        });
      }
      return full;
    },
    [repoId, currentPageFilePaths],
  );

  const handleSend = useCallback(async () => {
    if (!input.trim() || isStreaming || isAutoResearching) return;

    const userContent = input.trim();

    // Step 8: reset research state for each new user query
    researchIterationRef.current = 0;
    setResearchIteration(0);
    setIsAutoResearching(false);
    setResearchStatus("");

    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      content: userContent,
    };
    const assistantId = `${Date.now() + 1}`;

    // Snapshot messages synchronously before any state updates (avoids stale closure)
    const baseHistory = [
      ...messagesRef.current.filter((m) => m.id !== "welcome"),
      userMsg,
    ].map((m) => ({ role: m.role, content: m.content }));

    activeAssistantIdRef.current = assistantId;
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "" },
    ]);
    setInput("");
    setIsStreaming(true);

    // Step 1: initialise research iteration counter when deep research is active
    if (deepResearch) {
      researchIterationRef.current = 1;
      setResearchIteration(1);
      setResearchStatus(RESEARCH_STATUS[1]);
    }

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      // Accumulated full history — avoids reading stale React state inside the loop
      let accHistory = baseHistory;

      // Step 4: first stream — pass deepResearch flag
      const firstResponse = await streamSingle(accHistory, assistantId, controller, deepResearch);
      accHistory = [...accHistory, { role: "assistant", content: firstResponse }];

      // Step 5: auto-continue loop (deep research only)
      if (deepResearch) {
        let lastResponse = firstResponse;

        while (
          !checkResearchComplete(lastResponse) &&
          researchIterationRef.current < 5
        ) {
          if (controller.signal.aborted) break;

          const nextIter = researchIterationRef.current + 1;
          researchIterationRef.current = nextIter;
          setResearchIteration(nextIter);
          setResearchStatus(RESEARCH_STATUS[nextIter] ?? ">> PROCESSING...");
          setIsAutoResearching(true);

          // Brief pause so the user sees the status update before the next request
          await new Promise<void>((r) => setTimeout(r, 1000));
          if (controller.signal.aborted) break;

          // Append hidden "Continue" turn to the accumulated history only —
          // it is NOT added to the messages state so it never appears in the UI.
          accHistory = [
            ...accHistory,
            { role: "user", content: "Continue the research" },
          ];

          // Add visible placeholder for the next assistant response
          const continueId = `continue-${Date.now()}`;
          activeAssistantIdRef.current = continueId;
          setMessages((prev) => [
            ...prev,
            { id: continueId, role: "assistant", content: "" },
          ]);

          lastResponse = await streamSingle(accHistory, continueId, controller, true);
          accHistory = [...accHistory, { role: "assistant", content: lastResponse }];
        }
      }
    } catch (e) {
      const targetId = activeAssistantIdRef.current;
      if ((e as Error).name === "AbortError") {
        if (targetId) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === targetId && !m.content
                ? { ...m, content: "> 已停止生成。" }
                : m,
            ),
          );
        }
        return;
      }
      if (targetId) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === targetId && !m.content
              ? { ...m, content: `> ⚠️ ${(e as Error).message || "连接失败，请稍后重试。"}` }
              : m,
          ),
        );
      }
    } finally {
      setIsStreaming(false);
      setIsAutoResearching(false);
      setResearchStatus("");
      // Only null out the controller if it's still ours — a new request may have
      // replaced abortRef.current while this one was unwinding from an abort.
      if (abortRef.current?.signal.aborted) {
        abortRef.current = null;
      }
    }
  }, [input, isStreaming, isAutoResearching, deepResearch, streamSingle]);

  const busy = isStreaming || isAutoResearching;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {/* breathe keyframes — scoped inside this component, no tailwind.config change needed */}
      <style>{`
        @keyframes breathe {
          0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
          50%       { transform: scaleX(1);   opacity: 1;   }
        }
      `}</style>

      {isOpen && (
        <div
          className="mb-4 w-80 sm:w-96 animate-in fade-in slide-in-from-bottom-6 duration-500 ease-out rounded-t-2xl overflow-hidden outline outline-1 outline-white/10"
          style={{ height: "min(500px, calc(100vh - 7rem))" }}
        >
          <GlassPanel className="h-full flex flex-col overflow-hidden shadow-[0_-20px_80px_-20px_rgba(0,0,0,0.8)] border-none bg-[#0D0D0F]/90 backdrop-blur-2xl">
            {/* Cyber Cap Header */}
            <div className="relative h-11 shrink-0 flex items-center justify-between px-4 bg-black/40 border-b border-white/5">
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

            {/* Step 3: Research Phase Ribbon — only visible during deep research */}
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
                // Step 7: Halo Conclusion — highlight assistant messages that contain a conclusion marker
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

            {/* Step 6: Auto-research status bar — Synaptic Pulse */}
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

                {/* Step 2: DEEP Research Toggle — between input and send button */}
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
          </GlassPanel>
        </div>
      )}

      {/* Floating action button */}
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

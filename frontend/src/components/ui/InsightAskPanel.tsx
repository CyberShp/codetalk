"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { Send, Bot, Loader2, Square, Link as LinkIcon, FileText, Code2, Sparkles } from "lucide-react";
import MarkdownRenderer from "./MarkdownRenderer";
import { api } from "@/lib/api";
import type { EvidenceItem } from "@/lib/types";

type Evidence = EvidenceItem;

/**
 * Convert standalone [N] markers in LLM output to markdown link form [[N]](#citation-N)
 * so MarkdownRenderer's `a` handler can render them as interactive badges.
 *
 * Rules:
 * - Link text [[N]] keeps brackets so extractText() returns "[N]", matching NUMERIC_CITATION_RE.
 * - Skips triple-backtick code fences and single-backtick inline code to avoid
 *   corrupting patterns like arr[1] inside code blocks.
 */
function preprocessCitations(text: string): string {
  const CODE_RE = /```[\s\S]*?```|`[^`\n]+`/g;
  const CITATION_RE = /\[(\d{1,2})\](?!\()/g;

  const segments: string[] = [];
  let lastEnd = 0;

  for (const match of text.matchAll(CODE_RE)) {
    const start = match.index!;
    // Transform citations in prose segment before this code block
    segments.push(text.slice(lastEnd, start).replace(CITATION_RE, "[[$1]](#citation-$1)"));
    // Keep code block unchanged
    segments.push(match[0]);
    lastEnd = start + match[0].length;
  }
  // Remaining prose after last code block
  segments.push(text.slice(lastEnd).replace(CITATION_RE, "[[$1]](#citation-$1)"));

  return segments.join("");
}

// Pure string formatting — converts evidence array to a system message for deepwiki context injection.
// This is format conversion only (not analysis logic) per CLAUDE.md core constraints.
function formatEvidenceAsSystem(evidence: Evidence[]): string {
  if (!evidence.length) return "";
  const parts = ["[代码证据 — 请在回答中使用 [1] [2] 等标记引用对应来源]\n"];
  evidence.forEach((ev, i) => {
    const loc =
      ev.type === "code"
        ? `${ev.file || ev.title}${ev.line_range ? ` (L${ev.line_range})` : ""}`
        : `文档: ${ev.title}`;
    parts.push(`[${i + 1}] ${loc}`);
    parts.push(ev.content);
    parts.push("");
  });
  return parts.join("\n");
}

function checkResearchComplete(text: string): boolean {
  return (
    text.includes("## Final Conclusion") ||
    text.includes("## 最终结论") ||
    text.includes("# Final Conclusion") ||
    text.includes("# 最终结论")
  );
}

const RESEARCH_STATUS: Record<number, string> = {
  1: ">> ANALYZING_STRUCTURE...",
  2: ">> LINKING_CONTEXT...",
  3: ">> DEEP_INSPECTION...",
  4: ">> CROSS_REFERENCING...",
  5: ">> SYNTHESIZING...",
};

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  evidence?: Evidence[];
  prompt?: string;
}

interface Props {
  taskId: string;
  repoId?: string; // repo-centric chat & Deep Research require this
  className?: string;
}

export default function InsightAskPanel({ taskId, repoId, className }: Props) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [currentEvidence, setCurrentEvidence] = useState<Evidence[]>([]);
  const [highlightedEvidenceId, setHighlightedEvidenceId] = useState<string | null>(null);
  // Tracks which answer's evidence the sidecar is showing.
  // null = show currentEvidence (in-progress Phase 1 results).
  const [activeAnswerId, setActiveAnswerId] = useState<string | null>(null);

  // Deep Research state
  const [deepResearch, setDeepResearch] = useState(false);
  const [researchIteration, setResearchIteration] = useState(0);
  const [isAutoResearching, setIsAutoResearching] = useState(false);
  const [researchStatus, setResearchStatus] = useState("");

  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef(messages);
  // Ref-tracked iteration counter — safe to read inside async loops without stale closure issues
  const researchIterationRef = useRef(0);
  // Tracks the ID of the assistant message currently being streamed — used by the
  // catch block to write error/abort text to the right bubble without relying on
  // "last bubble" heuristics (which break when a new request starts mid-unwind).
  const activeAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Only abort the controller — do NOT touch isStreaming / isAutoResearching here.
  // handleSend's finally block is the sole owner of cleanup, preventing race conditions
  // where the old request's catch/finally could overwrite a new request's state.
  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // Stream one API request via repo-centric endpoint, appending chunks to the given
  // assistant message ID. Returns the full accumulated response text. Re-throws on error
  // so handleSend's catch/finally can own all state cleanup.
  const startStreaming = useCallback(
    async (
      id: string,
      history: { role: string; content: string }[],
      evidence: Evidence[],
      prompt: string,
      drFlag: boolean,
    ): Promise<string> => {
      setMessages((prev) => [...prev, { id, role: "assistant", content: "", evidence, prompt }]);
      setActiveAnswerId(id);
      activeAssistantIdRef.current = id;

      const controller = abortRef.current!;

      const response = await api.repos.chat.stream(
        repoId!,
        history,
        { deepResearch: drFlag },
        controller.signal,
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let fullText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        fullText += chunk;
        setMessages((prev) => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last.id === id) {
            msgs[msgs.length - 1] = { ...last, content: last.content + chunk };
          }
          return msgs;
        });
      }

      return fullText;
    },
    [repoId],
  );

  // Legacy task-scoped streaming — used as fallback when repoId is not available.
  const startStreamingLegacy = useCallback(
    async (
      id: string,
      history: { role: string; content: string }[],
      evidence: Evidence[],
      prompt: string,
    ): Promise<void> => {
      setMessages((prev) => [...prev, { id, role: "assistant", content: "", evidence, prompt }]);
      setActiveAnswerId(id);
      activeAssistantIdRef.current = id;

      const controller = abortRef.current!;

      const response = await api.chat.stream(taskId, history, controller.signal, evidence);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        setMessages((prev) => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last.id === id) {
            msgs[msgs.length - 1] = { ...last, content: last.content + chunk };
          }
          return msgs;
        });
      }
    },
    [taskId],
  );

  const handleSend = useCallback(async () => {
    if (!input.trim() || isStreaming || isSearching || isAutoResearching) return;
    if (deepResearch && !repoId) return;

    const query = input.trim();
    const userMsg: Message = { id: Date.now().toString(), role: "user", content: query };
    const assistantId = (Date.now() + 1).toString();

    // Reset research state for each new user query
    researchIterationRef.current = 0;
    setResearchIteration(0);
    setIsAutoResearching(false);
    setResearchStatus("");

    const controller = new AbortController();
    abortRef.current = controller;

    // Phase 1: Zoekt evidence retrieval (always task-scoped)
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsSearching(true);
    setCurrentEvidence([]);

    let evidence: Evidence[] = [];
    try {
      const ctx = await api.chat.askContext(taskId, query, controller.signal);
      if (controller.signal.aborted) return;
      evidence = (ctx.evidence ?? []).map((e, i) => ({ ...e, id: `ev-${i}` }));
      setCurrentEvidence(evidence);
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
    } finally {
      setIsSearching(false);
    }

    if (controller.signal.aborted) return;

    setIsStreaming(true);

    // Deep Research iteration initialisation
    if (deepResearch) {
      researchIterationRef.current = 1;
      setResearchIteration(1);
      setResearchStatus(RESEARCH_STATUS[1]);
    }

    try {
      if (!repoId) {
        // Fallback: task-scoped path — no Deep Research support
        const history = [
          ...messagesRef.current.filter((m) => m.content).map((m) => ({
            role: m.role,
            content: m.content,
          })),
          { role: "user" as const, content: query },
        ];
        await startStreamingLegacy(assistantId, history, evidence, query);
        return;
      }

      // Phase 2: Build history with evidence injected as system message
      const evidenceSystem = formatEvidenceAsSystem(evidence);
      const baseHistory: { role: string; content: string }[] = [];
      if (evidenceSystem) {
        baseHistory.push({ role: "system", content: evidenceSystem });
      }
      baseHistory.push(
        ...messagesRef.current.filter((m) => m.content).map((m) => ({
          role: m.role,
          content: m.content,
        })),
        { role: "user", content: query },
      );

      // Phase 3: First stream via repo-centric API
      const fullText = await startStreaming(assistantId, baseHistory, evidence, query, deepResearch);
      let accHistory = [...baseHistory, { role: "assistant", content: fullText }];

      // Phase 4: Auto-continue loop (Deep Research only)
      if (deepResearch) {
        let lastResponse = fullText;

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

          // Append hidden "Continue" turn to accumulated history only —
          // it is NOT added to messages state so it never appears in the UI.
          accHistory = [
            ...accHistory,
            { role: "user", content: "Continue the research" },
          ];

          // Add visible placeholder for the next assistant response
          const continueId = `continue-${Date.now()}`;
          activeAssistantIdRef.current = continueId;
          setMessages((prev) => [
            ...prev,
            { id: continueId, role: "assistant", content: "", evidence, prompt: query },
          ]);

          const continueResponse = await api.repos.chat.stream(
            repoId,
            accHistory,
            { deepResearch: true },
            controller.signal,
          );
          if (!continueResponse.ok) {
            const errText = await continueResponse.text().catch(() => "");
            throw new Error(errText || `HTTP ${continueResponse.status}`);
          }

          const reader = continueResponse.body!.getReader();
          const decoder = new TextDecoder();
          lastResponse = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            lastResponse += chunk;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === continueId ? { ...m, content: m.content + chunk } : m,
              ),
            );
          }

          accHistory = [...accHistory, { role: "assistant", content: lastResponse }];
        }
      }
    } catch (e) {
      const targetId = activeAssistantIdRef.current;
      if ((e as Error).name === "AbortError") {
        if (targetId) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === targetId && !m.content ? { ...m, content: "> 已停止生成。" } : m,
            ),
          );
        }
        return;
      }
      // Non-abort errors: write error message to the active bubble if it's still empty
      if (targetId) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === targetId && !m.content
              ? { ...m, content: "> ⚠️ 检索或生成失败，请重试。" }
              : m,
          ),
        );
      }
    } finally {
      setIsStreaming(false);
      setIsAutoResearching(false);
      setResearchStatus("");
      if (abortRef.current?.signal.aborted) {
        abortRef.current = null;
      }
    }
  }, [input, isStreaming, isSearching, isAutoResearching, taskId, repoId, deepResearch, startStreaming, startStreamingLegacy]);

  // Sidecar shows the evidence for whichever answer was last interacted with.
  // Falls back to currentEvidence while Phase 1 (Zoekt search) is in progress.
  const sidecarEvidence = activeAnswerId
    ? (messages.find((msg) => msg.id === activeAnswerId)?.evidence ?? [])
    : currentEvidence;
  const rootClassName = className ?? "h-[calc(100vh-8rem)]";

  const busy = isStreaming || isSearching || isAutoResearching;

  return (
    <div className={`flex min-h-0 flex-col bg-surface ${rootClassName}`}>
      {/* breathe keyframes — scoped inside this component, no tailwind.config change needed */}
      <style>{`
        @keyframes breathe {
          0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
          50%       { transform: scaleX(1);   opacity: 1;   }
        }
      `}</style>

      {/* Search Header - Sticky */}
      <div className="sticky top-0 z-20 border-b border-white/5 bg-surface/95 px-6 py-3 backdrop-blur-md">
        <div className="flex w-full gap-4">
          <div className="flex-1 relative">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
              placeholder="搜索代码实现、架构或库用法..."
              disabled={busy}
              className="w-full h-10 bg-white/[0.03] rounded-xl px-4 text-on-surface outline-none border border-white/10 focus:border-primary/50 focus:ring-1 focus:ring-primary/50 transition-all font-display"
            />
          </div>
          <div className="flex gap-2">
            {/* DEEP Research Toggle — only rendered when repoId is available */}
            {repoId && (
              <button
                onClick={() => setDeepResearch((v) => !v)}
                disabled={busy}
                className={`h-10 px-3 rounded-xl text-[10px] font-mono tracking-wider transition-all duration-300 shrink-0 border disabled:opacity-30 ${
                  deepResearch
                    ? "bg-primary/15 text-primary border-primary/30 shadow-[0_0_12px_rgba(164,230,255,0.15)]"
                    : "bg-white/[0.03] text-on-surface-variant/40 border-white/10 hover:text-on-surface-variant/70 hover:border-white/20"
                }`}
                title="深度研究模式：自动进行 5 轮迭代深入分析"
              >
                DEEP
              </button>
            )}

            {busy ? (
              <button
                onClick={handleStop}
                className="px-6 h-10 flex items-center gap-2 rounded-xl bg-tertiary/20 text-tertiary hover:bg-tertiary/30 border border-tertiary/20 transition-all font-bold text-xs uppercase tracking-widest"
              >
                <Square size={14} fill="currentColor" /> 停止
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="px-8 h-10 flex items-center gap-2 rounded-xl bg-primary text-on-primary hover:shadow-lg disabled:opacity-20 transition-all font-bold text-xs uppercase tracking-widest"
              >
                <Send size={14} /> 提问
              </button>
            )}
          </div>
        </div>
      </div>

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

      <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
        {/* Main Content: The Answer Scroll Area */}
        <div className="flex-1 overflow-y-auto scrollbar-thin scroll-smooth" ref={scrollRef}>
          <div className="w-full p-8 xl:px-10 xl:py-10">
            <div className="space-y-12">
            {messages.length === 0 && !isSearching && (
              <div className="py-20 text-center space-y-4 opacity-40">
                <div className="flex justify-center"><Bot size={48} className="text-primary" /></div>
                <h2 className="text-xl font-display font-medium">AI 问答</h2>
                <p className="mx-auto max-w-md text-sm">输入代码符号、模块、调用链或实现问题。系统会先检索源码证据，再生成回答。</p>
              </div>
            )}

            {messages.map((m) =>
              m.role === "user" ? (
                <section
                  key={m.id}
                  id={`question-${m.id}`}
                  className="scroll-mt-24 rounded-2xl border border-white/10 bg-white/[0.02] px-5 py-4"
                >
                  <p className="mb-2 text-[11px] font-black uppercase tracking-[0.16em] text-on-surface-variant/50">
                    用户问题
                  </p>
                  <p className="text-sm leading-7 text-on-surface">{m.content}</p>
                </section>
              ) : (
                <div
                  key={m.id}
                  className={`group animate-in fade-in duration-1000 ${
                    checkResearchComplete(m.content)
                      ? "ring-1 ring-primary/20 rounded-2xl p-4 bg-primary/[0.02] shadow-[0_0_30px_rgba(164,230,255,0.06)]"
                      : ""
                  }`}
                >
                  {/* User query context indicator */}
                  <div className="flex items-center gap-2 mb-4 opacity-50 text-[11px] font-bold uppercase tracking-widest">
                    <Sparkles size={12} className="text-secondary" />
                    <span>问题：&ldquo;{m.prompt}&rdquo;</span>
                  </div>

                  <div className="prose prose-invert prose-slate max-w-none
                    [&_p]:leading-8 [&_p]:text-on-surface-variant/90 [&_p]:text-[15px]
                    [&_pre]:bg-black/30 [&_pre]:p-6 [&_pre]:rounded-2xl [&_pre]:border [&_pre]:border-white/5 [&_pre]:my-6
                    [&_code]:text-primary-container [&_code]:font-mono [&_code]:bg-white/5 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded
                    [&_h1]:text-2xl [&_h2]:text-xl [&_h3]:text-lg
                    [&_ul]:list-disc [&_ul]:pl-6 [&_li]:my-2
                    [&_blockquote]:border-l-primary [&_blockquote]:bg-primary/5 [&_blockquote]:p-4 [&_blockquote]:rounded-r-xl">
                    <MarkdownRenderer
                      content={preprocessCitations(m.content)}
                      onCitationClick={(citationId) => {
                        if (citationId.startsWith("citation-")) {
                          const index = parseInt(citationId.split("-")[1], 10) - 1;
                          const evidenceId = m.evidence?.[index]?.id;
                          if (evidenceId) {
                            setActiveAnswerId(m.id);
                            setHighlightedEvidenceId(evidenceId);
                            document.getElementById(`evidence-${evidenceId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
                          }
                        }
                      }}
                    />
                    {m.id === messages[messages.length - 1].id && !m.content && isStreaming && (
                      <div className="flex items-center gap-3 py-4 text-primary/50 animate-pulse font-display italic">
                        <Loader2 size={18} className="animate-spin" />
                        正在整合源码见解...
                      </div>
                    )}
                  </div>

                  {/* Footnote style sources for each answer */}
                  {m.evidence && m.evidence.length > 0 && (
                    <div className="mt-8 pt-8 border-t border-white/5">
                      <h4 className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant/40 mb-4">引用的来源</h4>
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        {m.evidence.map((ev, i) => (
                          <button
                            key={ev.id}
                            onClick={() => { setActiveAnswerId(m.id); setHighlightedEvidenceId(ev.id); }}
                            className="flex items-center gap-3 p-3 rounded-xl bg-white/[0.02] border border-white/5 hover:border-primary/20 hover:bg-white/[0.04] transition-all text-left"
                          >
                            <span className="text-[11px] font-black text-primary/40">[{i + 1}]</span>
                            <div className="min-w-0">
                              <p className="text-[11px] font-bold text-on-surface-variant truncate">{ev.title}</p>
                              <p className="text-[9px] text-on-surface-variant/40 uppercase tracking-tighter">Line {ev.line_range}</p>
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            )}
            </div>
          </div>
        </div>

        {/* Right Panel: Sidecar Evidence View */}
        <div className="flex h-[36vh] shrink-0 flex-col overflow-hidden border-t border-white/5 bg-black/10 p-6 xl:h-auto xl:w-[520px] xl:border-l xl:border-t-0">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-[11px] font-black uppercase tracking-[0.2em] text-on-surface-variant">证据来源</h3>
            {isSearching && <Loader2 size={14} className="animate-spin text-primary" />}
          </div>

          <div className="flex-1 overflow-y-auto space-y-4 pr-1 scrollbar-thin">
            {sidecarEvidence.map((ev, i) => (
              <div
                key={ev.id}
                id={`evidence-${ev.id}`}
                className={`group p-5 rounded-2xl border transition-all duration-500 ${
                  highlightedEvidenceId === ev.id
                  ? "bg-primary/10 border-primary/50 ring-1 ring-primary/30"
                  : "bg-white/[0.02] border-white/5"
                }`}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[10px] font-black text-primary">[{i + 1}]</span>
                    <span className="text-[11px] font-bold font-mono text-on-surface/80 truncate">{ev.title}</span>
                  </div>
                  <div className="shrink-0 p-1.5 rounded-lg bg-white/5 text-on-surface-variant/40">
                    {ev.type === "code" ? <Code2 size={12} /> : <FileText size={12} />}
                  </div>
                </div>

                <div className="relative">
                  <pre className="text-[11px] font-mono leading-relaxed text-on-surface-variant/70 bg-black/40 p-4 rounded-xl border border-white/5 overflow-x-auto">
                    {ev.content}
                  </pre>
                </div>

                <div className="mt-3 flex items-center justify-between">
                  <span className="text-[10px] font-mono text-on-surface-variant/30">Lines {ev.line_range}</span>
                </div>
              </div>
            ))}

            {!isSearching && sidecarEvidence.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center opacity-20 text-center p-10">
                <LinkIcon size={40} className="mb-4" />
                <p className="text-[10px] font-black uppercase tracking-widest">没有关联的证据</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Auto-research status bar — Synaptic Pulse */}
      {isAutoResearching && (
        <div className="border-t border-white/5 bg-black/20 px-6 py-2 shrink-0">
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

      {/* Status Bar */}
      <div className="flex h-8 items-center justify-between border-t border-white/5 bg-black/40 px-6">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 text-[9px] font-bold text-on-surface-variant/40 uppercase tracking-widest">
            <div className={`w-1 h-1 rounded-full ${
              isAutoResearching ? "bg-primary animate-pulse"
              : isStreaming || isSearching ? "bg-primary animate-pulse"
              : "bg-green-500/50"
            }`} />
            {isAutoResearching
              ? `深度研究 ${researchIteration}/5 轮`
              : isSearching ? "检索代码证据..."
              : isStreaming ? "深度推理中..."
              : "系统就绪"}
          </div>
        </div>
        <div className="text-[9px] font-mono font-bold uppercase tracking-widest text-on-surface-variant/20">
          {sidecarEvidence.length} 条来源
        </div>
      </div>
    </div>
  );
}

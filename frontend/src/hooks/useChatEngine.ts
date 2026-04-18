"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { ChatWsClient } from "@/lib/chatWs";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export const RESEARCH_STATUS: Record<number, string> = {
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

export interface ChatEngine {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  isStreaming: boolean;
  deepResearch: boolean;
  setDeepResearch: React.Dispatch<React.SetStateAction<boolean>>;
  researchIteration: number;
  isAutoResearching: boolean;
  researchStatus: string;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  inputRef: React.RefObject<HTMLInputElement | null>;
  handleSend: () => Promise<void>;
  handleStop: () => void;
}

interface UseChatEngineOptions {
  repoId: string;
  currentPageFilePaths?: string[];
}

export function useChatEngine({
  repoId,
  currentPageFilePaths,
}: UseChatEngineOptions): ChatEngine {
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
  const wsClientRef = useRef<ChatWsClient | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef(messages);
  const researchIterationRef = useRef(0);
  const activeAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  });

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      wsClientRef.current?.close();
    };
  }, []);

  const handleStop = useCallback(() => {
    if (wsClientRef.current?.connected) {
      wsClientRef.current.stop();
    } else {
      abortRef.current?.abort();
    }
  }, []);

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

    if (deepResearch) {
      researchIterationRef.current = 1;
      setResearchIteration(1);
      setResearchStatus(RESEARCH_STATUS[1]);
    }

    // --- Attempt WS path ---
    if (!wsClientRef.current?.connected) {
      const wsClient = new ChatWsClient({
        repoId,
        onChunk: (chunk) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last.id === activeAssistantIdRef.current) {
              next[next.length - 1] = { ...last, content: last.content + chunk };
            }
            return next;
          });
        },
        onDone: () => {
          setIsStreaming(false);
          setIsAutoResearching(false);
          setResearchStatus("");
          if (abortRef.current?.signal.aborted) {
            abortRef.current = null;
          }
        },
        onError: (message) => {
          const targetId = activeAssistantIdRef.current;
          if (targetId) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === targetId && !m.content
                  ? { ...m, content: `> ⚠️ ${message}` }
                  : m,
              ),
            );
          }
          setIsStreaming(false);
          setIsAutoResearching(false);
          setResearchStatus("");
        },
        onResearchRound: (round) => {
          researchIterationRef.current = round;
          setResearchIteration(round);
          setResearchStatus(RESEARCH_STATUS[round] ?? ">> PROCESSING...");
          if (round > 1) {
            setIsAutoResearching(true);
            const continueId = `continue-${Date.now()}`;
            activeAssistantIdRef.current = continueId;
            setMessages((prev) => [
              ...prev,
              { id: continueId, role: "assistant", content: "" },
            ]);
          }
        },
      });
      wsClientRef.current = wsClient;
      const connected = await wsClient.connect();
      if (!connected) {
        wsClientRef.current = null;
      }
    }

    if (wsClientRef.current?.connected) {
      wsClientRef.current.send({
        messages: baseHistory,
        file_path: undefined,
        included_files: currentPageFilePaths,
        deep_research: deepResearch,
      });
      return;
    }

    // --- HTTP fallback path ---
    try {
      const controller = new AbortController();
      abortRef.current = controller;

      let accHistory = baseHistory;

      const firstResponse = await streamSingle(accHistory, assistantId, controller, deepResearch);
      accHistory = [...accHistory, { role: "assistant", content: firstResponse }];

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

          await new Promise<void>((r) => setTimeout(r, 1000));
          if (controller.signal.aborted) break;

          accHistory = [
            ...accHistory,
            { role: "user", content: "Continue the research" },
          ];

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
      if (abortRef.current?.signal.aborted) {
        abortRef.current = null;
      }
    }
  }, [input, isStreaming, isAutoResearching, deepResearch, streamSingle, repoId, currentPageFilePaths]);

  return {
    messages,
    setMessages,
    input,
    setInput,
    isStreaming,
    deepResearch,
    setDeepResearch,
    researchIteration,
    isAutoResearching,
    researchStatus,
    scrollRef,
    inputRef,
    handleSend,
    handleStop,
  };
}

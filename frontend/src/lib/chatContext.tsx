"use client";
import React, { createContext, useCallback, useContext, useEffect, useReducer, useRef } from "react";
import type { WorkspaceChatMessage, ChatMode } from "@/lib/types";
import { api } from "@/lib/api";

interface ChatSlice {
  messages: WorkspaceChatMessage[];
  streaming: boolean;
  streamingContent: string;
  loadingHistory: boolean;
}

const defaultSlice = (): ChatSlice => ({
  messages: [],
  streaming: false,
  streamingContent: "",
  loadingHistory: false,
});

type State = Map<string, ChatSlice>;
type Action =
  | { type: "patch"; key: string; patch: Partial<ChatSlice> }
  | { type: "set_messages"; key: string; messages: WorkspaceChatMessage[] }
  | { type: "delete"; key: string };

function reducer(state: State, action: Action): State {
  const next = new Map(state);
  if (action.type === "delete") {
    next.delete(action.key);
    return next;
  }
  const cur = next.get(action.key) ?? defaultSlice();
  if (action.type === "patch") {
    next.set(action.key, { ...cur, ...action.patch });
  } else {
    next.set(action.key, { ...cur, messages: action.messages, loadingHistory: false });
  }
  return next;
}

interface CtxValue {
  state: State;
  init: (wsId: string) => Promise<void>;
  send: (wsId: string, text: string, mode: ChatMode, module?: string) => Promise<void>;
  stop: (wsId: string) => void;
  cleanup: (wsId: string) => void;
}

const ChatCtx = createContext<CtxValue | null>(null);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, new Map<string, ChatSlice>());
  const stateRef = useRef(state);
  stateRef.current = state;
  const aborts = useRef(new Map<string, AbortController>());
  const pendingCleanup = useRef(new Set<string>());

  const init = useCallback(async (wsId: string) => {
    if (stateRef.current.get(wsId)?.messages.length) return;
    dispatch({ type: "patch", key: wsId, patch: { loadingHistory: true } });
    try {
      const msgs = await api.workspaces.chatHistory(wsId);
      dispatch({ type: "set_messages", key: wsId, messages: msgs });
    } catch {
      dispatch({ type: "patch", key: wsId, patch: { loadingHistory: false } });
    }
  }, []);

  const send = useCallback(async (wsId: string, text: string, mode: ChatMode, module?: string) => {
    if (stateRef.current.get(wsId)?.streaming) return;
    const userBubble: WorkspaceChatMessage = {
      id: `local-${Date.now()}`,
      workspace_id: wsId,
      mode,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    // Capture snapshot before dispatch so the error path always includes the user bubble,
    // even if the render hasn't flushed yet when a fast failure hits the catch block.
    const messagesWithUserBubble = [...(stateRef.current.get(wsId)?.messages ?? []), userBubble];
    const mergeAbortSnapshot = (
      history: WorkspaceChatMessage[],
      accumulated: string,
    ): WorkspaceChatMessage[] => {
      let next = history.some((msg) => msg.role === "user" && msg.content === text)
        ? history
        : [...history, userBubble];
      const partial = accumulated.trim();
      if (partial && !next.some((msg) => msg.role === "assistant" && msg.content.includes(partial.slice(0, 80)))) {
        next = [
          ...next,
          {
            id: `abort-${Date.now()}`,
            workspace_id: wsId,
            mode,
            role: "assistant" as const,
            content: `${accumulated}\n\n[interrupted: partial answer saved]`,
            created_at: new Date().toISOString(),
          },
        ];
      }
      return next;
    };
    dispatch({
      type: "patch",
      key: wsId,
      patch: { messages: messagesWithUserBubble, streaming: true, streamingContent: "" },
    });
    const abort = new AbortController();
    aborts.current.set(wsId, abort);
    try {
      const res = await api.workspaces.chatStream(wsId, text, mode, module, abort.signal);
      if (!res.ok || !res.body) {
        const detail = await res.text().catch(() => "");
        throw new Error(detail.trim() || `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let accumulated = "";
      let streamError = "";
      while (!abort.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6)) as {
              content?: string;
              done: boolean;
              error?: string;
            };
            if (evt.error) {
              streamError = evt.error;
              accumulated += `\n\n⚠️ 发送失败：${evt.error}`;
              dispatch({ type: "patch", key: wsId, patch: { streamingContent: accumulated } });
              break;
            }
            if (evt.done) break;
            if (evt.content) {
              accumulated += evt.content;
              dispatch({ type: "patch", key: wsId, patch: { streamingContent: accumulated } });
            }
          } catch {
            // ignore malformed SSE lines
          }
        }
      }
      if (streamError && !abort.signal.aborted) {
        dispatch({
          type: "patch",
          key: wsId,
          patch: {
            messages: [
              ...messagesWithUserBubble,
              {
                id: `err-${Date.now()}`,
                workspace_id: wsId,
                mode,
                role: "assistant" as const,
                content: `⚠️ 发送失败：${streamError}`,
                created_at: new Date().toISOString(),
              },
            ],
            streaming: false,
            streamingContent: "",
          },
        });
        return;
      }
      if (abort.signal.aborted) {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      const updated = await api.workspaces
        .chatHistory(wsId)
        .catch(() => stateRef.current.get(wsId)?.messages ?? []);
      dispatch({
        type: "set_messages",
        key: wsId,
        messages: abort.signal.aborted ? mergeAbortSnapshot(updated, accumulated) : updated,
      });
      dispatch({ type: "patch", key: wsId, patch: { streaming: false, streamingContent: "" } });
    } catch (e) {
      if (abort.signal.aborted || (e instanceof Error && e.name === "AbortError")) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const updated = await api.workspaces
          .chatHistory(wsId)
          .catch(() => stateRef.current.get(wsId)?.messages ?? messagesWithUserBubble);
        dispatch({ type: "set_messages", key: wsId, messages: mergeAbortSnapshot(updated, "") });
        dispatch({ type: "patch", key: wsId, patch: { streaming: false, streamingContent: "" } });
        return;
      }
      const detail = e instanceof Error && e.message ? `：${e.message}` : "";
      dispatch({
        type: "patch",
        key: wsId,
        patch: {
          messages: [
            ...messagesWithUserBubble,
            {
              id: `err-${Date.now()}`,
              workspace_id: wsId,
              mode,
              role: "assistant" as const,
              content: `⚠️ 发送失败${detail}`,
              created_at: new Date().toISOString(),
            },
          ],
          streaming: false,
          streamingContent: "",
        },
      });
    } finally {
      aborts.current.delete(wsId);
    }
  }, []);

  const stop = useCallback((wsId: string) => {
    aborts.current.get(wsId)?.abort();
    aborts.current.delete(wsId);
  }, []);

  const cleanup = useCallback((wsId: string) => {
    if (stateRef.current.get(wsId)?.streaming) {
      pendingCleanup.current.add(wsId);
    } else {
      dispatch({ type: "delete", key: wsId });
    }
  }, []);

  useEffect(() => {
    for (const wsId of [...pendingCleanup.current]) {
      if (!state.get(wsId)?.streaming) {
        pendingCleanup.current.delete(wsId);
        dispatch({ type: "delete", key: wsId });
      }
    }
  }, [state]);

  return (
    <ChatCtx.Provider value={{ state, init, send, stop, cleanup }}>
      {children}
    </ChatCtx.Provider>
  );
}

export function useWsChat(wsId: string) {
  const ctx = useContext(ChatCtx);
  if (!ctx) throw new Error("useWsChat must be used inside ChatProvider");
  const { state, init, send, stop, cleanup } = ctx;

  useEffect(() => {
    return () => { cleanup(wsId); };
  }, [wsId, cleanup]);

  return {
    ...(state.get(wsId) ?? defaultSlice()),
    init: useCallback(() => init(wsId), [init, wsId]),
    send: useCallback(
      (text: string, mode: ChatMode, module?: string) => send(wsId, text, mode, module),
      [send, wsId],
    ),
    stop: useCallback(() => stop(wsId), [stop, wsId]),
  };
}

"use client";
import React, { createContext, useCallback, useContext, useEffect, useReducer, useRef } from "react";
import type { ChatMessage } from "@/lib/types";
import { api } from "@/lib/api";

interface TaskChatSlice {
  messages: ChatMessage[];
  streaming: boolean;
  streamingContent: string;
  loadingHistory: boolean;
}

const defaultSlice = (): TaskChatSlice => ({
  messages: [],
  streaming: false,
  streamingContent: "",
  loadingHistory: false,
});

type State = Map<string, TaskChatSlice>;
type Action =
  | { type: "patch"; key: string; patch: Partial<TaskChatSlice> }
  | { type: "set_messages"; key: string; messages: ChatMessage[] }
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
  init: (taskId: string) => Promise<void>;
  send: (taskId: string, text: string) => Promise<void>;
  stop: (taskId: string) => void;
  cleanup: (taskId: string) => void;
}

const TaskChatCtx = createContext<CtxValue | null>(null);

export function TaskChatProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, new Map<string, TaskChatSlice>());
  const stateRef = useRef(state);
  stateRef.current = state;
  const aborts = useRef(new Map<string, AbortController>());

  const init = useCallback(async (taskId: string) => {
    if (stateRef.current.get(taskId)?.messages.length) return;
    dispatch({ type: "patch", key: taskId, patch: { loadingHistory: true } });
    try {
      const msgs = await api.tasks.chatHistory(taskId);
      dispatch({ type: "set_messages", key: taskId, messages: msgs });
    } catch {
      dispatch({ type: "patch", key: taskId, patch: { loadingHistory: false } });
    }
  }, []);

  const send = useCallback(async (taskId: string, text: string) => {
    if (stateRef.current.get(taskId)?.streaming) return;
    const userBubble: ChatMessage = {
      id: Date.now(),
      task_id: taskId,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    // Capture snapshot before dispatch so the error path always includes the user bubble.
    const messagesWithUserBubble = [...(stateRef.current.get(taskId)?.messages ?? []), userBubble];
    dispatch({
      type: "patch",
      key: taskId,
      patch: { messages: messagesWithUserBubble, streaming: true, streamingContent: "" },
    });
    const abort = new AbortController();
    aborts.current.set(taskId, abort);
    try {
      const res = await fetch(api.tasks.chatUrl(taskId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
        signal: abort.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let accumulated = "";
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
            if (evt.done) break;
            if (evt.error) {
              accumulated += `\n\n⚠️ ${evt.error}`;
              dispatch({ type: "patch", key: taskId, patch: { streamingContent: accumulated } });
              break;
            }
            if (evt.content) {
              accumulated += evt.content;
              dispatch({ type: "patch", key: taskId, patch: { streamingContent: accumulated } });
            }
          } catch {
            // ignore malformed SSE lines
          }
        }
      }
      const updated = await api.tasks
        .chatHistory(taskId)
        .catch(() => stateRef.current.get(taskId)?.messages ?? []);
      dispatch({ type: "set_messages", key: taskId, messages: updated });
      dispatch({ type: "patch", key: taskId, patch: { streaming: false, streamingContent: "" } });
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") {
        dispatch({ type: "patch", key: taskId, patch: { streaming: false, streamingContent: "" } });
        return;
      }
      dispatch({
        type: "patch",
        key: taskId,
        patch: {
          messages: [
            ...messagesWithUserBubble,
            {
              id: Date.now() + 1,
              task_id: taskId,
              role: "assistant" as const,
              content: "抱歉，发生了网络错误，请稍后重试。",
              created_at: new Date().toISOString(),
            },
          ],
          streaming: false,
          streamingContent: "",
        },
      });
    } finally {
      aborts.current.delete(taskId);
    }
  }, []);

  const stop = useCallback((taskId: string) => {
    aborts.current.get(taskId)?.abort();
    aborts.current.delete(taskId);
    dispatch({ type: "patch", key: taskId, patch: { streaming: false, streamingContent: "" } });
  }, []);

  const cleanup = useCallback((taskId: string) => {
    if (stateRef.current.get(taskId)?.streaming) return;
    dispatch({ type: "delete", key: taskId });
  }, []);

  return (
    <TaskChatCtx.Provider value={{ state, init, send, stop, cleanup }}>
      {children}
    </TaskChatCtx.Provider>
  );
}

export function useTaskChat(taskId: string) {
  const ctx = useContext(TaskChatCtx);
  if (!ctx) throw new Error("useTaskChat must be used inside TaskChatProvider");
  const { state, init, send, stop, cleanup } = ctx;

  useEffect(() => {
    return () => { cleanup(taskId); };
  }, [taskId, cleanup]);

  return {
    ...(state.get(taskId) ?? defaultSlice()),
    init: useCallback(() => init(taskId), [init, taskId]),
    send: useCallback((text: string) => send(taskId, text), [send, taskId]),
    stop: useCallback(() => stop(taskId), [stop, taskId]),
  };
}

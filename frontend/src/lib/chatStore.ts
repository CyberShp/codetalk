/**
 * Workspace chat store — lifts SSE stream ownership out of React components.
 *
 * The SSE reader loop runs inside store actions and keeps running even when
 * the ChatPanel component unmounts (tab switch, brief navigation). An
 * AbortController is held only for the "stop" button — never tied to
 * component lifecycle.
 */
import { create } from "zustand";
import type { WorkspaceChatMessage, ChatMode } from "@/lib/types";
import { api } from "@/lib/api";

interface ChatStore {
  wsId: string | null;
  messages: WorkspaceChatMessage[];
  streaming: boolean;
  streamingContent: string;
  loadingHistory: boolean;
  _abort: AbortController | null;
  init: (wsId: string) => Promise<void>;
  send: (wsId: string, text: string, mode: ChatMode, module?: string) => Promise<void>;
  stop: () => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  wsId: null,
  messages: [],
  streaming: false,
  streamingContent: "",
  loadingHistory: false,
  _abort: null,

  init: async (wsId) => {
    const s = get();
    if (s.wsId === wsId && s.messages.length > 0) return;
    set({ wsId, messages: [], loadingHistory: true });
    try {
      const msgs = await api.workspaces.chatHistory(wsId);
      set({ messages: msgs, loadingHistory: false });
    } catch {
      set({ loadingHistory: false });
    }
  },

  send: async (wsId, text, mode, module) => {
    if (get().streaming) return;

    const userBubble: WorkspaceChatMessage = {
      id: `local-${Date.now()}`,
      workspace_id: wsId,
      mode,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    set((s) => ({
      messages: [...s.messages, userBubble],
      streaming: true,
      streamingContent: "",
    }));

    const abort = new AbortController();
    set({ _abort: abort });

    try {
      const res = await api.workspaces.chatStream(wsId, text, mode, module, abort.signal);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body!.getReader();
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
              content: string;
              done: boolean;
              error?: string;
            };
            if (evt.error) {
              accumulated += `\n\n⚠️ ${evt.error}`;
              set({ streamingContent: accumulated });
              break;
            }
            if (evt.done) break;
            if (evt.content) {
              accumulated += evt.content;
              set({ streamingContent: accumulated });
            }
          } catch {
            continue;
          }
        }
      }

      const updated = await api.workspaces
        .chatHistory(wsId)
        .catch(() => get().messages);
      set({ messages: updated, streaming: false, streamingContent: "" });
    } catch {
      set((s) => ({
        messages: [
          ...s.messages,
          {
            id: `err-${Date.now()}`,
            workspace_id: wsId,
            mode,
            role: "assistant" as const,
            content: "⚠️ 发送失败，请稍后重试。",
            created_at: new Date().toISOString(),
          },
        ],
        streaming: false,
        streamingContent: "",
      }));
    } finally {
      set({ _abort: null });
    }
  },

  stop: () => {
    get()._abort?.abort();
    set({ _abort: null, streaming: false, streamingContent: "" });
  },
}));

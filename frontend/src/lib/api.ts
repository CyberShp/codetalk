import type {
  Task,
  TaskCreate,
  LLMConfig,
  LLMConfigCreate,
  LLMConfigUpdate,
  GeneralSettings,
  ToolInfo,
} from "./types";

const BASE =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8100`
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100");

function extractErrorMessage(body: string): string {
  const text = body.trim();
  if (!text) return "请求失败";

  try {
    const parsed = JSON.parse(text) as unknown;
    if (typeof parsed === "string") return parsed;
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      const detail = record.detail ?? record.message;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // plain-text body
  }

  const firstLine = text
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean);
  return firstLine ?? text;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${extractErrorMessage(body)}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json();
}

export const api = {
  // ── 任务管理 ──
  tasks: {
    list: () => request<Task[]>("/api/tasks"),

    create: (data: TaskCreate) =>
      request<Task>("/api/tasks", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    get: (id: string) => request<Task>(`/api/tasks/${id}`),

    run: (id: string) =>
      request<{ task_id: string; status: string; message: string }>(
        `/api/tasks/${id}/run`,
        { method: "POST" },
      ),

    delete: (id: string) =>
      request<void>(`/api/tasks/${id}`, { method: "DELETE" }),

    output: (id: string) =>
      request<{ filename: string; size: number }[]>(`/api/tasks/${id}/output`),

    outputFile: (id: string, filename: string) =>
      request<{ filename: string; content: string }>(
        `/api/tasks/${id}/output/${encodeURIComponent(filename)}`,
      ),

    exportUrl: (id: string, format: string) =>
      `${BASE}/api/tasks/${id}/export?format=${format}`,
  },

  // ── LLM 配置 ──
  settings: {
    listLLM: () => request<LLMConfig[]>("/api/settings/llm"),

    createLLM: (data: LLMConfigCreate) =>
      request<LLMConfig>("/api/settings/llm", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    updateLLM: (id: string, data: LLMConfigUpdate) =>
      request<LLMConfig>(`/api/settings/llm/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    deleteLLM: (id: string) =>
      request<void>(`/api/settings/llm/${id}`, { method: "DELETE" }),

    testLLM: (data: LLMConfigCreate) =>
      request<{ success: boolean; message: string }>("/api/settings/llm/test", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    getGeneral: () => request<GeneralSettings>("/api/settings/general"),

    updateGeneral: (data: GeneralSettings) =>
      request<GeneralSettings>("/api/settings/general", {
        method: "PUT",
        body: JSON.stringify(data),
      }),
  },

  // ── 工具状态 ──
  tools: {
    status: () => request<ToolInfo[]>("/api/tools/status"),

    restart: (name: string) =>
      request<{ success: boolean; message: string }>(
        `/api/tools/${name}/restart`,
        { method: "POST" },
      ),
  },
};

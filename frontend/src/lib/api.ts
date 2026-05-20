import type {
  Task,
  TaskCreate,
  TaskStep,
  ChatMessage,
  LLMConfig,
  LLMConfigCreate,
  LLMConfigUpdate,
  GeneralSettings,
  ToolInfo,
  PromptTemplate,
  PromptTemplateCreate,
  PromptTemplateUpdate,
  CoverageAnalysis,
  CoverageDetail,
  CoverageModuleResult,
  Workspace,
  WorkspaceCreate,
  DeepWikiRepo,
  DeepWikiRepoCreate,
} from "./types";

export const BASE =
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

const HTTP_STATUS_MESSAGES: Record<number, string> = {
  400: "请求参数有误，请检查输入",
  401: "认证失败，请检查 API Key 设置",
  403: "认证失败，请检查 API Key 设置",
  404: "请求的资源不存在",
  409: "操作冲突，请稍后重试",
  429: "请求过于频繁，请稍后重试",
  500: "服务器内部错误，请稍后重试",
  502: "服务暂时不可用，请检查后端服务是否启动",
  503: "服务暂时不可用，请检查后端服务是否启动",
};

function friendlyErrorMessage(status: number, detail: string): string {
  const friendly = HTTP_STATUS_MESSAGES[status] ?? `请求失败 (${status})`;
  return detail ? `${friendly}\n[详情] ${detail}` : friendly;
}

const MAX_RETRIES = 2;
const RETRY_DELAYS = [1000, 2000];

function isRetryable(status: number): boolean {
  return status >= 500 && status <= 599;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    let res: Response;
    try {
      res = await fetch(`${BASE}${path}`, {
        credentials: "include",
        headers: { "Content-Type": "application/json", ...init?.headers },
        ...init,
      });
    } catch {
      lastError = new Error("网络连接失败，请检查后端服务是否运行");
      if (attempt < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt]));
        continue;
      }
      throw lastError;
    }

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      const detail = extractErrorMessage(body);
      lastError = new Error(friendlyErrorMessage(res.status, detail));
      if (isRetryable(res.status) && attempt < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt]));
        continue;
      }
      throw lastError;
    }

    if (res.status === 204) {
      return undefined as T;
    }

    return res.json();
  }

  throw lastError ?? new Error("请求失败");
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

    steps: (id: string) =>
      request<TaskStep[]>(`/api/tasks/${id}/steps`),

    exportUrl: (id: string, format: string) =>
      `${BASE}/api/tasks/${id}/export?format=${format}`,

    chatHistory: (id: string) =>
      request<ChatMessage[]>(`/api/tasks/${id}/chat`),

    chatUrl: (id: string) => `${BASE}/api/tasks/${id}/chat`,
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

  // ── 提示词模板 ──
  prompts: {
    list: () => request<PromptTemplate[]>("/api/prompts"),

    get: (id: string) => request<PromptTemplate>(`/api/prompts/${id}`),

    create: (data: PromptTemplateCreate) =>
      request<PromptTemplate>("/api/prompts", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    update: (id: string, data: PromptTemplateUpdate) =>
      request<PromptTemplate>(`/api/prompts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    delete: (id: string) =>
      request<void>(`/api/prompts/${id}`, { method: "DELETE" }),
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

  // ── 覆盖率分析 ──
  coverage: {
    list: () => request<CoverageAnalysis[]>("/api/coverage/list"),

    get: (id: string) => request<CoverageDetail>(`/api/coverage/${id}`),

    upload: async (files: File[], name?: string): Promise<CoverageAnalysis> => {
      const formData = new FormData();
      for (const f of files) {
        formData.append("files", f);
      }
      if (name) {
        formData.append("name", name);
      }
      const res = await fetch(`${BASE}/api/coverage/upload`, {
        method: "POST",
        credentials: "include",
        body: formData,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        const detail = extractErrorMessage(body);
        throw new Error(friendlyErrorMessage(res.status, detail));
      }
      return res.json();
    },

    analyze: (id: string) =>
      request<{
        analysis_id: string;
        status: string;
        module_results: number;
        results: CoverageModuleResult[];
      }>(`/api/coverage/${id}/analyze`, { method: "POST" }),

    delete: (id: string) =>
      request<void>(`/api/coverage/${id}`, { method: "DELETE" }),
  },

  // ── 工作空间 (V2) ──
  workspaces: {
    list: () => request<Workspace[]>("/api/workspaces"),

    create: (data: WorkspaceCreate) =>
      request<Workspace>("/api/workspaces", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    get: (id: string) => request<Workspace>(`/api/workspaces/${id}`),

    uploadMaterial: async (wsId: string, file: File): Promise<Workspace["materials"][number]> => {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${BASE}/api/workspaces/${wsId}/materials`, {
        method: "POST",
        credentials: "include",
        body: formData,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        const detail = extractErrorMessage(body);
        throw new Error(friendlyErrorMessage(res.status, detail));
      }
      return res.json();
    },

    deleteMaterial: (wsId: string, matId: string) =>
      request<void>(`/api/workspaces/${wsId}/materials/${matId}`, {
        method: "DELETE",
      }),
  },

  // ── DeepWiki (V2) ──
  deepwiki: {
    list: () =>
      request<Omit<DeepWikiRepo, "wiki_data" | "pages">[]>("/api/deepwiki/repos"),

    create: (data: DeepWikiRepoCreate) =>
      request<DeepWikiRepo>("/api/deepwiki/repos", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    get: (id: string) => request<DeepWikiRepo>(`/api/deepwiki/repos/${id}`),

    generate: (id: string) =>
      request<{ status: string; message: string }>(
        `/api/deepwiki/repos/${id}/generate`,
        { method: "POST" },
      ),

    status: (id: string) =>
      request<{ running: boolean; progress: number; error: string | null }>(
        `/api/deepwiki/repos/${id}/status`,
      ),
  },
};

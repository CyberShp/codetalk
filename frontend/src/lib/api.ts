import type {
  Project,
  ProjectCreate,
  ProjectUpdate,
  Repository,
  RepositoryCreate,
  AnalysisTask,
  TaskCreate,
  TaskDetail,
  ToolInfo,
  LLMConfig,
  LLMConfigCreate,
  LLMConfigUpdate,
  FileSlice,
  SyncResult,
  ComponentContract,
  ComponentStatus,
  ComponentConfigResponse,
  ApplyResult,
  RestartResult,
  WikiResponse,
  WikiGenerateResponse,
  WikiStatus,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${body}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export const api = {
  projects: {
    list: () => request<Project[]>("/api/projects"),
    create: (data: ProjectCreate) =>
      request<Project>("/api/projects", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    get: (id: string) => request<Project>(`/api/projects/${id}`),
    update: (id: string, data: ProjectUpdate) =>
      request<Project>(`/api/projects/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    delete: (id: string) =>
      request<void>(`/api/projects/${id}`, { method: "DELETE" }),
    repos: (projectId: string) =>
      request<Repository[]>(`/api/projects/${projectId}/repositories`),
    addRepo: (projectId: string, data: RepositoryCreate) =>
      request<Repository>(`/api/projects/${projectId}/repositories`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },

  repos: {
    sync: (repoId: string) =>
      request<SyncResult>(`/api/repos/${repoId}/sync`, { method: "POST" }),
    delete: (repoId: string) =>
      request<void>(`/api/repos/${repoId}`, { method: "DELETE" }),
    cancelSync: (repoId: string) =>
      request<{ status: string }>(`/api/repos/${repoId}/sync/cancel`, { method: "POST" }),
    search: (repoId: string, query: string) =>
      request<{
        results: { file: string; repo: string; matches: { line_number: number; line_content: string }[] }[];
        query: string;
        repo_name: string;
        total_matches: number;
      }>(`/api/repos/${repoId}/search`, {
        method: "POST",
        body: JSON.stringify({ query }),
      }),
  },

  tasks: {
    list: (params?: { status?: string; repository_id?: string }) => {
      const qs = new URLSearchParams();
      if (params?.status) qs.set("status", params.status);
      if (params?.repository_id) qs.set("repository_id", params.repository_id);
      const q = qs.toString();
      return request<AnalysisTask[]>(`/api/tasks${q ? `?${q}` : ""}`);
    },
    create: (data: TaskCreate) =>
      request<AnalysisTask>("/api/tasks", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    get: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),
    getResults: (id: string) =>
      request<Record<string, unknown>>(`/api/tasks/${id}/results`),
    delete: (id: string) =>
      request<void>(`/api/tasks/${id}`, { method: "DELETE" }),
    cancel: (id: string) =>
      request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  },

  tools: {
    list: () => request<ToolInfo[]>("/api/tools"),
  },

  settings: {
    listLLM: () => request<LLMConfig[]>("/api/settings/llm"),
    saveLLM: (data: LLMConfigCreate) =>
      request<LLMConfig>("/api/settings/llm", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateLLM: (id: string, data: LLMConfigUpdate) =>
      request<LLMConfig>(`/api/settings/llm/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    setDefaultLLM: (id: string) =>
      request<LLMConfig>(`/api/settings/llm/${id}/default`, { method: "PATCH" }),
    testLLM: (id: string) =>
      request<{ success: boolean; message: string }>(`/api/settings/llm/${id}/test`, {
        method: "POST",
      }),
    deleteLLM: (id: string) =>
      request<void>(`/api/settings/llm/${id}`, { method: "DELETE" }),
  },

  gitnexus: {
    getFile: (repo: string, path: string, startLine?: number, endLine?: number) => {
      const qs = new URLSearchParams({ repo, path });
      if (startLine !== undefined) qs.set("start_line", String(startLine));
      if (endLine !== undefined) qs.set("end_line", String(endLine));
      return request<FileSlice>(`/api/gitnexus/file?${qs}`);
    },
  },

  wiki: {
    get: (taskId: string) =>
      request<WikiResponse>(`/api/tasks/${taskId}/wiki`),
    generate: (taskId: string, comprehensive = true, forceRefresh = false) =>
      request<WikiGenerateResponse>(`/api/tasks/${taskId}/wiki/generate`, {
        method: "POST",
        body: JSON.stringify({ comprehensive, force_refresh: forceRefresh }),
      }),
    status: (taskId: string) =>
      request<WikiStatus>(`/api/tasks/${taskId}/wiki/status`),
    deleteCache: (taskId: string) =>
      request<{ status: string }>(`/api/tasks/${taskId}/wiki/cache`, {
        method: "DELETE",
      }),
  },

  chat: {
    stream: (
      taskId: string,
      messages: { role: string; content: string }[],
      signal?: AbortSignal,
    ) =>
      fetch(`${BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, messages }),
        signal,
      }),
  },

  components: {
    contracts: () => request<ComponentContract[]>("/api/components/contracts"),
    list: () => request<ComponentStatus[]>("/api/components"),
    saveConfig: (component: string, domain: string, config: Record<string, string>) =>
      request<ComponentConfigResponse>(`/api/components/${component}/${domain}`, {
        method: "PUT",
        body: JSON.stringify({ config }),
      }),
    apply: (component: string) =>
      request<ApplyResult>(`/api/components/${component}/apply`, { method: "POST" }),
    restart: (component: string) =>
      request<RestartResult>(`/api/components/${component}/restart`, { method: "POST" }),
    applyRestart: (component: string) =>
      request<RestartResult>(`/api/components/${component}/apply-restart`, { method: "POST" }),
    health: (component: string) =>
      request<ComponentStatus>(`/api/components/${component}/health`),
  },
};

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
    deleteLLM: (id: string) =>
      request<void>(`/api/settings/llm/${id}`, { method: "DELETE" }),
  },
};

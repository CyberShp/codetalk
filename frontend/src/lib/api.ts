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
  EvidenceItem,
  AskContextResponse,
  RepoDetail,
  PaginatedAnalyses,
  RepoGraphResponse,
  AnalysisSummary,
  SemgrepScanSummary,
  SemgrepFinding,
  JoernMethodBranch,
  JoernErrorPath,
  JoernBoundaryValue,
  JoernCallContext,
  JoernCalleeImpact,
  TaintPath,
  TestPoint,
  SeverityLevel,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function collapseErrorText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function extractErrorMessage(body: string): string {
  const text = body.trim();
  if (!text) return "Request failed";

  try {
    const parsed = JSON.parse(text) as unknown;
    if (typeof parsed === "string") return collapseErrorText(parsed);
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      const detail = record.detail ?? record.message ?? parsed;
      if (typeof detail === "string") return collapseErrorText(detail);
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) => {
            if (typeof item === "string") return item;
            if (item && typeof item === "object") {
              const row = item as Record<string, unknown>;
              return typeof row.msg === "string"
                ? row.msg
                : typeof row.message === "string"
                  ? row.message
                  : JSON.stringify(row);
            }
            return String(item);
          })
          .filter(Boolean);
        if (messages.length > 0) return collapseErrorText(messages.join("; "));
      }
      return collapseErrorText(JSON.stringify(detail));
    }
  } catch {
    // Plain-text error body.
  }

  const firstLine = text.split("\n").map((line) => line.trim()).find(Boolean);
  return collapseErrorText(firstLine ?? text);
}

export interface ChatSession {
  id: string;
  repo_id: string;
  title: string | null;
  messages: { role: string; content: string }[];
  created_at: string | null;
  updated_at: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
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

    // ── Repo-centric endpoints ────────────────────────────────────────────
    get: (repoId: string) =>
      request<RepoDetail>(`/api/repos/${repoId}`),

    analyses: (repoId: string, page = 1, pageSize = 10) =>
      request<PaginatedAnalyses>(
        `/api/repos/${repoId}/analyses?page=${page}&page_size=${pageSize}`
      ),

    wiki: {
      get: (repoId: string) =>
        request<WikiResponse>(`/api/repos/${repoId}/wiki`),
      generate: (repoId: string, comprehensive = true, forceRefresh = false) =>
        request<WikiGenerateResponse>(`/api/repos/${repoId}/wiki/generate`, {
          method: "POST",
          body: JSON.stringify({ comprehensive, force_refresh: forceRefresh }),
        }),
      status: (repoId: string) =>
        request<WikiStatus>(`/api/repos/${repoId}/wiki/status`),
      deleteCache: (repoId: string) =>
        request<{ status: string }>(`/api/repos/${repoId}/wiki/cache`, {
          method: "DELETE",
        }),
      regeneratePage: (repoId: string, pageId: string, pageTitle: string, filePaths: string[]) =>
        request<{ status: string; content: string }>(`/api/repos/${repoId}/wiki/regenerate-page`, {
          method: "POST",
          body: JSON.stringify({ page_id: pageId, page_title: pageTitle, file_paths: filePaths }),
        }),
    },

    graph: {
      get: (repoId: string) =>
        request<RepoGraphResponse>(`/api/repos/${repoId}/graph`),
    },

    analysis: {
      summary: (repoId: string) =>
        request<AnalysisSummary>(`/api/repos/${repoId}/analysis/summary`),

      joern: {
        rebuild: (repoId: string) =>
          request<{ status: string; repo_id: string }>(`/api/repos/${repoId}/analysis/joern/rebuild`, {
            method: "POST",
          }),
        query: (repoId: string, cpgql: string) =>
          request<{ result: unknown }>(`/api/repos/${repoId}/analysis/joern/query`, {
            method: "POST",
            body: JSON.stringify({ query: cpgql }),
          }),
        methods: (repoId: string) =>
          request<{ methods: unknown[] }>(`/api/repos/${repoId}/analysis/joern/methods`),
        /** Batch: branches + errors + boundaries + cross-function context in ONE CPG import. */
        allForMethod: (repoId: string, methodName: string) =>
          request<{
            method: string;
            branches: JoernMethodBranch[];
            errors: JoernErrorPath[];
            boundaries: JoernBoundaryValue[];
            callContext: JoernCallContext[];
            calleeImpact: JoernCalleeImpact[];
          }>(
            `/api/repos/${repoId}/analysis/joern/method/${encodeURIComponent(methodName)}/all`
          ),
        branches: (repoId: string, methodName: string) =>
          request<{ branches: JoernMethodBranch[] }>(
            `/api/repos/${repoId}/analysis/joern/method/${encodeURIComponent(methodName)}/branches`
          ),
        errors: (repoId: string, methodName: string) =>
          request<{ errors: JoernErrorPath[] }>(
            `/api/repos/${repoId}/analysis/joern/method/${encodeURIComponent(methodName)}/errors`
          ),
        boundaries: (repoId: string, methodName: string) =>
          request<{ boundaries: JoernBoundaryValue[] }>(
            `/api/repos/${repoId}/analysis/joern/method/${encodeURIComponent(methodName)}/boundaries`
          ),
        cfg: (repoId: string, methodName: string) =>
          request<{ method: string; dot: string }>(
            `/api/repos/${repoId}/analysis/joern/method/${encodeURIComponent(methodName)}/cfg`
          ),
        taint: (repoId: string, source: string, sink: string) =>
          request<{ paths: TaintPath[] }>(`/api/repos/${repoId}/analysis/joern/taint`, {
            method: "POST",
            body: JSON.stringify({ source, sink }),
          }),
      },

      semgrep: {
        findings: (
          repoId: string,
          params?: { severity?: SeverityLevel; category?: string; page?: number }
        ) => {
          const qs = new URLSearchParams();
          if (params?.severity) qs.set("severity", params.severity);
          if (params?.category) qs.set("category", params.category);
          if (params?.page) qs.set("page", String(params.page));
          const q = qs.toString();
          return request<{ findings: SemgrepFinding[]; total: number; page: number }>(
            `/api/repos/${repoId}/analysis/semgrep/findings${q ? `?${q}` : ""}`
          );
        },
        scan: (repoId: string) =>
          request<{ status: string; summary: SemgrepScanSummary | null; findings: SemgrepFinding[] | null }>(
            `/api/repos/${repoId}/analysis/semgrep/scan`,
            { method: "POST" }
          ),
        scanIncremental: (repoId: string, baselineCommit: string) =>
          request<{ findings: SemgrepFinding[] }>(
            `/api/repos/${repoId}/analysis/semgrep/scan/incremental`,
            {
              method: "POST",
              body: JSON.stringify({ baseline_commit: baselineCommit }),
            }
          ),
      },

      testPoints: {
        generate: (
          repoId: string,
          target?: string,
          perspective = "black_box"
        ) =>
          request<{ test_points: TestPoint[] }>(
            `/api/repos/${repoId}/analysis/test-points`,
            {
              method: "POST",
              body: JSON.stringify({ target, perspective }),
            }
          ),
      },
    },

    chat: {
      stream: (
        repoId: string,
        messages: { role: string; content: string }[],
        options?: {
          filePath?: string;
          deepResearch?: boolean;
          includedFiles?: string[];
          excludedDirs?: string[];
        },
        signal?: AbortSignal,
      ) =>
        fetch(`${BASE}/api/repos/${repoId}/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            repo_id: repoId,
            messages,
            file_path: options?.filePath,
            deep_research: options?.deepResearch ?? false,
            included_files: options?.includedFiles,
            excluded_dirs: options?.excludedDirs,
          }),
          signal,
        }),

      sessions: {
        list: (repoId: string) =>
          request<ChatSession[]>(`/api/repos/${repoId}/chat/sessions`),

        get: (repoId: string, sessionId: string) =>
          request<ChatSession>(`/api/repos/${repoId}/chat/sessions/${sessionId}`),

        create: (repoId: string, body: { title?: string; messages?: { role: string; content: string }[] }) =>
          request<ChatSession>(`/api/repos/${repoId}/chat/sessions`, {
            method: "POST",
            body: JSON.stringify(body),
          }),

        update: (repoId: string, sessionId: string, body: { title?: string; messages?: { role: string; content: string }[] }) =>
          request<ChatSession>(`/api/repos/${repoId}/chat/sessions/${sessionId}`, {
            method: "PUT",
            body: JSON.stringify(body),
          }),

        delete: (repoId: string, sessionId: string) =>
          request<void>(`/api/repos/${repoId}/chat/sessions/${sessionId}`, {
            method: "DELETE",
          }),
      },
    },
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
    getFile: (id: string, path: string, start?: number, end?: number) => {
      const qs = new URLSearchParams({ path });
      if (start !== undefined) qs.set("start", String(start));
      if (end !== undefined) qs.set("end", String(end));
      return request<FileSlice>(`/api/tasks/${id}/file?${qs}`);
    },
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
    deepwikiModels: () =>
      request<Record<string, unknown>>(`/api/settings/deepwiki/models`),
  },

  gitnexus: {
    getFile: (repo: string, path: string, startLine?: number, endLine?: number) => {
      const qs = new URLSearchParams({ repo, path });
      if (startLine !== undefined) qs.set("start_line", String(startLine));
      if (endLine !== undefined) qs.set("end_line", String(endLine));
      return request<FileSlice>(`/api/gitnexus/file?${qs}`);
    },
    search: async (
      query: string,
      repo?: string,
      mode: "hybrid" | "bm25" | "semantic" = "hybrid",
      limit = 10,
    ) => {
      const raw = await request<{
        results: Array<{
          nodeId?: string;
          id?: string;
          name: string;
          label: string;
          filePath: string;
          score: number;
          connections?: { outgoing?: unknown[]; incoming?: unknown[] } | number;
          cluster?: string;
          processes?: string[];
        }>;
      }>("/api/gitnexus/search", {
        method: "POST",
        body: JSON.stringify({ query, repo, mode, limit, enrich: true }),
      });
      return {
        results: raw.results.map((r) => ({
          ...r,
          id: r.nodeId || r.id || r.name,
          connections:
            r.connections != null && typeof r.connections === "object" && !Array.isArray(r.connections)
              ? ((r.connections.outgoing?.length ?? 0) + (r.connections.incoming?.length ?? 0))
              : r.connections as number | undefined,
        })),
      };
    },

    query: (cypher: string, repo?: string) =>
      request<{ results: unknown[] }>("/api/gitnexus/query", {
        method: "POST",
        body: JSON.stringify({ cypher, repo }),
      }),

    processes: (repo?: string) => {
      const qs = new URLSearchParams();
      if (repo) qs.set("repo", repo);
      const q = qs.toString();
      return request<{ processes: unknown[] }>(`/api/gitnexus/processes${q ? `?${q}` : ""}`);
    },

    process: (name: string, repo?: string) => {
      const qs = new URLSearchParams({ name });
      if (repo) qs.set("repo", repo);
      return request<{ name: string; steps: unknown[]; [key: string]: unknown }>(
        `/api/gitnexus/process?${qs}`
      );
    },

    clusters: (repo?: string) => {
      const qs = new URLSearchParams();
      if (repo) qs.set("repo", repo);
      const q = qs.toString();
      return request<{ clusters: unknown[] }>(`/api/gitnexus/clusters${q ? `?${q}` : ""}`);
    },

    cluster: (name: string, repo?: string) => {
      const qs = new URLSearchParams({ name });
      if (repo) qs.set("repo", repo);
      return request<{ name: string; members: unknown[]; cohesion?: number; [key: string]: unknown }>(
        `/api/gitnexus/cluster?${qs}`
      );
    },

    impact: (
      target: string,
      direction: "upstream" | "downstream" | "both" = "both",
      depth = 3,
      repo?: string,
    ) =>
      request<{
        target: string;
        depth: number;
        upstream?: Array<{ nodes: unknown[]; rels: unknown[] }>;
        downstream?: Array<{ nodes: unknown[]; rels: unknown[] }>;
      }>("/api/gitnexus/impact", {
        method: "POST",
        body: JSON.stringify({ target, direction, depth, repo }),
      }),

    grep: (pattern: string, repo?: string, glob?: string) => {
      const qs = new URLSearchParams({ pattern });
      if (repo) qs.set("repo", repo);
      if (glob) qs.set("glob", glob);
      return request<{
        matches: Array<{
          file: string;
          line: number;
          content: string;
          context?: string[];
        }>;
      }>(`/api/gitnexus/grep?${qs}`);
    },

    repos: (repo?: string) => {
      const qs = new URLSearchParams();
      if (repo) qs.set("repo", repo);
      const q = qs.toString();
      return request<{ repos: unknown[] }>(`/api/gitnexus/repos${q ? `?${q}` : ""}`);
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
      evidence?: EvidenceItem[],
    ) =>
      fetch(`${BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, messages, evidence: evidence ?? [] }),
        signal,
      }),
    askContext: (taskId: string, query: string, signal?: AbortSignal) =>
      request<AskContextResponse>("/api/chat/ask/context", {
        method: "POST",
        body: JSON.stringify({ task_id: taskId, query }),
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

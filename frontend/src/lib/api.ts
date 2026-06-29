import type {
  Task,
  TaskCreate,
  TaskStep,
  ChatMessage,
  LLMConfig,
  LLMConfigCreate,
  LLMConfigUpdate,
  GeneralSettings,
  AgentRuntime,
  AgentRuntimeCreate,
  AgentProviderSettings,
  ToolInfo,
  PromptTemplate,
  PromptTemplateCreate,
  PromptTemplateUpdate,
  CoverageAnalysis,
  CoverageDetail,
  CoverageModuleResult,
  Workspace,
  WorkspaceCreate,
  WorkspaceMaterial,
  EmbeddingStatus,
  AnalysisPlan,
  ScopePreview,
  ExternalAgentStartupProbeResult,
  WorkbenchDeploymentProbeResult,
  WorkflowDefinition,
  WorkflowPreset,
  SemanticCase,
  SemanticCaseImportResult,
  EvidenceMemoryItem,
  EvidenceSourceSlice,
  AgentRunRecord,
  ArtifactValidationResult,
  AgentRunExecutionResult,
  MaterializeEvidenceResult,
  MaterializeWorkflowOutputsResult,
  PreparedWorkbenchTaskRun,
  TaskRerunExecutionResult,
  TaskRerunHistory,
  TaskRerunPlan,
  TaskRerunPlanValidation,
  WorkbenchTaskRunRunResult,
  WorkflowExecutionResult,
  WorkbenchAcceptanceAudit,
  WorkbenchProviderCapabilitiesMatrix,
  WorkbenchSystemAudit,
  WorkbenchWorkflowCapabilities,
  WorkbenchCoreWorkflowReadiness,
  WorkbenchInputUploadResult,
  WorkbenchProviderTaskProbeResult,
  WorkbenchSmokeE2EResult,
  WorkbenchTaskArtifactContent,
  WorkbenchTaskArtifactManifest,
  AIConversation,
  AIConversationRun,
  AIMessage,
  AIThreadScope,
  AIContextReference,
} from "./types";

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");

export const BASE =
  CONFIGURED_API_BASE ??
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:3004`
    : "http://localhost:3004");

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

async function requestForm<T>(path: string, body: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    credentials: "include",
    body,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(friendlyErrorMessage(res.status, extractErrorMessage(text)));
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

    steps: (id: string) =>
      request<TaskStep[]>(`/api/tasks/${id}/steps`),

    exportUrl: (id: string, format: string) =>
      `${BASE}/api/tasks/${id}/export?format=${format}`,

    chatHistory: (id: string) =>
      request<ChatMessage[]>(`/api/tasks/${id}/chat`),

    chatUrl: (id: string) => `${BASE}/api/tasks/${id}/chat`,

    cancel: (id: string) =>
      request<{ task_id: string; status: string }>(`/api/tasks/${id}/cancel`, {
        method: "POST",
      }),
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

    getAgentProviders: () =>
      request<AgentProviderSettings>("/api/settings/agent-providers"),

    updateAgentProviders: (data: AgentProviderSettings) =>
      request<AgentProviderSettings>("/api/settings/agent-providers", {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    listAgentRuntimes: (params?: { enabled?: boolean }) => {
      const query = new URLSearchParams({
        ...(params?.enabled !== undefined ? { enabled: String(params.enabled) } : {}),
      });
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<{ items: AgentRuntime[] }>(`/api/settings/agent-runtimes${suffix}`);
    },

    createAgentRuntime: (data: AgentRuntimeCreate) =>
      request<AgentRuntime>("/api/settings/agent-runtimes", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    updateAgentRuntime: (id: string, data: Partial<AgentRuntimeCreate>) =>
      request<AgentRuntime>(`/api/settings/agent-runtimes/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),

    deleteAgentRuntime: (id: string) =>
      request<void>(`/api/settings/agent-runtimes/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),

    probeAgentRuntime: (id: string) =>
      request<{ success: boolean; message: string }>(
        `/api/settings/agent-runtimes/${encodeURIComponent(id)}/probe`,
        { method: "POST" },
      ),
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

  // ── 工具进程 API（Workbench 探测使用） ──
  tools: {
    status: () => request<ToolInfo[]>("/api/tools/procs"),

    start: (name: string) =>
      request<{ success: boolean; message: string }>(
        `/api/tools/${name}/start`,
        { method: "POST" },
      ),

    stop: (name: string) =>
      request<{ success: boolean; message: string }>(
        `/api/tools/${name}/stop`,
        { method: "POST" },
      ),

    restart: (name: string) =>
      request<{ success: boolean; message: string }>(
        `/api/tools/${name}/restart`,
        { method: "POST" },
      ),

    startupProbe: (name: string, repoPath?: string) => {
      const suffix = repoPath
        ? `?${new URLSearchParams({ repo_path: repoPath }).toString()}`
        : "";
      return request<ExternalAgentStartupProbeResult>(
        `/api/tools/${name}/startup-probe${suffix}`,
        { method: "POST" },
      );
    },
  },

  // ── 覆盖率分析 ──
  coverage: {
    list: () => request<CoverageAnalysis[]>("/api/coverage/list"),

    get: (id: string) => request<CoverageDetail>(`/api/coverage/${id}`),

    upload: async (
      files: File[],
      name?: string,
      workspaceId?: string,
    ): Promise<CoverageAnalysis> => {
      const formData = new FormData();
      for (const f of files) {
        formData.append("files", f);
      }
      if (name) {
        formData.append("name", name);
      }
      if (workspaceId) {
        formData.append("workspace_id", workspaceId);
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

    uploadMaterial: async (wsId: string, filePath: string): Promise<Workspace["materials"][number]> => {
      const res = await fetch(`${BASE}/api/workspaces/${wsId}/materials`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        const detail = extractErrorMessage(body);
        throw new Error(friendlyErrorMessage(res.status, detail));
      }
      return res.json();
    },

    toggleMaterial: (wsId: string, matId: string, isActive: boolean) =>
      request<WorkspaceMaterial>(`/api/workspaces/${wsId}/materials/${matId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: isActive }),
      }),

    deleteMaterial: (wsId: string, matId: string) =>
      request<void>(`/api/workspaces/${wsId}/materials/${matId}`, {
        method: "DELETE",
      }),

    embeddingStatus: (wsId: string) =>
      request<EmbeddingStatus>(`/api/workspaces/${wsId}/materials/embedding-status`),

    triggerEmbedding: (wsId: string) =>
      request<{ status: string }>(`/api/workspaces/${wsId}/materials/embed`, {
        method: "POST",
      }),

    indexStatus: (id: string) =>
      request<{ indexed: number; index_job: string | null; index_progress: number }>(
        `/api/workspaces/${id}/index-status`,
      ),

    reindex: (id: string) =>
      request<{ status: string; message: string }>(
        `/api/workspaces/${id}/reindex`,
        { method: "POST" },
      ),

    analyze: (
      id: string,
      body?: {
        plan?: AnalysisPlan;
        scope_preview?: ScopePreview | null;
        include_coverage_gaps?: boolean;
        coverage_analysis_ids?: string[] | null;
      },
    ) =>
      request<{
        status: string;
        message: string;
        analysis_units?: number | null;
        evidence_cards?: number | null;
        plan_persisted?: boolean;
        preview_persisted?: boolean;
      }>(`/api/workspaces/${id}/analyze`, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      }),

    defaultAnalysisPlan: (id: string) =>
      request<AnalysisPlan>(`/api/workspaces/${id}/analysis/default-plan`),

    previewScope: (id: string, plan: AnalysisPlan) =>
      request<ScopePreview>(`/api/workspaces/${id}/analysis/preview`, {
        method: "POST",
        body: JSON.stringify({ plan }),
      }),

    analyzeStatus: (id: string) =>
      request<{ analyze_status: string | null; analyze_progress: number; task_id: string | null }>(
        `/api/workspaces/${id}/analyze-status`,
      ),

    versions: (wsId: string) =>
      request<import("./types").WorkspaceVersion[]>(`/api/workspaces/${wsId}/versions`),

    report: (wsId: string, reportId: string) =>
      request<import("./types").WorkspaceReport>(
        `/api/workspaces/${wsId}/reports/${reportId}`,
      ),

    modules: (wsId: string) =>
      request<import("./types").WorkspaceModule[]>(`/api/workspaces/${wsId}/modules`),

    chatHistory: (wsId: string, limit = 50) =>
      request<import("./types").WorkspaceChatMessage[]>(
        `/api/workspaces/${wsId}/chat/history?limit=${limit}&_=${Date.now()}`,
        { cache: "no-store" },
      ),

    chatStream: (wsId: string, message: string, mode: import("./types").ChatMode, module?: string, signal?: AbortSignal): Promise<Response> =>
      fetch(`${BASE}/api/workspaces/${wsId}/chat/stream`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, mode, ...(module ? { module } : {}) }),
        signal,
      }),

    exportUrl: (wsId: string, format: "md" | "docx" | "xml", taskId?: string | null) => {
      const params = new URLSearchParams({ format });
      if (taskId) params.set("task_id", taskId);
      return `${BASE}/api/workspaces/${wsId}/export?${params.toString()}`;
    },

    chatExportUrl: (wsId: string) =>
      `${BASE}/api/workspaces/${wsId}/chat/export`,
  },

  aiConversations: {
    list: (params?: {
      scope_type?: AIThreadScope;
      scope_id?: string;
      workspace_id?: string;
      memory_namespace?: string;
      status?: string;
      limit?: number;
    }) => {
      const query = new URLSearchParams({
        ...(params?.scope_type ? { scope_type: params.scope_type } : {}),
        ...(params?.scope_id ? { scope_id: params.scope_id } : {}),
        ...(params?.workspace_id ? { workspace_id: params.workspace_id } : {}),
        ...(params?.memory_namespace ? { memory_namespace: params.memory_namespace } : {}),
        ...(params?.status ? { status: params.status } : {}),
        ...(params?.limit ? { limit: String(params.limit) } : {}),
      });
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<{ items: AIConversation[] }>(`/api/ai/conversations${suffix}`);
    },

    create: (data: {
      scope_type: AIThreadScope;
      scope_id: string;
      workspace_id?: string;
      memory_namespace?: string;
      runtime_type?: "builtin_llm" | "agent_runtime";
      agent_runtime_id?: string | null;
      title: string;
      initial_context?: Record<string, unknown>;
    }) =>
      request<AIConversation>("/api/ai/conversations", {
        method: "POST",
        body: JSON.stringify(data),
      }),

    createForScope: async (data: {
      scope_type: AIThreadScope;
      scope_id: string;
      workspace_id?: string;
      memory_namespace?: string;
      runtime_type?: "builtin_llm" | "agent_runtime";
      agent_runtime_id?: string | null;
      title: string;
      initial_context?: Record<string, unknown>;
    }) => {
      const existing = await api.aiConversations.list({
        scope_type: data.scope_type,
        scope_id: data.scope_id,
        limit: 1,
      });
      return existing.items[0] ?? api.aiConversations.create(data);
    },

    update: (id: string, data: { runtime_type: "builtin_llm" | "agent_runtime"; agent_runtime_id?: string | null }) =>
      request<AIConversation>(`/api/ai/conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),

    get: (id: string) =>
      request<AIConversation>(`/api/ai/conversations/${encodeURIComponent(id)}`),

    messages: (id: string) =>
      request<{ items: AIMessage[] }>(
        `/api/ai/conversations/${encodeURIComponent(id)}/messages`,
        { cache: "no-store" },
      ),

    send: (id: string, content: string) =>
      request<{
        message: AIMessage;
        run: AIConversationRun;
        references: AIContextReference[];
      }>(`/api/ai/conversations/${encodeURIComponent(id)}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      }),

    stream: (id: string, cursor = 0, signal?: AbortSignal): Promise<Response> =>
      fetch(`${BASE}/api/ai/conversations/${encodeURIComponent(id)}/stream?cursor=${cursor}`, {
        credentials: "include",
        signal,
      }),

    cancel: (id: string) =>
      request<{ run: AIConversationRun | null }>(
        `/api/ai/conversations/${encodeURIComponent(id)}/cancel`,
        { method: "POST" },
      ),
  },

  workbench: {
    providerCapabilities: () =>
      request<WorkbenchProviderCapabilitiesMatrix>(
        "/api/workbench/provider-capabilities",
      ),

    systemAudit: () =>
      request<WorkbenchSystemAudit>("/api/workbench/system-audit"),

    workflowCapabilities: () =>
      request<WorkbenchWorkflowCapabilities>("/api/workbench/workflow-capabilities"),

    coreWorkflowReadiness: () =>
      request<WorkbenchCoreWorkflowReadiness>(
        "/api/workbench/core-workflow-readiness",
      ),

    deploymentProbe: (
      repoPath?: string,
      providers?: string[],
      taskContractProbe = false,
      timeoutSec = 30,
    ) =>
      request<WorkbenchDeploymentProbeResult>("/api/workbench/deployment-probe", {
        method: "POST",
        body: JSON.stringify({
          repo_path: repoPath ?? "",
          providers: providers ?? [],
          task_contract_probe: taskContractProbe,
          timeout_sec: timeoutSec,
        }),
      }),

    smokeE2E: (repoPath?: string, timeoutSec = 30) =>
      request<WorkbenchSmokeE2EResult>("/api/workbench/task-runs/smoke-e2e", {
        method: "POST",
        body: JSON.stringify({
          repo_path: repoPath ?? "",
          timeout_sec: timeoutSec,
        }),
      }),

    providerTaskProbe: (provider: string, repoPath?: string, timeoutSec = 30) =>
      request<WorkbenchProviderTaskProbeResult>("/api/workbench/provider-task-probe", {
        method: "POST",
        body: JSON.stringify({
          provider,
          repo_path: repoPath ?? "",
          timeout_sec: timeoutSec,
        }),
      }),

    uploadInputFile: (file: File, inputId: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("input_id", inputId);
      return requestForm<WorkbenchInputUploadResult>(
        "/api/workbench/input-files/upload",
        form,
      );
    },

    workflows: {
      presets: () =>
        request<{ items: WorkflowPreset[] }>("/api/workbench/workflow-presets"),

      installPreset: (id: string) =>
        request<WorkflowDefinition>(
          `/api/workbench/workflow-presets/${encodeURIComponent(id)}/install`,
          { method: "POST" },
        ),

      list: () => request<WorkflowDefinition[]>("/api/workbench/workflows"),

      auditDraft: (data: WorkflowDefinition | Record<string, unknown>) =>
        request<import("./types").WorkflowDraftServerAudit>(
          "/api/workbench/workflows/audit-draft",
          {
            method: "POST",
            body: JSON.stringify(data),
          },
        ),

      create: (data: WorkflowDefinition | Record<string, unknown>) =>
        request<WorkflowDefinition>("/api/workbench/workflows", {
          method: "POST",
          body: JSON.stringify(data),
        }),

      get: (id: string) =>
        request<WorkflowDefinition>(`/api/workbench/workflows/${encodeURIComponent(id)}`),

      snapshot: (id: string) =>
        request<Record<string, unknown>>(
          `/api/workbench/workflows/${encodeURIComponent(id)}/snapshot`,
        ),
    },

    semanticCases: {
      create: (data: Record<string, unknown>) =>
        request<{ semantic_id: string; case_id: string }>(
          "/api/workbench/semantic-cases",
          {
            method: "POST",
            body: JSON.stringify(data),
          },
        ),

      importMany: (data: unknown) =>
        request<SemanticCaseImportResult>(
          "/api/workbench/semantic-cases/import",
          {
            method: "POST",
            body: JSON.stringify(data),
          },
        ),

      importFile: (file: File, defaults?: Record<string, unknown>) => {
        const body = new FormData();
        body.append("file", file);
        body.append("defaults_json", JSON.stringify(defaults ?? {}));
        return requestForm<SemanticCaseImportResult>(
          "/api/workbench/semantic-cases/import-file",
          body,
        );
      },

      search: (params: {
        q: string;
        module?: string;
        test_level?: string;
        limit?: number;
      }) => {
        const query = new URLSearchParams({
          q: params.q,
          ...(params.module ? { module: params.module } : {}),
          ...(params.test_level ? { test_level: params.test_level } : {}),
          ...(params.limit ? { limit: String(params.limit) } : {}),
        });
        return request<{ items: SemanticCase[] }>(
          `/api/workbench/semantic-cases/search?${query.toString()}`,
        );
      },
    },

    memory: {
      createRun: (data: {
        workspace_id: string;
        repo_path: string;
        object_text: string;
        workflow_id: string;
        status?: string;
        run_id?: string;
      }) =>
        request<{ run_id: string }>("/api/workbench/memory/runs", {
          method: "POST",
          body: JSON.stringify(data),
        }),

      createEvidence: (data: Record<string, unknown>) =>
        request<{ evidence_id: string }>("/api/workbench/memory/evidence", {
          method: "POST",
          body: JSON.stringify(data),
        }),

      search: (params: { q: string; workspace_id?: string; limit?: number }) => {
        const query = new URLSearchParams({
          q: params.q,
          ...(params.workspace_id ? { workspace_id: params.workspace_id } : {}),
          ...(params.limit ? { limit: String(params.limit) } : {}),
        });
        return request<{ items: EvidenceMemoryItem[] }>(
          `/api/workbench/memory/search?${query.toString()}`,
        );
      },

      sourceSlices: (evidenceId: string) =>
        request<{ items: EvidenceSourceSlice[] }>(
          `/api/workbench/memory/evidence/${encodeURIComponent(evidenceId)}/source-slices`,
        ),

      recent: (params?: { workspace_id?: string; limit?: number }) => {
        const query = new URLSearchParams({
          ...(params?.workspace_id ? { workspace_id: params.workspace_id } : {}),
          ...(params?.limit ? { limit: String(params.limit) } : {}),
        });
        const suffix = query.toString() ? `?${query.toString()}` : "";
        return request<{ items: Array<Record<string, unknown>> }>(
          `/api/workbench/memory/recent${suffix}`,
        );
      },
    },

    agentRuns: {
      create: (data: {
        provider: string;
        command: string[];
        cwd: string;
        workflow_snapshot?: Record<string, unknown>;
        task_bundle?: Record<string, unknown>;
        mcp_profile?: string;
      }) =>
        request<AgentRunRecord>("/api/workbench/agent-runs", {
          method: "POST",
          body: JSON.stringify(data),
        }),

      recordRawOutput: (runId: string, data: { stdout?: string; stderr?: string }) =>
        request<{ ok: boolean }>(
          `/api/workbench/agent-runs/${encodeURIComponent(runId)}/raw-output`,
          {
            method: "POST",
            body: JSON.stringify(data),
          },
        ),

      execute: (runId: string, timeoutSec = 90) =>
        request<AgentRunExecutionResult>(
          `/api/workbench/agent-runs/${encodeURIComponent(runId)}/execute`,
          {
            method: "POST",
            body: JSON.stringify({ timeout_sec: timeoutSec }),
          },
        ),

      validateMrArtifacts: (runId: string, requiredArtifacts: string[]) =>
        request<ArtifactValidationResult>(
          `/api/workbench/agent-runs/${encodeURIComponent(runId)}/validate-mr-artifacts`,
          {
            method: "POST",
            body: JSON.stringify({ required_artifacts: requiredArtifacts }),
          },
        ),
    },

    taskRuns: {
      list: (params?: { workspace_id?: string; limit?: number }) => {
        const query = new URLSearchParams({
          ...(params?.workspace_id ? { workspace_id: params.workspace_id } : {}),
          ...(params?.limit ? { limit: String(params.limit) } : {}),
        });
        const suffix = query.toString() ? `?${query.toString()}` : "";
        return request<{ items: PreparedWorkbenchTaskRun[] }>(
          `/api/workbench/task-runs${suffix}`,
        );
      },

      get: (taskRunId: string) =>
        request<PreparedWorkbenchTaskRun>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}`,
        ),

      rerunPlan: (taskRunId: string) =>
        request<TaskRerunPlan>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/rerun-plan`,
        ),

      rerunPlanValidation: (taskRunId: string) =>
        request<TaskRerunPlanValidation>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/rerun-plan/validation`,
        ),

      rerunHistory: (taskRunId: string) =>
        request<TaskRerunHistory>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/rerun-plan/history`,
        ),

      executeRerunPlan: (taskRunId: string, timeoutSec = 90, stopOnError = true) =>
        request<TaskRerunExecutionResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/rerun-plan/execute`,
          {
            method: "POST",
            body: JSON.stringify({
              timeout_sec: timeoutSec,
              stop_on_error: stopOnError,
            }),
          },
        ),

      acceptanceAudit: (taskRunId: string) =>
        request<WorkbenchAcceptanceAudit>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/acceptance-audit`,
          {
            method: "POST",
          },
        ),

      artifacts: (taskRunId: string) =>
        request<WorkbenchTaskArtifactManifest>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/artifacts`,
        ),

      artifactContent: (taskRunId: string, artifactPath: string) => {
        const encodedPath = artifactPath
          .split("/")
          .map((part) => encodeURIComponent(part))
          .join("/");
        return request<WorkbenchTaskArtifactContent>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/artifacts/content/${encodedPath}`,
        );
      },

      prepare: (data: {
        workflow_id: string;
        workspace_id: string;
        repo_path: string;
        inputs?: Record<string, unknown>;
        provider_override?: string | null;
      }) =>
        request<PreparedWorkbenchTaskRun>("/api/workbench/task-runs/prepare", {
          method: "POST",
          body: JSON.stringify(data),
        }),

      run: (
        data: {
          workflow_id: string;
          workspace_id: string;
          repo_path: string;
          inputs?: Record<string, unknown>;
          provider_override?: string | null;
        },
        timeoutSec = 90,
        stopOnError = true,
      ) =>
        request<WorkbenchTaskRunRunResult>("/api/workbench/task-runs/run", {
          method: "POST",
          body: JSON.stringify({
            ...data,
            timeout_sec: timeoutSec,
            stop_on_error: stopOnError,
          }),
        }),

      execute: (taskRunId: string, timeoutSec = 90, stopOnError = true) =>
        request<WorkflowExecutionResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/execute`,
          {
            method: "POST",
            body: JSON.stringify({
              timeout_sec: timeoutSec,
              stop_on_error: stopOnError,
            }),
          },
        ),

      materializeOutputs: (taskRunId: string) =>
        request<MaterializeWorkflowOutputsResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/materialize-outputs`,
          {
            method: "POST",
          },
        ),

      importSemanticOutputs: (
        taskRunId: string,
        data: { output_ids?: string[]; defaults?: Record<string, unknown> } = {},
      ) =>
        request<SemanticCaseImportResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/semantic-cases/import-outputs`,
          {
            method: "POST",
            body: JSON.stringify(data),
          },
        ),

      executeAgentRun: (taskRunId: string, stepId: string, timeoutSec = 90) =>
        request<AgentRunExecutionResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/agent-runs/${encodeURIComponent(stepId)}/execute`,
          {
            method: "POST",
            body: JSON.stringify({ timeout_sec: timeoutSec }),
          },
        ),

      validateMrArtifacts: (
        taskRunId: string,
        stepId: string,
        requiredArtifacts: string[],
      ) =>
        request<ArtifactValidationResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/agent-runs/${encodeURIComponent(stepId)}/validate-mr-artifacts`,
          {
            method: "POST",
            body: JSON.stringify({ required_artifacts: requiredArtifacts }),
          },
        ),

      materializeEvidence: (
        taskRunId: string,
        stepId: string,
        requiredArtifacts: string[],
        objectText = "",
      ) =>
        request<MaterializeEvidenceResult>(
          `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/agent-runs/${encodeURIComponent(stepId)}/materialize-evidence`,
          {
            method: "POST",
            body: JSON.stringify({
              required_artifacts: requiredArtifacts,
              object_text: objectText,
            }),
          },
        ),
    },
  },
};

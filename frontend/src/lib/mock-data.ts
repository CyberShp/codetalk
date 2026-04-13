import type {
  Project,
  Repository,
  AnalysisTask,
  TaskDetail,
  ToolInfo,
  LLMConfig,
  LogEntry,
} from "./types";

export const mockProjects: Project[] = [
  {
    id: "p1",
    name: "linux-kernel",
    description: "Linux kernel source tree — C/C++ analysis target",
    created_at: "2025-12-01T08:00:00Z",
    updated_at: "2025-12-10T14:30:00Z",
    repo_count: 1,
  },
  {
    id: "p2",
    name: "llvm-project",
    description: "LLVM compiler infrastructure",
    created_at: "2025-12-05T10:00:00Z",
    updated_at: "2025-12-12T09:00:00Z",
    repo_count: 2,
  },
  {
    id: "p3",
    name: "redis",
    description: "In-memory data structure store",
    created_at: "2025-12-08T16:00:00Z",
    updated_at: "2025-12-08T16:00:00Z",
    repo_count: 1,
  },
];

export const mockRepositories: Repository[] = [
  {
    id: "r1",
    project_id: "p1",
    name: "linux",
    source_type: "git_url",
    source_uri: "https://github.com/torvalds/linux.git",
    local_path: "/data/repos/linux",
    branch: "master",
    last_indexed_at: "2025-12-10T14:30:00Z",
    created_at: "2025-12-01T08:00:00Z",
  },
  {
    id: "r2",
    project_id: "p2",
    name: "llvm-project",
    source_type: "git_url",
    source_uri: "https://github.com/llvm/llvm-project.git",
    local_path: "/data/repos/llvm-project",
    branch: "main",
    last_indexed_at: "2025-12-12T09:00:00Z",
    created_at: "2025-12-05T10:00:00Z",
  },
  {
    id: "r3",
    project_id: "p3",
    name: "redis",
    source_type: "local_path",
    source_uri: "/data/repos/redis",
    local_path: "/data/repos/redis",
    branch: "unstable",
    last_indexed_at: null,
    created_at: "2025-12-08T16:00:00Z",
  },
];

export const mockTasks: AnalysisTask[] = [
  {
    id: "t1",
    repository_id: "r1",
    task_type: "full_repo",
    status: "completed",
    tools: ["deepwiki"],
    ai_enabled: true,
    progress: 100,
    error: null,
    ai_summary:
      "Linux kernel documentation generated successfully. Architecture diagrams include subsystem relationships, memory management flow, and scheduler interaction patterns.",
    started_at: "2025-12-10T14:00:00Z",
    completed_at: "2025-12-10T14:28:00Z",
    created_at: "2025-12-10T14:00:00Z",
  },
  {
    id: "t2",
    repository_id: "r2",
    task_type: "full_repo",
    status: "running",
    tools: ["deepwiki"],
    ai_enabled: true,
    progress: 62,
    error: null,
    ai_summary: null,
    started_at: "2025-12-12T09:10:00Z",
    completed_at: null,
    created_at: "2025-12-12T09:10:00Z",
  },
  {
    id: "t3",
    repository_id: "r1",
    task_type: "file_paths",
    status: "failed",
    tools: ["deepwiki"],
    ai_enabled: false,
    progress: 34,
    error: "Connection refused: deepwiki service unavailable",
    ai_summary: null,
    started_at: "2025-12-11T11:00:00Z",
    completed_at: "2025-12-11T11:05:00Z",
    created_at: "2025-12-11T11:00:00Z",
  },
  {
    id: "t4",
    repository_id: "r3",
    task_type: "full_repo",
    status: "pending",
    tools: ["deepwiki"],
    ai_enabled: true,
    progress: 0,
    error: null,
    ai_summary: null,
    started_at: null,
    completed_at: null,
    created_at: "2025-12-12T10:00:00Z",
  },
];

export const mockTaskDetail: TaskDetail = {
  ...mockTasks[0],
  tool_runs: [
    {
      id: "tr1",
      tool_name: "deepwiki",
      status: "completed",
      started_at: "2025-12-10T14:00:10Z",
      completed_at: "2025-12-10T14:27:50Z",
      result: {
        documentation: "# Linux Kernel Architecture\n\n## Overview\n\nThe Linux kernel is a monolithic kernel with modular capabilities...\n\n## Subsystems\n\n### Memory Management\nThe memory management subsystem handles virtual memory, page allocation, and memory mapping.\n\n### Process Scheduler\nThe Completely Fair Scheduler (CFS) provides fair CPU time distribution.\n\n```c\nstruct sched_entity {\n    struct load_weight load;\n    struct rb_node run_node;\n    u64 vruntime;\n};\n```\n\n### File Systems\nVFS provides a unified interface for multiple filesystem implementations.\n\n## Architecture Diagram\n\n```mermaid\ngraph TD\n    A[User Space] --> B[System Call Interface]\n    B --> C[VFS]\n    B --> D[Process Scheduler]\n    B --> E[Memory Manager]\n    C --> F[ext4]\n    C --> G[btrfs]\n    D --> H[CFS]\n    E --> I[Page Allocator]\n    E --> J[Slab Allocator]\n```\n",
        diagrams: [
          "graph TD\n    A[User Space] --> B[System Call Interface]\n    B --> C[VFS]\n    B --> D[Process Scheduler]\n    B --> E[Memory Manager]",
        ],
      },
      error: null,
    },
  ],
};

export const mockTools: ToolInfo[] = [
  {
    name: "deepwiki",
    capabilities: ["documentation", "knowledge_graph", "architecture_diagram"],
    healthy: true,
    message: "Connected to deepwiki at localhost:8001",
  },
  {
    name: "zoekt",
    capabilities: ["code_search"],
    healthy: false,
    message: "Not configured",
  },
  {
    name: "joern",
    capabilities: [
      "call_graph",
      "taint_analysis",
      "security_scan",
      "ast_analysis",
    ],
    healthy: false,
    message: "Not configured",
  },
  {
    name: "codecompass",
    capabilities: ["call_graph", "dependency_graph", "pointer_analysis"],
    healthy: false,
    message: "Not configured",
  },
  {
    name: "gitnexus",
    capabilities: ["code_search", "dependency_graph"],
    healthy: false,
    message: "Not configured",
  },
];

export const mockLLMConfigs: LLMConfig[] = [
  {
    id: "llm1",
    provider: "google",
    model_name: "gemini-2.0-flash",
    has_api_key: true,
    base_url: null,
    proxy_mode: "system",
    is_default: true,
    created_at: "2025-12-01T08:00:00Z",
  },
];

export const mockLogs: LogEntry[] = [
  {
    timestamp: "2025-12-10T14:00:00Z",
    level: "info",
    message: "Task t1 started — full_repo analysis on linux",
    tool: "orchestrator",
  },
  {
    timestamp: "2025-12-10T14:00:05Z",
    level: "info",
    message: "Cloning repository: https://github.com/torvalds/linux.git",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:02:30Z",
    level: "info",
    message: "Repository cloned. Starting embedding generation...",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:10:00Z",
    level: "info",
    message: "Embedding complete. 2,847 files indexed.",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:10:05Z",
    level: "debug",
    message: "RAG query: architecture overview",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:15:00Z",
    level: "warn",
    message: "Large context window — chunking response",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:27:50Z",
    level: "info",
    message: "Documentation generated: 3 sections, 1 architecture diagram",
    tool: "deepwiki",
  },
  {
    timestamp: "2025-12-10T14:28:00Z",
    level: "info",
    message: "Task t1 completed successfully",
    tool: "orchestrator",
  },
];

export const repoNameById: Record<string, string> = {
  r1: "linux",
  r2: "llvm-project",
  r3: "redis",
};

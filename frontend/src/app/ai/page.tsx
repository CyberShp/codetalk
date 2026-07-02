"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Bot,
  CalendarClock,
  FolderPlus,
  Loader2,
  MessageSquarePlus,
  MessageSquareText,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { AgentRuntime, AIConversation, Workspace } from "@/lib/types";

type ProjectRow = {
  id: string;
  name: string;
  detail: string;
  count: number;
  workspace: Workspace | null;
};

function projectIdForThread(thread: AIConversation): string {
  if (thread.workspace_id && thread.workspace_id !== "global") return thread.workspace_id;
  if (thread.scope_type === "workspace") return thread.scope_id;
  if (thread.scope_type === "module") return thread.scope_id.split(":")[0] ?? "global";
  return "global";
}

function publicProjectDetail(workspace: Workspace): string {
  return `workspace:${workspace.id}`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function scopeLabel(thread: AIConversation): string {
  const labels: Record<string, string> = {
    workspace: "项目线程",
    workbench_task_run: "运行复盘",
    workflow: "工作流",
    report: "报告",
    module: "代码模块",
    requirement_doc: "需求文档",
    test_case_set: "用例集",
    freeform: "自由调查",
  };
  return labels[thread.scope_type] ?? thread.scope_type;
}

export default function AIHomePage() {
  const router = useRouter();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [threads, setThreads] = useState<AIConversation[]>([]);
  const [agentRuntimes, setAgentRuntimes] = useState<AgentRuntime[]>([]);
  const [selectedRuntimeId, setSelectedRuntimeId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const deletingThreadRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [workspaceItems, threadResult, runtimeResult] = await Promise.all([
          api.workspaces.list(),
          api.aiConversations.list({ limit: 100 }),
          api.settings.listAgentRuntimes({ enabled: true }).catch(() => ({ items: [] as AgentRuntime[] })),
        ]);
        if (cancelled) return;
        setWorkspaces(workspaceItems);
        setThreads(threadResult.items);
        setAgentRuntimes(runtimeResult.items);
        setSelectedRuntimeId((current) => current || runtimeResult.items[0]?.id || "builtin_llm");
        setSelectedProjectId((current) => current || workspaceItems[0]?.id || "global");
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "加载 AI 线程失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const projectRows = useMemo(() => {
    const counts = new Map<string, number>();
    for (const thread of threads) {
      const id = projectIdForThread(thread);
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    const rows: ProjectRow[] = workspaces.map((workspace) => ({
      id: workspace.id,
      name: workspace.name,
      detail: publicProjectDetail(workspace),
      count: counts.get(workspace.id) ?? 0,
      workspace,
    }));
    if (counts.has("global") || rows.length === 0) {
      rows.push({
        id: "global",
        name: "未绑定项目",
        detail: "自由调查、历史导入或旧线程",
        count: counts.get("global") ?? 0,
        workspace: null,
      });
    }
    return rows;
  }, [threads, workspaces]);

  const selectedProject = projectRows.find((item) => item.id === selectedProjectId) ?? projectRows[0] ?? null;
  const visibleThreads = useMemo(
    () => threads.filter((thread) => projectIdForThread(thread) === (selectedProject?.id ?? "")),
    [selectedProject?.id, threads],
  );

  const createThread = async () => {
    if (!selectedProject?.workspace || creating) return;
    setCreating(true);
    setError(null);
    try {
      const conversation = await api.aiConversations.create({
        scope_type: "workspace",
        scope_id: selectedProject.workspace.id,
        workspace_id: selectedProject.workspace.id,
        memory_namespace: `workspace:${selectedProject.workspace.id}`,
        runtime_type: selectedRuntimeId && selectedRuntimeId !== "builtin_llm" ? "agent_runtime" : "builtin_llm",
        agent_runtime_id: selectedRuntimeId && selectedRuntimeId !== "builtin_llm" ? selectedRuntimeId : null,
        title: title.trim() || `${selectedProject.workspace.name} · AI 调查线程`,
        initial_context: {
          workspace_id: selectedProject.workspace.id,
          project_name: selectedProject.workspace.name,
          memory_namespace: `workspace:${selectedProject.workspace.id}`,
        },
      });
      router.push(`/ai/${conversation.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "创建线程失败");
    } finally {
      setCreating(false);
    }
  };

  const deleteThread = async (thread: AIConversation) => {
    if (thread.status === "running" || thread.latest_run?.status === "running" || thread.latest_run?.status === "queued") {
      setError("当前线程仍在生成中，请先停止后再删除。");
      return;
    }
    if (deletingThreadRef.current) return;
    const confirmed = window.confirm(`删除线程“${thread.title}”？这会删除该线程的消息和运行记录。`);
    if (!confirmed) return;
    deletingThreadRef.current = thread.id;
    setDeletingThreadId(thread.id);
    setError(null);
    try {
      await api.aiConversations.delete(thread.id);
      setThreads((current) => current.filter((item) => item.id !== thread.id));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "删除线程失败");
    } finally {
      deletingThreadRef.current = null;
      setDeletingThreadId(null);
    }
  };

  return (
    <div className="ct-ai-home mx-auto max-w-[1600px] px-4 xl:px-6">
      <header className="ct-ai-home__header">
        <div>
          <span>
            <Bot size={15} />
            CodeTalk AI
          </span>
          <h1>按项目管理持续对话</h1>
          <p>直接像 Codex 一样打开线程、切换项目、持续追问；智能体编排、报告和工作空间只是上下文来源。</p>
        </div>
        <Link href="/workspaces/new" className="ct-ai-home__new-project">
          <FolderPlus size={16} />
          新建项目
        </Link>
      </header>

      {error && <div className="ct-thread-hub__error">{error}</div>}

      {loading ? (
        <div className="flex min-h-[420px] items-center justify-center">
          <Loader2 size={24} className="animate-spin text-primary" />
        </div>
      ) : (
        <div className="ct-ai-home__grid">
          <aside className="ct-ai-home__projects">
            <div className="ct-thread-hub__section-title">
              <Sparkles size={15} />
              项目
            </div>
            <div className="ct-ai-home__project-list">
              {projectRows.map((project) => (
                <button
                  key={project.id}
                  type="button"
                  className={`ct-thread-project ${project.id === selectedProject?.id ? "is-active" : ""}`}
                  onClick={() => setSelectedProjectId(project.id)}
                >
                  <span>
                    <strong>{project.name}</strong>
                    <small>{project.detail}</small>
                  </span>
                  <em>{project.count}</em>
                </button>
              ))}
            </div>
          </aside>

          <main className="ct-ai-home__threads" key={selectedProject?.id ?? "none"}>
            <div className="ct-thread-hub__thread-head">
              <div>
                <span>当前项目</span>
                <h2>{selectedProject?.name ?? "未选择项目"}</h2>
                <p>{selectedProject?.detail ?? "选择一个项目来查看或新建线程"}</p>
              </div>
              {selectedProject?.workspace && (
                <div className="ct-thread-create">
                  <select
                    value={selectedRuntimeId || "builtin_llm"}
                    onChange={(event) => setSelectedRuntimeId(event.target.value)}
                    aria-label="AI 线程执行器"
                  >
                    {agentRuntimes.map((runtime) => (
                      <option key={runtime.id} value={runtime.id}>
                        {runtime.name}
                      </option>
                    ))}
                    <option value="builtin_llm">内置模型</option>
                  </select>
                  <input
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder="线程名称，例如：登录需求评审 / MR #128 复测"
                  />
                  <button type="button" onClick={createThread} disabled={creating}>
                    {creating ? <Loader2 size={15} className="animate-spin" /> : <MessageSquarePlus size={15} />}
                    新建线程
                  </button>
                </div>
              )}
            </div>

            {visibleThreads.length === 0 ? (
              <div className="ct-thread-hub__empty ct-thread-hub__empty--large">
                <MessageSquareText size={34} />
                <p>这个项目还没有 AI 调查线程。新建一个线程后，可以围绕需求、报告、用例或运行结果持续追问。</p>
              </div>
            ) : (
              <div className="ct-thread-timeline">
                {visibleThreads.slice(0, 50).map((thread) => (
                  <div key={thread.id} className="ct-thread-card-row">
                    <Link
                      href={`/ai/${thread.id}`}
                      className="ct-thread-card"
                    >
                      <div>
                        <span>{scopeLabel(thread)}</span>
                        <h3>{thread.title}</h3>
                        <p>{thread.scope_type} / {thread.scope_id}</p>
                      </div>
                      <div className="ct-thread-card__meta">
                        <span>
                          <CalendarClock size={13} />
                          {formatTime(thread.updated_at)}
                        </span>
                        <em className={thread.status === "running" ? "is-running" : ""}>
                          {thread.status === "running" ? "生成中" : "已保存"}
                        </em>
                        <ArrowRight size={16} />
                      </div>
                    </Link>
                    <button
                      type="button"
                      className="ct-thread-card__delete"
                      onClick={() => void deleteThread(thread)}
                      disabled={deletingThreadId === thread.id}
                      title="删除线程"
                      aria-label={`删除线程 ${thread.title}`}
                    >
                      {deletingThreadId === thread.id ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  );
}

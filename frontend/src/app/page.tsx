"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  FolderOpen,
  BookOpen,
  Plus,
  Wrench,
  Loader2,
  RefreshCw,
  Archive,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace, Task, ToolInfo, TaskStatus, DeepWikiRepo, DeepWikiStatus } from "@/lib/types";

type RepoListItem = Omit<DeepWikiRepo, "wiki_data" | "pages">;

const TASK_STATUS_CONFIG: Record<TaskStatus, { label: string; color: string; bg: string }> = {
  pending: { label: "等待中", color: "text-amber-400", bg: "bg-amber-400/10" },
  running: { label: "运行中", color: "text-blue-400", bg: "bg-blue-400/10" },
  completed: { label: "已完成", color: "text-green-400", bg: "bg-green-400/10" },
  completed_with_warnings: { label: "部分完成", color: "text-yellow-400", bg: "bg-yellow-400/10" },
  failed: { label: "失败", color: "text-red-400", bg: "bg-red-400/10" },
};

const DEEPWIKI_BADGE: Record<DeepWikiStatus, { label: string; cls: string }> = {
  pending: { label: "待生成", cls: "bg-amber-400/10 text-amber-400" },
  running: { label: "生成中", cls: "bg-blue-400/10 text-blue-400" },
  completed: { label: "已完成", cls: "bg-green-400/10 text-green-400" },
  failed: { label: "失败", cls: "bg-red-400/10 text-red-400" },
};

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function WorkbenchPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [repos, setRepos] = useState<RepoListItem[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [sectionErrors, setSectionErrors] = useState<{
    workspaces?: string;
    repos?: string;
    tasks?: string;
  }>({});

  const handleDeleteTask = useCallback(
    async (e: React.MouseEvent, taskId: string) => {
      e.preventDefault();
      e.stopPropagation();
      if (!confirm("确定要删除此任务吗？")) return;
      try {
        await api.tasks.delete(taskId);
        setTasks((prev) => prev.filter((t) => t.id !== taskId));
      } catch (err: unknown) {
        console.error("删除任务失败:", err);
      }
    },
    [],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setToolsLoading(true);
    setSectionErrors({});
    const [wsResult, dwResult, taskResult, toolResult] = await Promise.allSettled([
      api.workspaces.list(),
      api.deepwiki.list(),
      api.tasks.list(),
      api.tools.status(),
    ]);
    const errs: { workspaces?: string; repos?: string; tasks?: string } = {};
    if (wsResult.status === "fulfilled") setWorkspaces(wsResult.value);
    else errs.workspaces = "加载失败，请刷新重试";
    if (dwResult.status === "fulfilled") setRepos(dwResult.value);
    else errs.repos = "加载失败，请刷新重试";
    if (taskResult.status === "fulfilled") setTasks(taskResult.value);
    else errs.tasks = "加载失败，请刷新重试";
    if (toolResult.status === "fulfilled") setTools(toolResult.value);
    setSectionErrors(errs);
    setLoading(false);
    setToolsLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const recentTasks = tasks.slice(0, 5);

  return (
    <div className="max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            CodeTalk 工作台
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            代码分析与知识工作台
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            aria-label="刷新数据"
            className="flex items-center gap-2 px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors"
          >
            <RefreshCw size={14} />
            刷新
          </button>
          <Link
            href="/workspaces/new"
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus size={16} />
            新建工作空间
          </Link>
        </div>
      </div>

      {/* Tool Status */}
      <div className="mb-8">
        <h2 className="text-sm font-medium text-on-surface-variant mb-3 flex items-center gap-2">
          <Wrench size={14} />
          工具状态
          {toolsLoading && <Loader2 size={12} className="animate-spin" />}
        </h2>
        {toolsLoading ? (
          <div className="grid grid-cols-2 gap-3">
            {[1, 2].map((i) => (
              <div
                key={i}
                className="h-12 bg-surface-container rounded-lg border border-outline-variant/20 animate-pulse"
              />
            ))}
          </div>
        ) : tools.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {tools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center justify-between bg-surface-container rounded-lg px-4 py-3 border border-outline-variant/20"
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-2 h-2 rounded-full ${
                      tool.healthy ? "bg-green-400" : "bg-red-400"
                    }`}
                  />
                  <span className="text-sm font-medium text-on-surface">
                    {tool.display_name}
                  </span>
                </div>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    tool.healthy
                      ? "bg-green-400/10 text-green-400"
                      : "bg-red-400/10 text-red-400"
                  }`}
                >
                  {tool.healthy ? "正常" : "离线"}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Workspaces Section */}
      <div className="mb-8">
        <h2 className="text-sm font-medium text-on-surface-variant mb-3 flex items-center gap-2">
          <FolderOpen size={14} />
          工作空间
        </h2>
        {loading ? (
          <div className="grid grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-24 bg-surface-container rounded-xl border border-outline-variant/20 animate-pulse"
              />
            ))}
          </div>
        ) : sectionErrors.workspaces ? (
          <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
            工作空间{sectionErrors.workspaces}
          </div>
        ) : workspaces.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
            <FolderOpen size={28} className="text-on-surface-variant/40" />
            <p className="text-on-surface-variant text-sm">还没有工作空间</p>
            <Link
              href="/workspaces/new"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
            >
              <Plus size={12} />
              新建工作空间
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {workspaces.map((ws) => (
              <Link
                key={ws.id}
                href={`/workspaces/${ws.id}`}
                className="block p-5 rounded-xl border border-outline-variant/30 bg-surface-container-low hover:bg-surface-container transition-colors"
              >
                <div className="flex items-start gap-3">
                  <FolderOpen size={20} className="text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="font-medium text-on-surface truncate">{ws.name}</p>
                    <p className="text-xs text-on-surface-variant mt-0.5 truncate">
                      {ws.repo_path}
                    </p>
                    <div className="flex items-center gap-2 mt-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          ws.indexed === 1
                            ? "bg-green-400/10 text-green-400"
                            : ws.indexed === -1
                              ? "bg-red-400/10 text-red-400 cursor-help"
                              : "bg-amber-400/10 text-amber-400"
                        }`}
                        title={ws.indexed === -1 && ws.last_index_error ? ws.last_index_error : undefined}
                      >
                        {ws.indexed === 1 ? "已索引" : ws.indexed === -1 ? `索引失败${ws.last_index_error ? " ⓘ" : ""}` : "索引中"}
                      </span>
                      <span className="text-xs text-on-surface-variant">
                        {ws.reports.length} 份报告
                      </span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* DeepWiki Section */}
      <div className="mb-8">
        <h2 className="text-sm font-medium text-on-surface-variant mb-3 flex items-center gap-2">
          <BookOpen size={14} />
          DeepWiki 知识库
        </h2>
        {loading ? (
          <div className="grid grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-24 bg-surface-container rounded-xl border border-outline-variant/20 animate-pulse"
              />
            ))}
          </div>
        ) : sectionErrors.repos ? (
          <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
            DeepWiki 知识库{sectionErrors.repos}
          </div>
        ) : repos.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
            <BookOpen size={28} className="text-on-surface-variant/40" />
            <p className="text-on-surface-variant text-sm">还没有知识库</p>
            <Link
              href="/deepwiki"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors"
            >
              前往 DeepWiki
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {repos.map((repo) => {
              const badge = DEEPWIKI_BADGE[repo.status];
              return (
                <Link
                  key={repo.id}
                  href={`/deepwiki/${repo.id}`}
                  className="block p-5 rounded-xl border border-outline-variant/30 bg-surface-container-low hover:bg-surface-container transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <BookOpen size={20} className="text-primary shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-on-surface truncate">{repo.name}</p>
                      <p className="text-xs text-on-surface-variant mt-0.5 truncate font-mono">
                        {repo.repo_path}
                      </p>
                      <div className="flex items-center gap-2 mt-2">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${badge.cls}`}>
                          {badge.label}
                        </span>
                        {repo.status === "completed" && (
                          <span className="text-xs text-on-surface-variant">
                            {repo.page_count} 页
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      {/* Recent Tasks — rendered when tasks exist or when there's a fetch error */}
      {!loading && (recentTasks.length > 0 || sectionErrors.tasks) && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-on-surface-variant flex items-center gap-2">
              <Archive size={14} />
              历史任务
            </h2>
            <Link
              href="/tasks"
              className="text-xs text-primary hover:text-primary-fixed-dim transition-colors"
            >
              查看全部
            </Link>
          </div>
          {sectionErrors.tasks ? (
            <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
              历史任务{sectionErrors.tasks}
            </div>
          ) : null}
          <div className="space-y-2">
            {recentTasks.map((task) => {
              const cfg = TASK_STATUS_CONFIG[task.status];
              return (
                <div
                  key={task.id}
                  className="flex items-center gap-2 bg-surface-container hover:bg-surface-container-high rounded-xl border border-outline-variant/20 transition-colors group"
                >
                  <Link
                    href={`/tasks/${task.id}`}
                    className="flex flex-1 items-center gap-4 px-5 py-4 min-w-0"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-on-surface group-hover:text-primary transition-colors truncate">
                        {task.name}
                      </p>
                      <p className="text-xs text-on-surface-variant mt-0.5 truncate">
                        {task.repo_path}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <div className="flex gap-1">
                        {task.tools.map((t) => (
                          <span
                            key={t}
                            className="text-[10px] px-1.5 py-0.5 bg-surface-container-high rounded text-on-surface-variant"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.color}`}>
                        {cfg.label}
                      </span>
                      {task.status === "running" && (
                        <span className="text-xs text-on-surface-variant tabular-nums">
                          {task.progress}%
                        </span>
                      )}
                      <span className="text-xs text-on-surface-variant/60">
                        {formatTime(task.created_at)}
                      </span>
                    </div>
                  </Link>
                  <button
                    onClick={(e) => handleDeleteTask(e, task.id)}
                    className="mr-3 p-2 rounded-lg text-on-surface-variant/40 hover:text-red-400 hover:bg-red-400/10 transition-colors shrink-0"
                    title="删除任务"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

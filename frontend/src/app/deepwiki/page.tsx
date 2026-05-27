"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, Plus, Loader2, CheckCircle, Clock, AlertCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { DeepWikiRepo, DeepWikiStatus } from "@/lib/types";

type RepoListItem = Omit<DeepWikiRepo, "wiki_data" | "pages">;

const STATUS_BADGE: Record<DeepWikiStatus, { label: string; cls: string; icon: React.ReactNode }> = {
  pending: {
    label: "待生成",
    cls: "bg-amber-400/10 text-amber-400",
    icon: <Clock size={12} />,
  },
  running: {
    label: "生成中",
    cls: "bg-blue-400/10 text-blue-400",
    icon: <RefreshCw size={12} className="animate-spin" />,
  },
  completed: {
    label: "已完成",
    cls: "bg-green-400/10 text-green-400",
    icon: <CheckCircle size={12} />,
  },
  failed: {
    label: "失败",
    cls: "bg-red-400/10 text-red-400",
    icon: <AlertCircle size={12} />,
  },
};

function AddRepoDialog({ onCreated }: { onCreated: (repo: RepoListItem) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const repo = await api.deepwiki.create({ name, repo_path: repoPath });
      onCreated(repo);
      setOpen(false);
      setName("");
      setRepoPath("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-4 py-2 bg-primary text-on-primary rounded-lg text-sm font-medium hover:opacity-90 transition-opacity"
      >
        <Plus size={16} />
        添加仓库
      </button>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <form
        onSubmit={handleSubmit}
        className="bg-surface rounded-2xl p-6 w-full max-w-md shadow-xl flex flex-col gap-4"
      >
        <h2 className="text-lg font-semibold text-on-surface">添加 DeepWiki 仓库</h2>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-on-surface-variant">仓库名称</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="my-project"
            className="rounded-lg border border-outline-variant/50 bg-surface-container px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-on-surface-variant">本地路径</label>
          <input
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
            required
            placeholder="/path/to/repo"
            className="rounded-lg border border-outline-variant/50 bg-surface-container px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary font-mono"
          />
        </div>

        {error && (
          <p className="text-xs text-error">{error}</p>
        )}

        <div className="flex gap-3 justify-end pt-1">
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="px-4 py-2 text-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-on-primary rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitting && <Loader2 size={14} className="animate-spin" />}
            添加
          </button>
        </div>
      </form>
    </div>
  );
}

export default function DeepWikiPage() {
  const [repos, setRepos] = useState<RepoListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.deepwiki
      .list()
      .then(setRepos)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "加载失败")
      )
      .finally(() => setLoading(false));
  }, []);

  // Auto-refresh list while any repo is generating
  useEffect(() => {
    const hasRunning = repos.some((r) => r.status === "running");
    if (!hasRunning) return;
    const id = setInterval(() => {
      api.deepwiki.list().then(setRepos).catch(() => undefined);
    }, 5000);
    return () => clearInterval(id);
  }, [repos]);

  function handleCreated(repo: RepoListItem) {
    setRepos((prev) => [repo, ...prev]);
  }

  return (
    <div className="w-full px-4 xl:px-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-on-surface">DeepWiki</h1>
          <p className="text-sm text-on-surface-variant mt-1">
            代码知识库文档 — 自动生成仓库 Wiki
          </p>
        </div>
        <AddRepoDialog onCreated={handleCreated} />
      </div>

      {loading && (
        <div className="flex justify-center py-16">
          <Loader2 size={24} className="animate-spin text-primary" />
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-error/10 border border-error/20 px-4 py-3 text-sm text-error">
          {error}
        </div>
      )}

      {!loading && !error && repos.length === 0 && (
        <div className="flex flex-col items-center justify-center h-64 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
          <BookOpen size={40} className="text-on-surface-variant/40" />
          <p className="text-on-surface-variant text-sm">
            还没有仓库，点击右上角添加
          </p>
        </div>
      )}

      {!loading && repos.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {repos.map((repo) => {
            const badge = STATUS_BADGE[repo.status];
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
                      <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${badge.cls}`}>
                        {badge.icon}
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
  );
}

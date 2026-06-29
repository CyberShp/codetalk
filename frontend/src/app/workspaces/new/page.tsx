"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, FolderSearch, Loader2 } from "lucide-react";
import Link from "next/link";
import { api, DuplicateWorkspaceError } from "@/lib/api";

export default function NewWorkspacePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existingWorkspace, setExistingWorkspace] = useState<{
    id: string;
    name?: string;
  } | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!name.trim()) { setError("请输入工作空间名称"); return; }
      if (!repoPath.trim()) { setError("请输入代码仓库路径"); return; }

      setSubmitting(true);
      setError(null);
      setExistingWorkspace(null);
      try {
        const ws = await api.workspaces.create({
          name: name.trim(),
          repo_path: repoPath.trim(),
        });
        router.push(`/workspaces/${ws.id}`);
      } catch (err: unknown) {
        if (err instanceof DuplicateWorkspaceError) {
          setExistingWorkspace({
            id: err.existingWorkspaceId,
            name: err.existingWorkspaceName,
          });
        }
        setError(err instanceof Error ? err.message : "创建工作空间失败");
      } finally {
        setSubmitting(false);
      }
    },
    [name, repoPath, router],
  );

  return (
    <div className="max-w-lg">
      <div className="flex items-center gap-3 mb-6">
        <Link
          href="/workspaces"
          className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
        >
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            新建工作空间
          </h1>
          <p className="text-sm text-on-surface-variant mt-0.5">
            创建持久化代码分析工作空间
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-5 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <div>{error}</div>
          {existingWorkspace && (
            <Link
              href={`/workspaces/${existingWorkspace.id}`}
              className="mt-2 inline-flex items-center rounded-md border border-red-500/30 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/10 hover:text-red-200 transition-colors"
            >
              打开已有工作空间{existingWorkspace.name ? `：${existingWorkspace.name}` : ""}
            </Link>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            工作空间名称
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：项目 A 分析工作台"
            className="w-full px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            代码仓库路径
          </label>
          <div className="relative">
            <FolderSearch
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50"
            />
            <input
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="本地文件夹路径，如 /home/user/project"
              className="w-full pl-10 pr-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors font-data"
            />
          </div>
          <p className="text-xs text-on-surface-variant/60 mt-1">
            服务器上可访问的本地路径，创建后将自动触发代码索引
          </p>
        </div>

        <div className="pt-1">
          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                创建中...
              </>
            ) : (
              "创建工作空间"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

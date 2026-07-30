"use client";

import { useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ChevronRight,
  FolderOpen,
  FolderSearch,
  HardDrive,
  Home,
  Loader2,
  X,
} from "lucide-react";
import Link from "next/link";
import { api, DuplicateWorkspaceError } from "@/lib/api";
import type { WorkspaceFolderBrowseResponse } from "@/lib/types";

function workspaceCreateErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "创建工作空间失败";
  if (/该代码路径已存在工作空间/.test(message)) {
    return message;
  }
  if (/代码路径不存在|代码路径不是目录|repo_path|路径/.test(message)) {
    return [
      message,
      "修复建议：请确认路径拼写、挂载点和权限；macOS 外置盘通常是 /Volumes/...，不是 /Volums/...",
    ].join("\n");
  }
  return message;
}

export default function NewWorkspacePage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existingWorkspace, setExistingWorkspace] = useState<{
    id: string;
    name?: string;
  } | null>(null);
  const [repoPath, setRepoPath] = useState("");
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [folderData, setFolderData] = useState<WorkspaceFolderBrowseResponse | null>(null);
  const [folderPathDraft, setFolderPathDraft] = useState("");
  const [folderLoading, setFolderLoading] = useState(false);
  const [folderError, setFolderError] = useState<string | null>(null);
  const submittingRef = useRef(false);

  const loadFolders = useCallback(async (path?: string) => {
    setFolderLoading(true);
    setFolderError(null);
    try {
      const data = await api.workspaces.browseFolders(path);
      setFolderData(data);
      setFolderPathDraft(data.path);
    } catch (err: unknown) {
      setFolderError(err instanceof Error ? err.message : "无法读取文件夹");
    } finally {
      setFolderLoading(false);
    }
  }, []);

  const openFolderPicker = useCallback(() => {
    setFolderPickerOpen(true);
    void loadFolders(repoPath.trim() || undefined);
  }, [loadFolders, repoPath]);

  const chooseFolder = useCallback((path: string) => {
    setRepoPath(path);
    setFolderPickerOpen(false);
    setFolderError(null);
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (submittingRef.current) return;
      const form = e.currentTarget as HTMLFormElement;
      const formData = new FormData(form);
      const submittedName = String(formData.get("name") ?? "").trim();
      const submittedRepoPath = repoPath.trim();
      if (!submittedName) { setError("请输入工作空间名称"); return; }

      submittingRef.current = true;
      setSubmitting(true);
      setError(null);
      setExistingWorkspace(null);
      try {
        const ws = await api.workspaces.create({
          name: submittedName,
          repo_path: submittedRepoPath,
        });
        router.push(`/workspaces/${ws.id}`);
      } catch (err: unknown) {
        if (err instanceof DuplicateWorkspaceError) {
          setExistingWorkspace({
            id: err.existingWorkspaceId,
            name: err.existingWorkspaceName,
          });
        }
        setError(workspaceCreateErrorMessage(err));
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
    },
    [repoPath, router],
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
        <div
          role="alert"
          className="mb-5 whitespace-pre-line px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400"
        >
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
          <label htmlFor="workspace-name" className="block text-sm font-medium text-on-surface mb-1.5">
            工作空间名称
          </label>
          <input
            name="name"
            id="workspace-name"
            type="text"
            placeholder="例如：项目 A 分析工作台"
            className="w-full px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors"
          />
        </div>

        <div>
          <label htmlFor="workspace-repo-path" className="block text-sm font-medium text-on-surface mb-1.5">
            本地文件夹路径 <span className="text-on-surface-variant/60">（可选）</span>
          </label>
          <div className="relative">
            <FolderSearch
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50"
            />
            <input
              name="repoPath"
              id="workspace-repo-path"
              type="text"
              value={repoPath}
              onChange={(event) => setRepoPath(event.currentTarget.value)}
              placeholder="本地文件夹路径，可留空，如 /home/user/project"
              className="w-full pl-10 pr-24 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors font-data"
            />
            <button
              type="button"
              onClick={openFolderPicker}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 rounded-md border border-outline-variant/30 px-2.5 py-1.5 text-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
            >
              <FolderOpen size={13} />
              浏览
            </button>
          </div>
          <p className="text-xs text-on-surface-variant/60 mt-1">
            填写服务器可访问的本地路径后会自动触发代码索引；留空时可先创建工作空间，后续补充材料或继续 AI 调查
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

      {folderPickerOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="folder-picker-title"
          className="ct-modal-backdrop fixed inset-0 flex items-center justify-center bg-black/40 px-4"
          style={{ zIndex: 80 }}
        >
          <div className="ct-modal-panel flex max-h-[88vh] w-[720px] max-w-[96vw] flex-col overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface shadow-xl">
            <header className="flex items-center justify-between border-b border-outline-variant/20 px-5 py-3">
              <div className="min-w-0">
                <h2 id="folder-picker-title" className="text-base font-semibold text-on-surface">
                  选择本地文件夹
                </h2>
                <p className="mt-0.5 truncate text-[11px] font-data text-on-surface-variant/70">
                  {folderData?.path ?? "正在读取文件夹"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setFolderPickerOpen(false)}
                className="rounded-md p-1.5 text-on-surface-variant hover:bg-surface-container"
                aria-label="关闭"
              >
                <X size={16} />
              </button>
            </header>

            <div className="space-y-3 border-b border-outline-variant/20 px-5 py-4">
              <div className="flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <FolderSearch
                    size={14}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50"
                  />
                  <input
                    value={folderPathDraft}
                    onChange={(event) => setFolderPathDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void loadFolders(folderPathDraft);
                    }}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface-container py-2 pl-9 pr-3 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/20"
                    placeholder="/Volumes/Media/project"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void loadFolders(folderPathDraft)}
                  disabled={folderLoading || !folderPathDraft.trim()}
                  className="inline-flex items-center justify-center rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {folderLoading ? <Loader2 size={15} className="animate-spin" /> : "打开"}
                </button>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void loadFolders(folderData?.home_path)}
                  disabled={folderLoading || !folderData?.home_path}
                  className="inline-flex items-center gap-1.5 rounded-md border border-outline-variant/30 px-2.5 py-1.5 text-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Home size={13} />
                  Home
                </button>
                <button
                  type="button"
                  onClick={() => void loadFolders("/")}
                  disabled={folderLoading}
                  className="inline-flex items-center gap-1.5 rounded-md border border-outline-variant/30 px-2.5 py-1.5 text-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <HardDrive size={13} />
                  根目录
                </button>
                {folderData?.parent_path && (
                  <button
                    type="button"
                    onClick={() => void loadFolders(folderData.parent_path ?? undefined)}
                    disabled={folderLoading}
                    className="inline-flex items-center gap-1.5 rounded-md border border-outline-variant/30 px-2.5 py-1.5 text-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <ArrowLeft size={13} />
                    上级
                  </button>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
              {folderError ? (
                <div className="mx-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
                  {folderError}
                </div>
              ) : folderLoading && !folderData ? (
                <div className="flex justify-center py-12 text-primary">
                  <Loader2 size={20} className="animate-spin" />
                </div>
              ) : folderData && folderData.entries.length === 0 ? (
                <div className="px-3 py-10 text-center text-sm text-on-surface-variant">
                  此文件夹下没有可进入的子文件夹
                </div>
              ) : (
                <div className="space-y-1">
                  {folderData?.entries.map((entry) => (
                    <div
                      key={entry.path}
                      className="group flex items-center gap-2 rounded-lg px-2 py-2 hover:bg-surface-container"
                    >
                      <button
                        type="button"
                        onClick={() => void loadFolders(entry.path)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <FolderOpen
                          size={16}
                          className={entry.hidden ? "shrink-0 text-on-surface-variant/50" : "shrink-0 text-primary"}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-on-surface">
                            {entry.name}
                          </span>
                          <span className="block truncate font-data text-[11px] text-on-surface-variant/60">
                            {entry.path}
                          </span>
                        </span>
                        <ChevronRight size={15} className="shrink-0 text-on-surface-variant/50" />
                      </button>
                      <button
                        type="button"
                        onClick={() => chooseFolder(entry.path)}
                        className="rounded-md border border-outline-variant/30 px-2.5 py-1.5 text-xs text-on-surface-variant opacity-0 transition-opacity hover:bg-surface-container-high hover:text-on-surface group-hover:opacity-100 focus:opacity-100"
                      >
                        选择
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <footer className="flex items-center justify-end gap-2 border-t border-outline-variant/20 px-5 py-3">
              <button
                type="button"
                onClick={() => setFolderPickerOpen(false)}
                className="rounded-lg border border-outline-variant/30 px-3 py-2 text-sm text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => folderData && chooseFolder(folderData.path)}
                disabled={!folderData}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <FolderOpen size={14} />
                选择此文件夹
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}

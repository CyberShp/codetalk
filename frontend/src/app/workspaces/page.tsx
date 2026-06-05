"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, FolderOpen, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace } from "@/lib/types";

export default function WorkspacesPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.workspaces
      .list()
      .then(setWorkspaces)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "加载失败")
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="w-full px-4 xl:px-6">
      <div className="flex flex-col gap-4 mb-8 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-on-surface">工作空间</h1>
          <p className="text-sm text-on-surface-variant mt-1">
            持久化代码分析工作空间
          </p>
        </div>
        <Link
          href="/workspaces/new"
          className="flex w-full items-center justify-center gap-2 whitespace-nowrap px-4 py-2 bg-primary text-on-primary rounded-lg text-sm font-medium hover:opacity-90 transition-opacity sm:w-auto"
        >
          <Plus size={16} />
          新建工作空间
        </Link>
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

      {!loading && !error && workspaces.length === 0 && (
        <div className="flex flex-col items-center justify-center h-64 rounded-xl border border-outline-variant/30 bg-surface-container-low gap-3">
          <FolderOpen size={40} className="text-on-surface-variant/40" />
          <p className="text-on-surface-variant text-sm">
            还没有工作空间，点击右上角新建
          </p>
        </div>
      )}

      {!loading && workspaces.length > 0 && (
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
  );
}

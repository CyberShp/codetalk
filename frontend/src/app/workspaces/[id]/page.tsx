"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, FolderOpen } from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace } from "@/lib/types";

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const wsId = params.id;

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!wsId) return;
    api.workspaces
      .get(wsId)
      .then(setWorkspace)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "加载失败")
      )
      .finally(() => setLoading(false));
  }, [wsId]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  if (error || !workspace) {
    return (
      <div className="max-w-3xl mx-auto">
        <Link
          href="/workspaces"
          className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
        >
          <ArrowLeft size={16} />
          返回工作空间列表
        </Link>
        <div className="rounded-lg bg-error/10 border border-error/20 px-4 py-3 text-sm text-error">
          {error ?? "工作空间不存在"}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <Link
        href="/workspaces"
        className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-on-surface mb-6"
      >
        <ArrowLeft size={16} />
        返回工作空间列表
      </Link>

      <div className="flex items-center gap-3 mb-8">
        <FolderOpen size={28} className="text-primary" />
        <div>
          <h1 className="text-2xl font-bold text-on-surface">{workspace.name}</h1>
          <p className="text-sm text-on-surface-variant mt-0.5">{workspace.repo_path}</p>
        </div>
      </div>

      <div className="flex items-center justify-center h-64 rounded-xl border border-outline-variant/30 bg-surface-container-low">
        <p className="text-on-surface-variant text-sm">Workspace Detail 功能即将上线</p>
      </div>
    </div>
  );
}

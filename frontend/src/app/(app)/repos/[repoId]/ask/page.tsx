"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import ChatPanel from "@/components/ui/ChatPanel";
import { useChatEngine } from "@/hooks/useChatEngine";
import { api } from "@/lib/api";
import type { RepoDetail } from "@/lib/types";

export default function RepoAskPage() {
  const params = useParams();
  const repoId = params.repoId as string;
  const [detail, setDetail] = useState<RepoDetail | null>(null);

  useEffect(() => {
    let alive = true;
    api.repos.get(repoId).then((d) => { if (alive) setDetail(d); }).catch(() => {});
    return () => { alive = false; };
  }, [repoId]);

  const engine = useChatEngine({ repoId });
  const repoName = detail?.repo.name ?? "Repository";

  return (
    <div className="fixed inset-0 z-[90] bg-surface text-on-surface">
      <header className="fixed inset-x-0 top-0 z-[95] flex h-16 items-center justify-between border-b border-outline-variant/20 bg-surface/95 px-6 backdrop-blur-md">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href={`/repos/${repoId}`}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-outline-variant/20 bg-surface-container-low px-4 text-sm font-medium text-on-surface transition-colors hover:border-primary/30 hover:text-primary"
          >
            <ArrowLeft size={16} />
            返回仓库
          </Link>
          <div className="h-6 w-px bg-outline-variant/20" />
          <Link
            href="/dashboard"
            className="text-[11px] font-black uppercase tracking-[0.35em] text-on-surface hover:text-primary"
          >
            CODETALKS
          </Link>
          <span className="truncate text-sm text-on-surface-variant">{repoName}</span>
        </div>
      </header>

      <div className="absolute inset-x-0 bottom-0 top-16">
        <ChatPanel engine={engine} repoId={repoId} className="h-full" />
      </div>
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  FileText,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { DeepWikiPage, DeepWikiRepo } from "@/lib/types";

function StatusBadge({ status, progress }: { status: DeepWikiRepo["status"]; progress: number }) {
  if (status === "running") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-blue-400">
        <RefreshCw size={12} className="animate-spin" />
        生成中 {progress > 0 ? `${progress}%` : ""}
      </span>
    );
  }
  if (status === "completed") {
    return <span className="text-xs text-green-400">已完成</span>;
  }
  if (status === "failed") {
    return <span className="text-xs text-red-400">生成失败</span>;
  }
  return <span className="text-xs text-amber-400">待生成</span>;
}

function MarkdownContent({ content }: { content: string }) {
  const lines = content.split("\n");
  return (
    <div className="prose prose-invert prose-sm max-w-none text-on-surface">
      {lines.map((line, i) => {
        if (line.startsWith("### ")) {
          return <h3 key={i} className="text-base font-semibold mt-4 mb-1 text-on-surface">{line.slice(4)}</h3>;
        }
        if (line.startsWith("## ")) {
          return <h2 key={i} className="text-lg font-semibold mt-5 mb-2 text-on-surface">{line.slice(3)}</h2>;
        }
        if (line.startsWith("# ")) {
          return <h1 key={i} className="text-xl font-bold mt-6 mb-3 text-on-surface">{line.slice(2)}</h1>;
        }
        if (line.startsWith("```")) {
          return <div key={i} className="text-xs font-mono text-on-surface-variant">{line}</div>;
        }
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return <li key={i} className="ml-4 text-sm text-on-surface">{line.slice(2)}</li>;
        }
        if (line.trim() === "") {
          return <div key={i} className="h-2" />;
        }
        return <p key={i} className="text-sm text-on-surface leading-relaxed">{line}</p>;
      })}
    </div>
  );
}

export default function DeepWikiRepoPage() {
  const params = useParams();
  const repoId = params.repoId as string;

  const [repo, setRepo] = useState<DeepWikiRepo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pageList, setPageList] = useState<{ id: string; title: string }[]>([]);
  const [selectedPageIndex, setSelectedPageIndex] = useState<number | null>(null);
  const [selectedPage, setSelectedPage] = useState<DeepWikiPage | null>(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function loadPageList(id: string) {
    const list = await api.deepwiki.pages(id);
    setPageList(list);
    if (list.length > 0) {
      setSelectedPageIndex(0);
      setPageLoading(true);
      try {
        const firstPage = await api.deepwiki.page(id, 0);
        setSelectedPage(firstPage);
      } finally {
        setPageLoading(false);
      }
    }
  }

  function startPolling() {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const status = await api.deepwiki.status(repoId);
        setRepo((prev) =>
          prev ? { ...prev, progress: status.progress } : prev
        );
        if (!status.running) {
          stopPolling();
          const data = await api.deepwiki.get(repoId);
          setRepo(data);
          await loadPageList(repoId);
          setGenerating(false);
        }
      } catch {
        // transient network error — keep polling
      }
    }, 3000);
  }

  useEffect(() => {
    api.deepwiki
      .get(repoId)
      .then(async (data) => {
        setRepo(data);
        if (data.status === "completed") {
          await loadPageList(repoId);
        } else if (data.status === "running") {
          setGenerating(true);
          startPolling();
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));

    return stopPolling;
    // startPolling/stopPolling/loadPageList use refs or stable api — safe to omit from deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId]);

  async function selectPage(index: number) {
    if (selectedPageIndex === index) return;
    setSelectedPageIndex(index);
    setPageLoading(true);
    try {
      const pageData = await api.deepwiki.page(repoId, index);
      setSelectedPage(pageData);
    } catch {
      // silently ignore transient error
    } finally {
      setPageLoading(false);
    }
  }

  async function handleGenerate() {
    if (!repo) return;
    setGenerating(true);
    setError(null);
    setPageList([]);
    setSelectedPage(null);
    setSelectedPageIndex(null);
    try {
      await api.deepwiki.generate(repoId);
      setRepo((prev) => prev ? { ...prev, status: "running", progress: 0 } : prev);
      startPolling();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "生成失败");
      setGenerating(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Loader2 size={24} className="animate-spin text-primary" />
      </div>
    );
  }

  if (error || !repo) {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="rounded-lg bg-error/10 border border-error/20 px-4 py-3 text-sm text-error">
          {error ?? "仓库不存在"}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-outline-variant/20 shrink-0">
        <div className="flex items-center gap-3">
          <Link
            href="/deepwiki"
            className="text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-on-surface">{repo.name}</h1>
            <p className="text-xs text-on-surface-variant font-mono mt-0.5">{repo.repo_path}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={repo.status} progress={repo.progress} />
          {repo.status !== "running" && (
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-on-primary rounded-lg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {generating ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Play size={12} />
              )}
              {repo.status === "completed" ? "重新生成" : "生成 Wiki"}
            </button>
          )}
        </div>
      </div>

      {/* 3-column layout */}
      {repo.status === "completed" && pageList.length > 0 ? (
        <div className="flex flex-1 min-h-0 gap-0 mt-4">
          {/* Left: page title list */}
          <div className="w-56 shrink-0 flex flex-col gap-0.5 overflow-y-auto pr-2 border-r border-outline-variant/20">
            {pageList.map((item, index) => (
              <button
                key={item.id}
                onClick={() => void selectPage(index)}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg text-left w-full transition-colors text-sm ${
                  selectedPageIndex === index
                    ? "bg-primary/10 text-primary"
                    : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                }`}
              >
                <FileText size={14} className="shrink-0" />
                <span className="truncate">{item.title}</span>
                {selectedPageIndex === index && (
                  <ChevronRight size={12} className="shrink-0 ml-auto" />
                )}
              </button>
            ))}
          </div>

          {/* Center: page content (lazy-loaded per selection) */}
          <div className="flex-1 min-w-0 overflow-y-auto px-6">
            {pageLoading ? (
              <div className="flex justify-center items-center h-full">
                <Loader2 size={20} className="animate-spin text-primary" />
              </div>
            ) : selectedPage ? (
              <>
                <h2 className="text-xl font-bold text-on-surface mb-4">
                  {selectedPage.title}
                </h2>
                <MarkdownContent content={selectedPage.content} />
              </>
            ) : (
              <div className="flex items-center justify-center h-full text-on-surface-variant text-sm">
                选择左侧页面查看内容
              </div>
            )}
          </div>

          {/* Right: metadata panel */}
          {selectedPage && !pageLoading && (
            <div className="w-52 shrink-0 flex flex-col gap-4 pl-4 border-l border-outline-variant/20 overflow-y-auto">
              {selectedPage.importance && (
                <div>
                  <p className="text-xs text-on-surface-variant mb-1">重要性</p>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full ${
                      selectedPage.importance === "high"
                        ? "bg-red-400/10 text-red-400"
                        : selectedPage.importance === "medium"
                        ? "bg-amber-400/10 text-amber-400"
                        : "bg-surface-container text-on-surface-variant"
                    }`}
                  >
                    {selectedPage.importance === "high"
                      ? "高"
                      : selectedPage.importance === "medium"
                      ? "中"
                      : "低"}
                  </span>
                </div>
              )}

              {selectedPage.filePaths && selectedPage.filePaths.length > 0 && (
                <div>
                  <p className="text-xs text-on-surface-variant mb-1.5">关联文件</p>
                  <ul className="flex flex-col gap-1">
                    {selectedPage.filePaths.map((fp) => (
                      <li
                        key={fp}
                        className="text-xs text-on-surface-variant font-mono truncate"
                        title={fp}
                      >
                        {fp}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selectedPage.relatedPages && selectedPage.relatedPages.length > 0 && (
                <div>
                  <p className="text-xs text-on-surface-variant mb-1.5">相关页面</p>
                  <ul className="flex flex-col gap-1">
                    {selectedPage.relatedPages.map((relId) => {
                      const relIdx = pageList.findIndex((p) => p.id === relId);
                      const relTitle = relIdx !== -1 ? pageList[relIdx].title : relId;
                      return (
                        <li key={relId}>
                          <button
                            onClick={() => relIdx !== -1 && void selectPage(relIdx)}
                            disabled={relIdx === -1}
                            className="text-xs text-primary hover:underline text-left disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {relTitle}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <div className="flex flex-col items-center gap-4 text-center">
            <BookOpen size={48} className="text-on-surface-variant/30" />
            {repo.status === "running" ? (
              <>
                <p className="text-on-surface-variant text-sm">
                  Wiki 正在生成中 {repo.progress > 0 ? `(${repo.progress}%)` : ""}，请稍候…
                </p>
                <RefreshCw size={20} className="animate-spin text-primary" />
              </>
            ) : repo.status === "failed" ? (
              <p className="text-error text-sm">生成失败，请重试</p>
            ) : (
              <p className="text-on-surface-variant text-sm">
                点击右上角「生成 Wiki」开始分析仓库
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

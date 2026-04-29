"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, AlertTriangle, BookOpen, ChevronLeft, ChevronRight, Loader2, X, FileCode, ArrowLeft } from "lucide-react";
import Link from "next/link";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { PluggableList } from "unified";
import GlassPanel from "@/components/ui/GlassPanel";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import WikiTOCSidebar from "@/components/ui/WikiTOCSidebar";
import { api } from "@/lib/api";
import type { WikiResponse, WikiStatus, WikiData, FileSlice } from "@/lib/types";

// rehypeRaw parses raw HTML (<details>, <summary>), then rehypeSanitize
// applies GitHub-level allowlist: blocks script/iframe/event handlers,
// permits standard tags + details/summary.
const wikiRehypePlugins: PluggableList = [rehypeRaw, [rehypeSanitize, defaultSchema]];

interface WikiViewerProps {
  /** Task ID — required in task-centric mode, omit for repo-centric mode. */
  taskId?: string;
  /** Repo ID — enables per-page regeneration; when provided without taskId, uses repo-centric wiki APIs. */
  repoId?: string;
  /** Repo name — needed in repo-centric mode for GitNexus file lookups. */
  repoName?: string;
  /** When true: full-page layout, anchor links scroll in-page. When false (default): embedded layout, anchor links open standalone page. */
  standalone?: boolean;
  /** Called whenever the visible wiki page changes, with the page's associated file paths. */
  onPageChange?: (pageId: string, filePaths: string[]) => void;
}

interface FilePanel {
  path: string;
  targetStart?: number;
  targetEnd?: number;
  slice: FileSlice | null;
  loading: boolean;
  error: string | null;
}

// sessionStorage key for persisting "generating" state across tab switches
function wikiGenKey(taskId?: string, repoId?: string) {
  return `wiki_generating_${taskId ?? repoId ?? ""}`;
}

export default function WikiViewer({ taskId, repoId, repoName, standalone = false, onPageChange }: WikiViewerProps) {
  const isRepoMode = !taskId && !!repoId;
  const [wiki, setWiki] = useState<WikiData | null>(null);
  const [status, setStatus] = useState<"loading" | "not_generated" | "generating" | "ready" | "error">(() => {
    if (typeof window !== "undefined" && sessionStorage.getItem(wikiGenKey(taskId, repoId))) return "generating";
    return "loading";
  });
  const [stale, setStale] = useState(false);
  const [currentPageId, setCurrentPageId] = useState<string | undefined>();
  const [genStatus, setGenStatus] = useState<WikiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [filePanel, setFilePanel] = useState<FilePanel | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // Use a ref for onPageChange to avoid adding it to the effect deps (prevents loop if parent
  // forgets useCallback).  Update inside useEffect to satisfy react-hooks/refs lint rule.
  const onPageChangeRef = useRef(onPageChange);
  useEffect(() => { onPageChangeRef.current = onPageChange; });

  const loadWiki = useCallback(async () => {
    try {
      const resp: WikiResponse = isRepoMode
        ? await api.repos.wiki.get(repoId!)
        : await api.wiki.get(taskId!);
      if (resp.status === "ready" && resp.wiki) {
        setWiki(resp.wiki);
        setStale(resp.stale);
        setStatus("ready");
        sessionStorage.removeItem(wikiGenKey(taskId, repoId));
        const pages = resp.wiki.wiki_structure.pages;
        setCurrentPageId((prev) => {
          if (prev) return prev;
          // Resolve hash to a page ID on first load (standalone mode only)
          if (standalone) {
            const hash = window.location.hash;
            if (hash) {
              const targetId = hash.slice(1);
              const matched = pages.find((p) => p.id === targetId);
              if (matched) return matched.id;
            }
          }
          return pages[0]?.id;
        });
      } else {
        const gs = isRepoMode
          ? await api.repos.wiki.status(repoId!)
          : await api.wiki.status(taskId!);
        if (gs.running) {
          setStatus("generating");
          setGenStatus(gs);
        } else {
          setStatus("not_generated");
          sessionStorage.removeItem(wikiGenKey(taskId, repoId));
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load wiki");
      setStatus("error");
      sessionStorage.removeItem(wikiGenKey(taskId, repoId));
    }
  }, [taskId, repoId, isRepoMode, standalone]);

  useEffect(() => {
    void (async () => {
      await loadWiki();
    })();
  }, [loadWiki]);

  // Poll generation status
  useEffect(() => {
    if (status !== "generating") {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        const gs = isRepoMode
          ? await api.repos.wiki.status(repoId!)
          : await api.wiki.status(taskId!);
        setGenStatus(gs);
        if (!gs.running) {
          if (gs.error) {
            setError(gs.error);
            setStatus("error");
            sessionStorage.removeItem(wikiGenKey(taskId, repoId));
          } else {
            loadWiki();
          }
        }
      } catch {
        // ignore polling errors
      }
    };

    pollRef.current = setInterval(poll, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [status, taskId, repoId, isRepoMode, loadWiki]);

  const handleGenerate = async (forceRefresh = false) => {
    try {
      setStatus("generating");
      setGenStatus({ running: true, current: 0, total: 0, page_title: "", error: null });
      sessionStorage.setItem(wikiGenKey(taskId, repoId), "1");
      if (isRepoMode) {
        await api.repos.wiki.generate(repoId!, true, forceRefresh);
      } else {
        await api.wiki.generate(taskId!, true, forceRefresh);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start generation");
      setStatus("error");
    }
  };

  const handlePageSelect = (pageId: string, hash?: string) => {
    setCurrentPageId(pageId);
    if (hash) {
      setTimeout(() => {
        document.getElementById(hash)?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 150);
    } else {
      contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const handleCitationClick = useCallback(
    async (file: string, start?: number, end?: number) => {
      if (file.startsWith("citation-")) {
        setFilePanel({
          path: file,
          targetStart: start,
          targetEnd: end,
          slice: null,
          loading: false,
          error: "该引用不支持源码预览",
        });
        return;
      }
      setFilePanel({ path: file, targetStart: start, targetEnd: end, slice: null, loading: true, error: null });
      try {
        const slice = isRepoMode && repoName
          ? await api.gitnexus.getFile(repoName, file, start, end)
          : await api.tasks.getFile(taskId!, file, start, end);
        setFilePanel({ path: file, targetStart: start, targetEnd: end, slice, loading: false, error: null });
      } catch (e) {
        setFilePanel({
          path: file,
          targetStart: start,
          targetEnd: end,
          slice: null,
          loading: false,
          error: e instanceof Error ? e.message : "无法加载文件",
        });
      }
    },
    [taskId, isRepoMode, repoName],
  );

  const handleRegeneratePage = async () => {
    if (!repoId || !currentPageId || !currentPage) return;
    setRegenerating(true);
    try {
      const result = await api.repos.wiki.regeneratePage(
        repoId,
        currentPageId,
        currentPage.title,
        currentPage.filePaths,
      );
      setWiki((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          generated_pages: {
            ...prev.generated_pages,
            [currentPageId]: { ...prev.generated_pages[currentPageId], content: result.content },
          },
        };
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "页面重新生成失败");
    } finally {
      setRegenerating(false);
    }
  };

  // Page navigation
  const pages = wiki?.wiki_structure.pages ?? [];
  const currentIndex = pages.findIndex((p) => p.id === currentPageId);
  const prevPage = currentIndex > 0 ? pages[currentIndex - 1] : null;
  const nextPage = currentIndex < pages.length - 1 ? pages[currentIndex + 1] : null;
  const currentPage = currentPageId ? wiki?.generated_pages[currentPageId] : null;

  // Notify parent whenever the current page (or its data) changes.
  useEffect(() => {
    if (currentPageId && wiki) {
      const page = wiki.generated_pages[currentPageId];
      if (page) {
        onPageChangeRef.current?.(currentPageId, page.filePaths);
      }
    }
  }, [currentPageId, wiki]);

  // Consume URL hash for heading anchors — only in standalone mode.
  useEffect(() => {
    if (!currentPage || !standalone) return;
    const hash = window.location.hash;
    if (!hash) return;
    const targetId = hash.slice(1);
    const isPageId = wiki?.wiki_structure.pages.some((p) => p.id === targetId);
    if (isPageId) return;
    const timer = setTimeout(() => {
      document.getElementById(targetId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 150);
    return () => clearTimeout(timer);
  }, [currentPage, standalone, wiki]);

  // ── Not generated state ──
  if (status === "loading") {
    return (
      <GlassPanel className="py-16 flex items-center justify-center">
        <Loader2 size={24} className="animate-spin text-primary/50" />
      </GlassPanel>
    );
  }

  if (status === "error") {
    return (
      <GlassPanel className="py-12 flex flex-col items-center gap-4">
        <AlertTriangle size={32} className="text-tertiary/60" />
        <p className="text-sm text-tertiary">{error}</p>
        <button
          onClick={() => { setError(null); setStatus("loading"); loadWiki(); }}
          className="text-xs text-primary hover:text-primary-fixed transition-colors"
        >
          重试
        </button>
      </GlassPanel>
    );
  }

  if (status === "not_generated") {
    return (
      <GlassPanel className="py-16 flex flex-col items-center gap-6">
        <div className="p-4 rounded-full bg-primary/5">
          <BookOpen size={32} className="text-primary/50" />
        </div>
        <div className="text-center space-y-2">
          <p className="text-sm text-on-surface">尚未生成结构化 Wiki 文档</p>
          <p className="text-xs text-on-surface-variant/50 max-w-md">
            Wiki 会将仓库分析为多个章节页面，包含架构图、代码引用和导航目录。
          </p>
        </div>
        <button
          onClick={() => handleGenerate()}
          className="px-6 py-2.5 bg-primary text-on-primary text-sm font-bold rounded-lg shadow-lg shadow-primary/20 hover:bg-primary/90 transition-colors"
        >
          生成 Wiki 文档
        </button>
      </GlassPanel>
    );
  }

  if (status === "generating") {
    const pct = genStatus && genStatus.total > 0
      ? Math.round((genStatus.current / genStatus.total) * 100)
      : 0;
    return (
      <GlassPanel className="py-16 flex flex-col items-center gap-6">
        <div className="relative">
          <div className="w-16 h-16 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
        <div className="text-center space-y-2">
          <p className="text-sm text-on-surface font-display">
            Wiki 生成中...
          </p>
          {genStatus && genStatus.total > 0 && (
            <>
              <p className="text-xs text-on-surface-variant/70 font-data">
                {genStatus.current}/{genStatus.total} 页 — {genStatus.page_title}
              </p>
              <div className="w-64 h-1.5 bg-surface-container-high rounded-full overflow-hidden mt-3">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-500"
                  style={{ width: `${Math.max(5, pct)}%` }}
                />
              </div>
            </>
          )}
          {(!genStatus || genStatus.total === 0) && (
            <p className="text-xs text-on-surface-variant/50 italic">
              正在分析仓库结构，确定章节...
            </p>
          )}
        </div>
      </GlassPanel>
    );
  }

  // ── Ready state: wiki viewer ──
  if (!wiki) return null;

  return (
    <div className={`flex gap-0 min-h-[500px] ${standalone ? "h-screen bg-transparent" : "h-[calc(100vh-20rem)]"}`}>
      {/* TOC Sidebar — collapsible */}
      <div
        className={`shrink-0 bg-[#0D0D0F]/40 backdrop-blur-md border-r border-white/5 overflow-y-auto rounded-l-xl transition-all duration-500 ease-in-out flex flex-col ${
          sidebarOpen ? "w-64 opacity-100 p-5" : "w-0 opacity-0 p-0 border-none overflow-hidden"
        }`}
      >
        {standalone && (
          <Link
            href={isRepoMode ? `/repos/${repoId}` : `/tasks/${taskId}`}
            className="flex items-center gap-2 text-[10px] text-on-surface-variant/40 hover:text-primary transition-colors mb-6 group shrink-0"
          >
            <ArrowLeft size={12} className="group-hover:-translate-x-0.5 transition-transform" />
            {isRepoMode ? "返回仓库" : "返回分析任务"}
          </Link>
        )}

        <h3 className="font-display text-sm font-bold text-on-surface mb-1 truncate whitespace-nowrap">
          {wiki.wiki_structure.title}
        </h3>
        <p className="text-[10px] text-on-surface-variant/50 mb-4 line-clamp-2">
          {wiki.wiki_structure.description}
        </p>

        <WikiTOCSidebar
          structure={wiki.wiki_structure}
          currentPageId={currentPageId}
          onPageSelect={handlePageSelect}
        />

        {stale && (
          <div className="mt-4 p-2.5 rounded-lg bg-tertiary/5 border border-tertiary/20 shrink-0">
            <p className="text-[10px] text-tertiary/80 mb-2 flex items-center gap-1">
              <AlertTriangle size={10} />
              内容可能已过期
            </p>
            <button
              onClick={() => handleGenerate(true)}
              className="text-[10px] text-primary hover:text-primary-fixed flex items-center gap-1 transition-colors"
            >
              <RefreshCw size={10} />
              重新生成
            </button>
          </div>
        )}

        {!stale && (
          <button
            onClick={() => handleGenerate(true)}
            className="mt-4 w-full shrink-0 flex items-center justify-center gap-1.5 text-[10px] text-on-surface-variant/50 hover:text-primary py-2 rounded-lg border border-white/5 bg-white/[0.02] hover:bg-primary/10 transition-colors"
          >
            <RefreshCw size={10} />
            刷新 Wiki
          </button>
        )}
      </div>

      {/* TOC toggle strip (Nerve Slider) */}
      <button
        onClick={() => setSidebarOpen((v) => !v)}
        className="group relative shrink-0 flex flex-col items-center justify-center w-5 bg-transparent hover:bg-primary/5 transition-colors z-10 cursor-pointer"
        title={sidebarOpen ? "收起目录" : "展开目录"}
      >
        <div className="absolute inset-y-0 w-[1px] bg-white/5 group-hover:bg-primary/30 transition-colors" />
        <div className="h-10 w-[3px] rounded-full bg-white/20 group-hover:bg-primary/80 group-hover:shadow-[0_0_12px_#A4E6FF80] transition-all duration-300 ease-out flex items-center justify-center" />
      </button>

      {/* Page Content */}
      <div ref={contentRef} className="flex-1 overflow-y-auto p-12 min-w-0 relative scroll-smooth custom-scrollbar">
        {currentPage ? (
          <div className={`mx-auto transition-all duration-700 ease-[cubic-bezier(0.2,0,0,1)] ${
            sidebarOpen
              ? (filePanel ? "max-w-3xl" : "max-w-4xl")
              : (filePanel ? "max-w-4xl" : "max-w-5xl")
          }`}>
            {/* Per-page regenerate button (repo-centric only) */}
            {repoId && (
              <div className="flex justify-end mb-4">
                <button
                  onClick={handleRegeneratePage}
                  disabled={regenerating}
                  className="flex items-center gap-1.5 text-[11px] text-on-surface-variant/50 hover:text-primary disabled:opacity-40 disabled:cursor-not-allowed py-1.5 px-3 rounded-lg border border-white/5 bg-white/[0.02] hover:bg-primary/10 transition-colors"
                >
                  {regenerating ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <RefreshCw size={11} />
                  )}
                  重新生成此页
                </button>
              </div>
            )}
            <div className="prose prose-invert prose-slate max-w-none prose-headings:font-display prose-headings:tracking-tight prose-headings:font-bold prose-p:text-on-surface/80 prose-p:leading-relaxed prose-a:text-primary hover:prose-a:text-primary-fixed prose-pre:bg-white/[0.03] prose-pre:border prose-pre:border-white/5 prose-hr:border-white/5">
              <MarkdownRenderer
                content={currentPage.content}
                rehypePlugins={wikiRehypePlugins}
                anchorBaseUrl={standalone ? undefined : (isRepoMode ? `/repos/${repoId}/wiki` : `/tasks/${taskId}/wiki`)}
                onCitationClick={handleCitationClick}
                enableNumericCitations={false}
              />
            </div>

            {/* Related pages */}
            {currentPage.relatedPages.length > 0 && (
              <div className="mt-12 pt-6 border-t border-white/5">
                <p className="text-[10px] uppercase tracking-widest text-on-surface-variant/30 font-bold mb-3">
                  相关页面
                </p>
                <div className="flex flex-wrap gap-2">
                  {currentPage.relatedPages.map((relId) => {
                    const relPage = wiki.wiki_structure.pages.find((p) => p.id === relId);
                    if (!relPage) return null;
                    return (
                      <button
                        key={relId}
                        onClick={() => handlePageSelect(relId)}
                        className="text-xs px-3 py-1.5 rounded-lg bg-white/[0.03] text-on-surface-variant hover:text-primary hover:bg-primary/10 border border-white/5 transition-colors"
                      >
                        {relPage.title}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Prev / Next */}
            <div className="mt-12 pt-8 border-t border-white/5 flex justify-between items-center pb-16">
              {prevPage ? (
                <button
                  onClick={() => handlePageSelect(prevPage.id)}
                  className="flex flex-col items-start gap-1 text-on-surface-variant hover:text-primary transition-colors group max-w-[45%]"
                >
                  <span className="text-[10px] uppercase tracking-widest text-on-surface-variant/30 font-bold flex items-center gap-1 transition-colors group-hover:text-primary/50">
                    <ChevronLeft size={10} /> 上一页
                  </span>
                  <span className="text-sm font-medium truncate w-full pl-3">{prevPage.title}</span>
                </button>
              ) : (
                <div />
              )}
              {nextPage ? (
                <button
                  onClick={() => handlePageSelect(nextPage.id)}
                  className="flex flex-col items-end gap-1 text-on-surface-variant hover:text-primary transition-colors group max-w-[45%] text-right"
                >
                  <span className="text-[10px] uppercase tracking-widest text-on-surface-variant/30 font-bold flex items-center gap-1 transition-colors group-hover:text-primary/50">
                    下一页 <ChevronRight size={10} />
                  </span>
                  <span className="text-sm font-medium truncate w-full pr-3">{nextPage.title}</span>
                </button>
              ) : (
                <div />
              )}
            </div>
          </div>
        ) : (
          <div className="h-full flex items-center justify-center">
            <p className="text-sm text-on-surface-variant/50 italic">
              从左侧目录选择一个页面
            </p>
          </div>
        )}
      </div>

      {/* File Citation Panel (Artifact Inspector) */}
      {filePanel && (
        <div className="w-[420px] shrink-0 border-l border-white/10 bg-[#0D0D0F]/90 backdrop-blur-2xl shadow-2xl flex flex-col overflow-hidden z-20 relative">
          {/* Glowing accent line */}
          <div className="absolute left-0 top-0 bottom-0 w-[1px] bg-gradient-to-b from-transparent via-primary/50 to-transparent opacity-50" />
          
          {/* Panel header */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-white/[0.02] shrink-0">
            <div className="p-1.5 rounded-md bg-secondary/10 border border-secondary/20">
              <FileCode size={14} className="text-secondary" />
            </div>
            <div className="flex-1 min-w-0">
              {(() => {
                // Use actualPath from the backend when available — it reflects the file
                // that was actually opened (basename-only citations get resolved to full path).
                const resolvedPath = filePanel.slice?.actualPath ?? filePanel.path;
                const resolvedName = resolvedPath.split("/").pop() ?? resolvedPath;
                const wasResolved =
                  filePanel.slice?.actualPath &&
                  filePanel.slice.actualPath !== filePanel.path;
                return (
                  <>
                    <div
                      className="text-xs font-data text-white/90 truncate"
                      title={resolvedPath}
                    >
                      {resolvedName}
                    </div>
                    <div
                      className="text-[10px] text-white/40 truncate mt-0.5"
                      title={resolvedPath}
                    >
                      {wasResolved ? resolvedPath : filePanel.path}
                    </div>
                  </>
                );
              })()}
            </div>
            <button
              onClick={() => setFilePanel(null)}
              className="shrink-0 p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/10 transition-colors"
            >
              <X size={14} />
            </button>
          </div>

          {/* Panel body */}
          <div className="flex-1 overflow-y-auto custom-scrollbar relative">
            {filePanel.loading && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-primary/50 bg-[#0D0D0F]/50 backdrop-blur-sm z-10">
                <Loader2 size={24} className="animate-spin" />
                <span className="text-[11px] font-data tracking-widest uppercase">INSPECTING...</span>
              </div>
            )}
            {filePanel.error && (
              <div className="p-6 text-xs text-tertiary/80 bg-tertiary/5 border-b border-tertiary/10 font-data">
                <AlertTriangle size={16} className="mb-2" />
                {filePanel.error}
              </div>
            )}
            {filePanel.slice && (() => {
              const startLine = Number.isFinite(filePanel.slice.startLine)
                ? filePanel.slice.startLine
                : 1;
              const lines = filePanel.slice.content.split("\n");

              return (
                <div className="text-[11px] font-data pb-8">
                  {startLine > 1 && (
                    <div className="sticky top-0 z-10 flex items-center justify-center py-1.5 text-[10px] text-white/30 bg-gradient-to-b from-[#0D0D0F] to-transparent backdrop-blur-md">
                      · · · {startLine - 1} lines above · · ·
                    </div>
                  )}
                  <pre className="py-2 text-white/70 overflow-x-auto whitespace-pre leading-[1.6]">
                    {lines.map((line, i) => {
                      const currentLine = startLine + i;
                      const isTarget = filePanel.targetStart
                        ? (currentLine >= filePanel.targetStart && currentLine <= (filePanel.targetEnd || filePanel.targetStart))
                        : false;

                      return (
                        <div
                          key={i}
                          className={`flex group px-4 transition-colors ${
                            isTarget
                              ? "bg-primary/[0.15] border-l-[2px] border-primary"
                              : "hover:bg-white/[0.03] border-l-[2px] border-transparent"
                          }`}
                        >
                          <span className={`select-none text-right w-10 shrink-0 pr-4 border-r border-white/5 transition-colors ${
                            isTarget ? "text-primary/80" : "text-white/20 group-hover:text-white/40"
                          }`}>
                            {currentLine}
                          </span>
                          <span className={`pl-4 ${isTarget ? "text-white" : ""}`}>{line || " "}</span>
                        </div>
                      );
                    })}
                  </pre>
                </div>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
}

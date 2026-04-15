"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, AlertTriangle, BookOpen, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { PluggableList } from "unified";
import GlassPanel from "@/components/ui/GlassPanel";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import WikiTOCSidebar from "@/components/ui/WikiTOCSidebar";
import { api } from "@/lib/api";
import type { WikiResponse, WikiStatus, WikiData } from "@/lib/types";

// rehypeRaw parses raw HTML (<details>, <summary>), then rehypeSanitize
// applies GitHub-level allowlist: blocks script/iframe/event handlers,
// permits standard tags + details/summary.
const wikiRehypePlugins: PluggableList = [rehypeRaw, [rehypeSanitize, defaultSchema]];

interface WikiViewerProps {
  taskId: string;
}

export default function WikiViewer({ taskId }: WikiViewerProps) {
  const [wiki, setWiki] = useState<WikiData | null>(null);
  const [status, setStatus] = useState<"loading" | "not_generated" | "generating" | "ready" | "error">("loading");
  const [stale, setStale] = useState(false);
  const [currentPageId, setCurrentPageId] = useState<string | undefined>();
  const [genStatus, setGenStatus] = useState<WikiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const loadWiki = useCallback(async () => {
    try {
      const resp: WikiResponse = await api.wiki.get(taskId);
      if (resp.status === "ready" && resp.wiki) {
        setWiki(resp.wiki);
        setStale(resp.stale);
        setStatus("ready");
        const firstPageId = resp.wiki.wiki_structure.pages[0]?.id;
        if (firstPageId) {
          setCurrentPageId((prev) => prev ?? firstPageId);
        }
      } else {
        const gs = await api.wiki.status(taskId);
        if (gs.running) {
          setStatus("generating");
          setGenStatus(gs);
        } else {
          setStatus("not_generated");
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load wiki");
      setStatus("error");
    }
  }, [taskId]);

  useEffect(() => {
    loadWiki();
  }, [loadWiki]);

  // Poll generation status
  useEffect(() => {
    if (status !== "generating") {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        const gs = await api.wiki.status(taskId);
        setGenStatus(gs);
        if (!gs.running) {
          if (gs.error) {
            setError(gs.error);
            setStatus("error");
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
  }, [status, taskId, loadWiki]);

  const handleGenerate = async (forceRefresh = false) => {
    try {
      setStatus("generating");
      setGenStatus({ running: true, current: 0, total: 0, page_title: "", error: null });
      await api.wiki.generate(taskId, true, forceRefresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start generation");
      setStatus("error");
    }
  };

  const handlePageSelect = (pageId: string) => {
    setCurrentPageId(pageId);
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  // Page navigation
  const pages = wiki?.wiki_structure.pages ?? [];
  const currentIndex = pages.findIndex((p) => p.id === currentPageId);
  const prevPage = currentIndex > 0 ? pages[currentIndex - 1] : null;
  const nextPage = currentIndex < pages.length - 1 ? pages[currentIndex + 1] : null;
  const currentPage = currentPageId ? wiki?.generated_pages[currentPageId] : null;

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
    <div className="flex gap-0 h-[calc(100vh-20rem)] min-h-[500px]">
      {/* TOC Sidebar */}
      <div className="w-64 shrink-0 bg-surface-container-lowest/30 rounded-l-xl border-r border-outline-variant/10 overflow-y-auto p-4">
        <h3 className="font-display text-sm font-bold text-on-surface mb-1 truncate">
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
          <div className="mt-4 p-2.5 rounded-lg bg-tertiary/5 border border-tertiary/20">
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
            className="mt-4 w-full flex items-center justify-center gap-1.5 text-[10px] text-on-surface-variant/50 hover:text-on-surface py-2 rounded-lg border border-outline-variant/10 hover:bg-surface-container-high/50 transition-colors"
          >
            <RefreshCw size={10} />
            刷新 Wiki
          </button>
        )}
      </div>

      {/* Page Content */}
      <div ref={contentRef} className="flex-1 overflow-y-auto p-8">
        {currentPage ? (
          <div className="max-w-3xl mx-auto">
            <div className="prose prose-invert prose-sm max-w-none prose-headings:font-display prose-headings:tracking-tight prose-a:text-primary hover:prose-a:text-primary-fixed">
              <MarkdownRenderer content={currentPage.content} rehypePlugins={wikiRehypePlugins} />
            </div>

            {/* Related pages */}
            {currentPage.relatedPages.length > 0 && (
              <div className="mt-8 pt-4 border-t border-outline-variant/20">
                <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-2">
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
                        className="text-xs px-3 py-1.5 rounded-lg bg-surface-container-high/50 text-on-surface-variant hover:text-primary hover:bg-primary/10 border border-outline-variant/10 transition-colors"
                      >
                        {relPage.title}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Prev / Next */}
            <div className="mt-8 flex justify-between items-center">
              {prevPage ? (
                <button
                  onClick={() => handlePageSelect(prevPage.id)}
                  className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-primary transition-colors group"
                >
                  <ChevronLeft size={16} className="group-hover:-translate-x-0.5 transition-transform" />
                  <span className="truncate max-w-[200px]">{prevPage.title}</span>
                </button>
              ) : (
                <div />
              )}
              {nextPage ? (
                <button
                  onClick={() => handlePageSelect(nextPage.id)}
                  className="flex items-center gap-2 text-sm text-on-surface-variant hover:text-primary transition-colors group"
                >
                  <span className="truncate max-w-[200px]">{nextPage.title}</span>
                  <ChevronRight size={16} className="group-hover:translate-x-0.5 transition-transform" />
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
    </div>
  );
}

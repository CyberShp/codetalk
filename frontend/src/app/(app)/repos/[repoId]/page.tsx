"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import GraphViewer from "@/components/ui/GraphViewer";
import GraphSearch from "@/components/ui/GraphSearch";
import CodePanel from "@/components/ui/CodePanel";
import IntelligencePanel from "@/components/ui/IntelligencePanel";
import GraphCatalog from "@/components/ui/GraphCatalog";
import WikiViewer from "@/components/ui/WikiViewer";
import FloatingChat from "@/components/ui/FloatingChat";
import ChatPanel from "@/components/ui/ChatPanel";
import { useChatEngine } from "@/hooks/useChatEngine";
import { usePageRestoreRefresh } from "@/hooks/usePageRestoreRefresh";
import { api } from "@/lib/api";
import type { RepoDetail, RepoGraphResponse, GraphNode, GraphData } from "@/lib/types";
import { ArrowLeft, MessageSquare, MessageSquareText, Sparkles, X, ShieldAlert } from "lucide-react";

type SearchMatch = { line_number: number; line_content: string };
type SearchFile = { file: string; repo: string; matches: SearchMatch[] };

function groupGrepResults(
  matches: Array<{ file: string; line: number; content: string }>,
): SearchFile[] {
  const byFile = new Map<string, SearchMatch[]>();
  for (const m of matches) {
    const arr = byFile.get(m.file) ?? [];
    arr.push({ line_number: m.line, line_content: m.content });
    byFile.set(m.file, arr);
  }
  return Array.from(byFile, ([file, ms]) => ({ file, repo: "", matches: ms }));
}

const INTELLIGENCE_LABELS = new Set(["Process", "Community", "Function", "Method", "Class"]);
const tabs = ["documentation", "graph", "search"] as const;
const GRAPH_RESTORE_REFRESH_MS = 5 * 60 * 1000;
type Tab = (typeof tabs)[number];

export default function RepoDetailPage() {
  const params = useParams();
  const repoId = params.repoId as string;
  const graphCacheKey = `repo-graph:${repoId}`;

  const [tab, setTab] = useState<Tab>("documentation");
  const [detail, setDetail] = useState<RepoDetail | null>(null);
  const [error, setError] = useState("");

  // Graph
  const [graphResp, setGraphResp] = useState<RepoGraphResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [previousProcess, setPreviousProcess] = useState<GraphNode | null>(null);

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchFile[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searchMode, setSearchMode] = useState<"zoekt" | "grep">("zoekt");

  // Wiki page context for chat scope
  const [wikiPageFilePaths, setWikiPageFilePaths] = useState<string[]>([]);
  const handleWikiPageChange = useCallback((_pageId: string, filePaths: string[]) => {
    setWikiPageFilePaths(filePaths);
  }, []);

  // Graph → Chat bridge
  const [graphChatOpen, setGraphChatOpen] = useState(false);
  const [graphChatContext, setGraphChatContext] = useState<{
    filePaths: string[];
    initialQuestion: string;
  } | null>(null);
  const handleAskAboutNode = useCallback((node: GraphNode) => {
    const filePaths = node.properties.filePath ? [node.properties.filePath as string] : [];
    const question = `请解释 ${node.properties.name} (${node.label}) 的实现逻辑、调用关系和设计意图。`;
    setGraphChatContext({ filePaths, initialQuestion: question });
    setGraphChatOpen(true);
  }, []);

  // Docked doc chat (documentation tab sidebar)
  const [showDocChat, setShowDocChat] = useState(false);
  const docChatEngine = useChatEngine({ repoId, currentPageFilePaths: wikiPageFilePaths });

  const gitnexusRepoKey =
    ((graphResp?.metadata as Record<string, unknown> | null)?.repo_name as string | undefined)
    ?? detail?.repo.id
    ?? "";
  const graphData = (graphResp?.graph as GraphData) ?? null;

  const nodeMap = useMemo(() => {
    if (!graphData) return new Map<string, GraphNode>();
    const m = new Map<string, GraphNode>();
    for (const n of graphData.nodes) m.set(n.id, n);
    return m;
  }, [graphData]);

  const handleNodeClick = useCallback((node: GraphNode | null) => {
    if (!node) { setSelectedNode(null); setPreviousProcess(null); return; }
    if (selectedNode?.label === "Process" && !INTELLIGENCE_LABELS.has(node.label)) {
      setPreviousProcess(selectedNode);
    } else if (INTELLIGENCE_LABELS.has(node.label)) {
      setPreviousProcess(null);
    }
    setSelectedNode(node);
  }, [selectedNode]);

  // Load repo detail
  useEffect(() => {
    api.repos.get(repoId).then(setDetail).catch((e) => setError(e instanceof Error ? e.message : "加载仓库失败"));
  }, [repoId]);

  useEffect(() => {
    setGraphResp(null);
    setGraphError("");
    setGraphLoading(false);
    setSelectedNode(null);
    setPreviousProcess(null);

    if (typeof window === "undefined") return;

    const cached = window.sessionStorage.getItem(graphCacheKey);
    if (!cached) return;
    try {
      const parsed = JSON.parse(cached) as RepoGraphResponse;
      if (parsed.graph) {
        setGraphResp(parsed);
      } else {
        window.sessionStorage.removeItem(graphCacheKey);
      }
    } catch {
      window.sessionStorage.removeItem(graphCacheKey);
    }
  }, [graphCacheKey]);

  const graphLoadingRef = useRef(false);
  const graphFetchedRef = useRef(false);
  const loadGraph = useCallback(async (force = false) => {
    if (!force && graphLoadingRef.current) return;
    graphLoadingRef.current = true;
    setGraphLoading(true);
    setGraphError("");
    try {
      const resp = await api.repos.graph.get(repoId);
      setGraphResp(resp);
      if (typeof window !== "undefined" && resp.graph) {
        window.sessionStorage.setItem(graphCacheKey, JSON.stringify(resp));
      }
    } catch (e) {
      setGraphError(e instanceof Error ? e.message : "加载图谱失败");
    } finally {
      graphLoadingRef.current = false;
      setGraphLoading(false);
    }
  }, [graphCacheKey, repoId]);

  // Reset fetch-attempted flag when leaving graph tab
  useEffect(() => {
    if (tab !== "graph") {
      graphFetchedRef.current = false;
    }
  }, [tab]);

  // Load graph on tab switch — retry once per tab visit
  useEffect(() => {
    if (tab !== "graph") return;
    if (graphResp?.graph && !graphError) return; // already have real data
    if (graphFetchedRef.current) return; // already tried this tab visit
    graphFetchedRef.current = true;
    void loadGraph();
  }, [tab, graphResp, graphError, loadGraph]);

  const restoreRefresh = useCallback(() => {
    if (tab !== "graph") return;
    const analyzedAtMs = graphResp?.analyzed_at ? Date.parse(graphResp.analyzed_at) : NaN;
    const graphFresh =
      !!graphResp?.graph &&
      Number.isFinite(analyzedAtMs) &&
      Date.now() - analyzedAtMs < GRAPH_RESTORE_REFRESH_MS;
    if (graphFresh && !graphError) return;
    void loadGraph(!!graphResp?.graph);
  }, [tab, graphResp, graphError, loadGraph]);
  usePageRestoreRefresh(restoreRefresh);

  const handleSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = searchQuery.trim();
    if (!q) return;
    setIsSearching(true);
    setSearchError("");
    try {
      if (searchMode === "grep") {
        const resp = await api.gitnexus.grep(q, gitnexusRepoKey || undefined);
        setSearchResults(groupGrepResults(resp.matches));
      } else {
        const resp = await api.repos.search(repoId, q);
        setSearchResults(resp.results);
      }
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setIsSearching(false);
    }
  };

  if (error) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <p className="text-tertiary">{error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <p className="text-on-surface-variant/50">加载仓库中...</p>
      </div>
    );
  }

  const showSidebar = tab === "graph" && (selectedNode || graphData);
  const isIntelligenceNode = selectedNode && INTELLIGENCE_LABELS.has(selectedNode.label);

  return (
    <div className="fixed inset-0 z-[80] bg-surface text-on-surface flex flex-col">
      {/* Fixed Header */}
      <div className="shrink-0 flex items-center gap-3 px-6 h-14 border-b border-outline-variant/10 bg-surface-container-lowest/80 backdrop-blur-md">
        <Link
          href="/assets"
          className="inline-flex h-9 items-center gap-2 rounded-full border border-outline-variant/20 bg-surface-container-low px-4 text-sm font-medium text-on-surface transition-colors hover:border-primary/30 hover:text-primary"
        >
          <ArrowLeft size={16} />
          返回
        </Link>
        <div className="h-6 w-px bg-outline-variant/20" />
        <h1 className="text-lg font-display font-bold text-on-surface truncate">{detail.repo.name}</h1>
        <span className="text-[10px] font-data text-on-surface-variant/40 shrink-0">{detail.repo.branch}</span>

        <div className="ml-auto flex items-center gap-2">
          <Link
            href={`/repos/${repoId}/analysis`}
            className="inline-flex items-center gap-2 rounded-full border border-tertiary/20 bg-tertiary/10 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-tertiary transition-colors hover:bg-tertiary/15"
          >
            <ShieldAlert size={12} />
            静态分析
          </Link>
          <Link
            href={`/repos/${repoId}/ask`}
            className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/15"
          >
            <Sparkles size={12} />
            全屏 AI 问答
          </Link>
          {tab === "documentation" && (
            <button
              onClick={() => setShowDocChat((v) => !v)}
              className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-[11px] font-bold uppercase tracking-widest transition-colors ${
                showDocChat
                  ? "border-secondary/40 bg-secondary/10 text-secondary hover:bg-secondary/15"
                  : "border-white/10 bg-white/5 text-on-surface-variant hover:bg-white/10"
              }`}
            >
              <MessageSquare size={12} />
              {showDocChat ? "收起 Chat" : "侧边 Chat"}
            </button>
          )}
        </div>
      </div>

      {/* Tabs bar */}
      <div className="shrink-0 flex items-center px-6 py-2 border-b border-outline-variant/5 bg-surface-container-lowest/50">
        <div className="flex gap-1 bg-surface-container-low/50 backdrop-blur-md rounded-xl p-1.5 border border-outline-variant/10 shadow-inner">
          {tabs.map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); if (t !== "graph") { setSelectedNode(null); setPreviousProcess(null); } }}
              className={`px-5 py-2 text-[11px] font-bold uppercase tracking-widest rounded-lg transition-all duration-200 ${
                tab === t
                  ? "bg-primary text-on-primary shadow-lg shadow-primary/20 scale-105"
                  : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
              }`}
            >
              {{ documentation: "文档", graph: "神经图谱", search: `搜索${searchResults?.length ? ` (${searchResults.length})` : ""}` }[t]}
            </button>
          ))}
        </div>
      </div>

      {/* Content — scrollable body */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
      <div className={`grid gap-8 transition-all duration-500 ease-in-out ${showSidebar ? "grid-cols-[1fr_520px]" : "grid-cols-1"}`}>
        <div className="space-y-6 min-w-0 animate-in fade-in duration-500">
          {/* Documentation */}
          {tab === "documentation" && (
            <div className={`grid gap-4 ${showDocChat ? "grid-cols-[1fr_420px]" : "grid-cols-1"}`}>
              <GlassPanel className="p-0 overflow-hidden min-w-0">
                <WikiViewer repoId={repoId} repoName={gitnexusRepoKey || undefined} onPageChange={handleWikiPageChange} />
              </GlassPanel>
              {showDocChat && (
                <GlassPanel className="p-0 overflow-hidden flex flex-col" style={{ height: "calc(100vh - 14rem)" }}>
                  <div className="relative h-11 shrink-0 flex items-center justify-between px-4 bg-black/40 border-b border-white/5">
                    <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-secondary/50 to-transparent" />
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-secondary shadow-lg shadow-secondary/60 animate-pulse" />
                      <h3 className="text-[10px] font-mono font-bold uppercase tracking-[0.2em] text-on-surface/70">
                        Neural Link <span className="text-secondary/50">Doc</span>
                      </h3>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[9px] font-mono text-on-surface-variant/40 italic">
                        {wikiPageFilePaths.length > 0 ? `${wikiPageFilePaths.length} files in scope` : "GLOBAL_CONTEXT"}
                      </span>
                      <button onClick={() => setShowDocChat(false)} className="p-1 rounded-md text-on-surface-variant hover:text-on-surface hover:bg-white/5 transition-all">
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                  <ChatPanel engine={docChatEngine} repoId={repoId} className="flex-1 min-h-0" />
                </GlassPanel>
              )}
            </div>
          )}

          {/* Graph */}
          {tab === "graph" && (
            <div className="space-y-3">
              {/* Symbol search — normal flow above graph */}
              <GraphSearch
                repo={gitnexusRepoKey || undefined}
                onNodeSelect={(nodeId) => { const node = nodeMap.get(nodeId); if (node) handleNodeClick(node); }}
                selectedNodeId={selectedNode?.id ?? null}
                className="relative w-full backdrop-blur-md bg-surface/80 border border-outline-variant/15 rounded-xl shadow-lg shadow-black/20 overflow-hidden"
              />
              {graphData ? (
                <div className="h-[calc(100vh-16rem)]">
                  <GraphViewer
                    nodes={graphData.nodes}
                    edges={graphData.edges}
                    selectedNodeId={selectedNode?.id ?? null}
                    onNodeClick={handleNodeClick}
                  />
                </div>
              ) : graphError ? (
                <GlassPanel className="py-16 flex flex-col items-center justify-center text-on-surface-variant/60 gap-4">
                  <p className="text-sm">{graphError}</p>
                  <button
                    onClick={() => { void loadGraph(true); }}
                    className="px-4 py-2 rounded-lg border border-primary/20 bg-primary/10 text-primary text-xs font-bold uppercase tracking-widest hover:bg-primary/15 transition-colors"
                  >
                    重试加载
                  </button>
                </GlassPanel>
              ) : (
                <GlassPanel className="py-32 flex flex-col items-center justify-center text-on-surface-variant/50 gap-2">
                  <p className="text-sm italic">
                    {graphLoading
                      ? "正在恢复 GitNexus 图谱..."
                      : graphResp?.status === "not_analyzed"
                      ? "尚未执行 GitNexus 分析。请先创建分析任务。"
                      : "加载图谱数据中..."}
                  </p>
                </GlassPanel>
              )}
            </div>
          )}

          {/* Search */}
          {tab === "search" && (
            <div className="space-y-6">
              <div className="space-y-2">
                <div className="flex bg-surface-container-low/50 p-0.5 rounded-md border border-outline-variant/10 w-fit">
                  <button
                    onClick={() => { setSearchMode("zoekt"); setSearchResults(null); }}
                    className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded ${searchMode === "zoekt" ? "bg-surface-container-high text-primary" : "text-on-surface-variant"}`}
                  >
                    全文
                  </button>
                  <button
                    onClick={() => { setSearchMode("grep"); setSearchResults(null); }}
                    className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded ${searchMode === "grep" ? "bg-surface-container-high text-primary" : "text-on-surface-variant"}`}
                  >
                    正则
                  </button>
                </div>
                <form onSubmit={handleSearch} className="flex gap-2">
                  <div className="relative flex-1 group">
                    <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-on-surface-variant/40 group-focus-within:text-primary transition-colors">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                      </svg>
                    </div>
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder={searchMode === "grep" ? "输入正则表达式..." : "输入关键词搜索代码..."}
                      className="w-full h-11 bg-surface-container-low border border-outline-variant/30 rounded-xl pl-10 pr-4 text-sm font-data text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:border-primary/50 focus:ring-4 focus:ring-primary/5 transition-all"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={isSearching || !searchQuery.trim()}
                    className="h-11 px-6 bg-primary text-on-primary text-xs font-bold uppercase tracking-widest rounded-xl hover:shadow-lg hover:shadow-primary/20 disabled:opacity-50 transition-all"
                  >
                    {isSearching ? "搜索中..." : "搜索"}
                  </button>
                </form>
              </div>

              {searchError && (
                <GlassPanel className="bg-tertiary-container/20 border-tertiary/30 py-3">
                  <p className="text-sm text-tertiary">{searchError}</p>
                </GlassPanel>
              )}

              {searchResults ? (
                searchResults.length === 0 ? (
                  <GlassPanel className="py-16 flex items-center justify-center">
                    <p className="text-sm text-on-surface-variant/50">未找到匹配结果。</p>
                  </GlassPanel>
                ) : (
                  <div className="space-y-4">
                    <div className="flex items-center gap-2 text-xs text-on-surface-variant/60 font-data">
                      <span className="text-primary font-bold">{searchResults.length}</span> 个文件匹配
                      <span className="bg-surface-container-high px-2 py-0.5 rounded font-mono text-on-surface">「{searchQuery}」</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ml-auto uppercase tracking-wider font-bold border ${
                        searchMode === "grep" ? "bg-primary/10 text-primary border-primary/20" : "bg-secondary/10 text-secondary border-secondary/20"
                      }`}>
                        {searchMode === "grep" ? "正则匹配" : "全文搜索"}
                      </span>
                    </div>
                    {searchResults.map((file, fi) => (
                      <GlassPanel key={fi} className="p-0 overflow-hidden">
                        <div className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high/50 border-b border-outline-variant/10">
                          <span className="font-mono text-xs text-secondary truncate">{file.file}</span>
                          <span className="text-[10px] text-on-surface-variant/40 shrink-0">{file.matches.length} 处</span>
                        </div>
                        <div className="divide-y divide-outline-variant/10">
                          {file.matches.map((m, mi) => (
                            <div key={mi} className="flex gap-3 px-4 py-2 hover:bg-surface-container-high/30 transition-colors">
                              <span className="shrink-0 w-10 text-right text-[10px] font-data text-on-surface-variant/30 pt-0.5">{m.line_number}</span>
                              <code className="text-xs font-mono text-on-surface/80 whitespace-pre-wrap break-all leading-relaxed">{m.line_content}</code>
                            </div>
                          ))}
                        </div>
                      </GlassPanel>
                    ))}
                  </div>
                )
              ) : (
                <GlassPanel className="py-16 flex items-center justify-center">
                  <p className="text-sm text-on-surface-variant/40 italic">输入关键词开始搜索代码。</p>
                </GlassPanel>
              )}
            </div>
          )}
        </div>

        {/* Graph sidebar */}
        {showSidebar && (
          <div className="sticky top-6 h-[calc(100vh-10rem)] overflow-y-auto animate-in slide-in-from-right-8 duration-500 space-y-2">
            {selectedNode ? (
              <>
                {previousProcess && !isIntelligenceNode && (
                  <button
                    onClick={() => { setSelectedNode(previousProcess); setPreviousProcess(null); }}
                    className="flex items-center gap-2 w-full px-3 py-2.5 text-xs text-on-surface-variant hover:text-primary transition-all rounded-lg bg-surface-container-low hover:bg-surface-container-high border border-outline-variant/20 group"
                  >
                    <span className="group-hover:-translate-x-0.5 transition-transform inline-block shrink-0 text-base leading-none">&larr;</span>
                    <span className="truncate">返回：{previousProcess.properties.name}</span>
                  </button>
                )}
                {isIntelligenceNode ? (
                  <IntelligencePanel node={selectedNode} nodeMap={nodeMap} edges={graphData?.edges ?? []} onNodeClick={handleNodeClick} repo={gitnexusRepoKey || undefined} />
                ) : (
                  <CodePanel node={selectedNode} repoName={gitnexusRepoKey} />
                )}
                <button
                  onClick={() => handleAskAboutNode(selectedNode)}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-primary/20 bg-primary/5 text-primary text-[11px] font-bold uppercase tracking-widest hover:bg-primary/10 hover:border-primary/40 transition-all"
                >
                  <MessageSquareText size={13} />
                  在 Chat 中追问
                </button>
              </>
            ) : (
              <GraphCatalog
                repo={gitnexusRepoKey || undefined}
                nodeMap={nodeMap}
                onNodeClick={handleNodeClick}
              />
            )}
          </div>
        )}
      </div>

      </div>{/* end scrollable body */}

      <FloatingChat
        repoId={repoId}
        currentPageFilePaths={
          tab === "documentation" ? wikiPageFilePaths
          : tab === "graph" && graphChatContext ? graphChatContext.filePaths
          : undefined
        }
        hidden={tab === "documentation" && showDocChat}
        forceOpen={graphChatOpen}
        initialMessage={graphChatContext?.initialQuestion}
        onClose={() => { setGraphChatOpen(false); setGraphChatContext(null); }}
      />
    </div>
  );
}

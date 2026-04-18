"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import GraphViewer from "@/components/ui/GraphViewer";
import GraphSearch from "@/components/ui/GraphSearch";
import CodePanel from "@/components/ui/CodePanel";
import IntelligencePanel from "@/components/ui/IntelligencePanel";
import WikiViewer from "@/components/ui/WikiViewer";
import FloatingChat from "@/components/ui/FloatingChat";
import ChatPanel from "@/components/ui/ChatPanel";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { api } from "@/lib/api";
import { useChatEngine } from "@/hooks/useChatEngine";
import type { TaskDetail, GraphNode, GraphData } from "@/lib/types";
import { ArrowLeft, ChevronDown, ChevronUp, Bot, Sparkles, MessageSquareText, MessageSquare, X } from "lucide-react";

const KEY_PROCESS_LIMIT = 10;

function scoreProcess(p: GraphNode): number {
  const isCross = p.properties.processType === "cross_community";
  const steps = p.properties.stepCount ?? 0;
  return (isCross ? 100 : 0) + steps;
}

const detailTabs = ["documentation", "graph", "findings", "search", "ai_summary"] as const;
type Tab = (typeof detailTabs)[number];

const INTELLIGENCE_LABELS = new Set(["Process", "Community", "Function", "Method", "Class"]);

export default function TaskDetailPage() {
  const params = useParams();
  const router = useRouter();
  const taskId = params.id as string;
  const [tab, setTab] = useState<Tab>("documentation");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [showOtherProcesses, setShowOtherProcesses] = useState(false);
  const [previousProcess, setPreviousProcess] = useState<GraphNode | null>(null);

  // Interactive search state (search tab)
  const [customSearchQuery, setCustomSearchQuery] = useState("");
  const [lastExecutedQuery, setLastExecutedQuery] = useState("");
  const [interactiveResults, setInteractiveResults] = useState<SearchFile[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  // Wiki page context: file paths of the currently viewed wiki page
  const [wikiPageFilePaths, setWikiPageFilePaths] = useState<string[]>([]);
  const handleWikiPageChange = useCallback((_pageId: string, filePaths: string[]) => {
    setWikiPageFilePaths(filePaths);
  }, []);

  // Docked chat toggle (documentation tab)
  const [showDocChat, setShowDocChat] = useState(false);
  const docChatEngine = useChatEngine({
    repoId: task?.repository_id ?? "",
    currentPageFilePaths: wikiPageFilePaths,
  });

  // Derive graph data — safe with task=null (useMemo must run unconditionally)
  const graphRun = task?.tool_runs.find((r) => r.tool_name === "gitnexus");
  const graphData = (graphRun?.result?.graph as GraphData) ?? null;
  const repoName = task?.repository_name
    ?? ((graphRun?.result?.metadata as Record<string, unknown>)?.repo_name as string | undefined)
    ?? "Repository";

  // Zoekt search results
  const zoektRun = task?.tool_runs.find((r) => r.tool_name === "zoekt");
  type SearchMatch = { line_number: number; line_content: string };
  type SearchFile = { file: string; repo: string; matches: SearchMatch[] };
  const searchResults = (zoektRun?.result?.search_results as SearchFile[]) ?? [];
  const searchQuery = (zoektRun?.result?.query as string) ?? "";
  const zoektIndexedOnly = zoektRun?.result?.indexed === true && !searchQuery;

  // Sync customSearchQuery with searchQuery when task loads
  useEffect(() => {
    if (searchQuery && !customSearchQuery && !interactiveResults) {
      setCustomSearchQuery(searchQuery);
    }
  }, [searchQuery, customSearchQuery, interactiveResults]);

  // Build node lookup for resolving step symbolIds to names
  const nodeMap = useMemo(() => {
    if (!graphData) return new Map<string, GraphNode>();
    const m = new Map<string, GraphNode>();
    for (const n of graphData.nodes) m.set(n.id, n);
    return m;
  }, [graphData]);


  // Score, rank, and split processes into key vs other
  const { keyProcesses, otherProcesses } = useMemo(() => {
    const all = graphData?.processes ?? [];
    const scored = all.map((p) => ({ node: p, score: scoreProcess(p) }));
    scored.sort((a, b) => b.score - a.score);
    const key = scored.slice(0, KEY_PROCESS_LIMIT).map((s) => s.node);
    const other = scored.slice(KEY_PROCESS_LIMIT).map((s) => s.node);
    return { keyProcesses: key, otherProcesses: other };
  }, [graphData]);

  // Track breadcrumb: sticky — only cleared on explicit return or new intelligence node
  const handleNodeClick = useCallback((node: GraphNode | null) => {
    if (!node) {
      setSelectedNode(null);
      setPreviousProcess(null);
      return;
    }
    if (selectedNode?.label === "Process" && !INTELLIGENCE_LABELS.has(node.label)) {
      // Process → code step: save the process as breadcrumb anchor
      setPreviousProcess(selectedNode);
    } else if (INTELLIGENCE_LABELS.has(node.label)) {
      // Selecting a new intelligence node: reset breadcrumb
      setPreviousProcess(null);
    }
    // code → code: leave previousProcess untouched (sticky breadcrumb)
    setSelectedNode(node);
  }, [selectedNode]);

  useEffect(() => {
    if (!taskId) return;
    const load = async () => {
      try {
        const data = await api.tasks.get(taskId);
        setTask(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load task");
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [taskId]);

  const handleDelete = async () => {
    try {
      await api.tasks.delete(taskId);
      router.push("/tasks");
    } catch (e) {
      console.error("Failed to delete task:", e);
    }
  };

  if (error) {
    return (
      <div className="fixed inset-0 z-[80] flex items-center justify-center bg-surface p-6">
        <p className="text-tertiary">{error}</p>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="fixed inset-0 z-[80] flex items-center justify-center bg-surface p-6">
        <p className="text-on-surface-variant/50">加载任务中...</p>
      </div>
    );
  }

  const showSidebar = tab === "graph" && selectedNode;
  const isIntelligenceNode = selectedNode && INTELLIGENCE_LABELS.has(selectedNode.label);

  const handleSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!customSearchQuery.trim() || !task?.repository_id) return;

    setIsSearching(true);
    setSearchError("");
    const executedQuery = customSearchQuery.trim();
    try {
      const resp = await api.repos.search(task.repository_id, executedQuery);
      setLastExecutedQuery(executedQuery);
      setInteractiveResults(resp.results);
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setIsSearching(false);
    }
  };

  const currentSearchResults = interactiveResults ?? searchResults;
  const currentSearchQuery = interactiveResults !== null ? lastExecutedQuery : searchQuery;

  return (
    <div className="fixed inset-0 z-[80] bg-surface text-on-surface">
      <header className="fixed inset-x-0 top-0 z-[85] flex h-16 items-center justify-between border-b border-outline-variant/20 bg-surface/95 px-6 backdrop-blur-md">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/tasks"
            className="inline-flex h-10 items-center gap-2 rounded-full border border-outline-variant/20 bg-surface-container-low px-4 text-sm font-medium text-on-surface transition-colors hover:border-primary/30 hover:text-primary"
          >
            <ArrowLeft size={16} />
            返回
          </Link>
          <div className="h-6 w-px bg-outline-variant/20" />
          <Link
            href="/dashboard"
            className="text-[11px] font-black uppercase tracking-[0.35em] text-on-surface hover:text-primary"
          >
            CODETALKS
          </Link>
          <span className="truncate text-sm text-on-surface-variant">{repoName}</span>
          <span className="rounded-full border border-outline-variant/20 px-2 py-0.5 font-mono text-[10px] text-on-surface-variant/60">
            {task.id.slice(0, 8)}
          </span>
        </div>

        <div className="flex items-center gap-3">
          <StatusBadge status={task.status as "running" | "completed" | "failed" | "pending"} />
          <Link
            href={`/tasks/${taskId}/ask`}
            className="inline-flex h-10 items-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-on-primary transition-transform hover:scale-[1.01]"
          >
            <MessageSquareText size={16} />
            AI 问答
          </Link>
        </div>
      </header>

      <div className="absolute inset-x-0 bottom-0 top-16 overflow-y-auto">
        <div className="space-y-6 px-6 py-6 pb-20 xl:px-8 xl:py-8">
      <ProgressBar value={task.progress} className="h-1.5" />

      {task.error && (
        <GlassPanel className="bg-tertiary-container/20 border-tertiary/30">
          <p className="text-sm text-tertiary flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-tertiary animate-ping" />
            {task.error}
          </p>
        </GlassPanel>
      )}

      {/* Main Layout Grid: Now dynamic for real space saving */}
      <div className={`grid gap-8 transition-all duration-500 ease-in-out ${showSidebar ? "grid-cols-[1fr_520px]" : "grid-cols-1"}`}>
        {/* Main Content Area */}
        <div className="space-y-6 min-w-0">
          {/* Enhanced Tabs Navigation */}
          <div className="flex items-center justify-between">
            <div className="flex gap-1 bg-surface-container-low/50 backdrop-blur-md rounded-xl p-1.5 border border-outline-variant/10 shadow-inner">
              {detailTabs.map((t) => (
                <button
                  key={t}
                  onClick={() => {
                    setTab(t);
                    if (t !== "graph") {
                      setSelectedNode(null);
                      setPreviousProcess(null);
                    }
                  }}
                  className={`px-5 py-2 text-[11px] font-bold uppercase tracking-widest rounded-lg transition-all duration-200 ${
                    tab === t
                      ? "bg-primary text-on-primary shadow-lg shadow-primary/20 scale-105"
                      : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
                  }`}
                >
                  {{
                    documentation: "文档",
                    graph: "神经图谱",
                    findings: "发现",
                    search: `搜索${currentSearchResults.length > 0 ? ` (${currentSearchResults.length})` : ""}`,
                    ai_summary: "AI 摘要",
                  }[t]}
                </button>
              ))}
            </div>
            <Link
              href={`/tasks/${taskId}/ask`}
              className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/15"
            >
              <Sparkles size={12} />
              打开全屏 AI 问答
            </Link>
            {tab === "documentation" && task.repository_id && (
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

          {/* Tab Content Panels */}
          <div className="animate-in fade-in duration-500">
            {tab === "documentation" && (
              <div className={`grid gap-4 ${showDocChat ? "grid-cols-[1fr_420px]" : "grid-cols-1"}`}>
                <GlassPanel className="p-0 overflow-hidden min-w-0">
                  <WikiViewer taskId={taskId} repoId={task?.repository_id ?? undefined} onPageChange={handleWikiPageChange} />
                </GlassPanel>
                {showDocChat && task.repository_id && (
                  <GlassPanel className="p-0 overflow-hidden flex flex-col" style={{ height: "calc(100vh - 14rem)" }}>
                    {/* Docked Chat Header */}
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
                          {wikiPageFilePaths.length > 0
                            ? `${wikiPageFilePaths.length} files in scope`
                            : "GLOBAL_CONTEXT"}
                        </span>
                        <button
                          onClick={() => setShowDocChat(false)}
                          className="p-1 rounded-md text-on-surface-variant hover:text-on-surface hover:bg-white/5 transition-all"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    </div>
                    <ChatPanel engine={docChatEngine} repoId={task.repository_id} className="flex-1 min-h-0" />
                  </GlassPanel>
                )}
              </div>
            )}

            {tab === "graph" && (
              <div className="space-y-3">
                {/* Graph Viewer */}
                {graphData ? (
                  <div className="relative h-[640px] flex flex-col">
                    <div className="flex-1 min-h-0">
                      <GraphViewer
                        nodes={graphData.nodes}
                        edges={graphData.edges}
                        selectedNodeId={selectedNode?.id ?? null}
                        onNodeClick={handleNodeClick}
                      />
                    </div>
                    {/* GitNexus Symbol Search — floating overlay */}
                    <GraphSearch
                      repo={repoName || undefined}
                      onNodeSelect={(nodeId) => {
                        const node = nodeMap.get(nodeId);
                        if (node) handleNodeClick(node);
                      }}
                      selectedNodeId={selectedNode?.id ?? null}
                    />
                  </div>
                ) : (
                  <GlassPanel className="py-32 flex flex-col items-center justify-center text-on-surface-variant/50 gap-2">
                    <p className="text-sm italic">
                      {graphRun?.status === "running"
                        ? "神经连接构建中..."
                        : graphRun?.status === "failed"
                          ? "GitNexus 分析失败"
                          : graphRun
                            ? "图谱数据为空。"
                            : "未执行 GitNexus 分析。"}
                    </p>
                    {graphRun?.error && (
                      <p className="text-xs text-tertiary/70 max-w-md text-center">{graphRun.error}</p>
                    )}
                  </GlassPanel>
                )}
              </div>
            )}

            {tab === "findings" && (
              <div className="space-y-8">
                {graphData?.intelligence ? (
                  <>
                    {/* Summary metrics */}
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
                      <GlassPanel className="bg-primary/5 border-primary/20">
                        <h4 className="text-[10px] uppercase tracking-[0.2em] text-primary/70 font-bold mb-4">关键流程</h4>
                        <div className="flex items-baseline gap-2">
                          <p className="text-5xl font-display font-black text-primary">{keyProcesses.length}</p>
                          <span className="text-xs text-on-surface-variant">/ {(graphData.processes?.length || 0)} 总计</span>
                        </div>
                      </GlassPanel>
                      <GlassPanel className="bg-secondary/5 border-secondary/20">
                        <h4 className="text-[10px] uppercase tracking-[0.2em] text-secondary/70 font-bold mb-4">逻辑社区</h4>
                        <div className="flex items-baseline gap-2">
                          <p className="text-5xl font-display font-black text-secondary">{graphData.communities?.length || 0}</p>
                          <span className="text-xs text-on-surface-variant">内聚模块</span>
                        </div>
                      </GlassPanel>
                      <GlassPanel className="bg-tertiary/5 border-tertiary/20">
                        <h4 className="text-[10px] uppercase tracking-[0.2em] text-tertiary/70 font-bold mb-4">跨社区流程</h4>
                        <div className="flex items-baseline gap-2">
                          <p className="text-5xl font-display font-black text-tertiary">
                            {(graphData.intelligence as Record<string, Record<string, number>>)?.process_summary?.cross_community ?? 0}
                          </p>
                          <span className="text-xs text-on-surface-variant">跨模块协作</span>
                        </div>
                      </GlassPanel>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                      {/* Key processes */}
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                          <div className="h-3 w-3 bg-primary/20 rounded-sm border border-primary/40" />
                          <h4 className="text-xs font-bold text-on-surface uppercase tracking-widest">关键流程</h4>
                          <span className="text-[9px] text-on-surface-variant/40 font-data">按跨社区 + 步骤数排序</span>
                        </div>
                        {keyProcesses.map(p => {
                          const steps = p.steps ?? [];
                          const isCross = p.properties.processType === "cross_community";
                          return (
                            <div
                              key={p.id}
                              className="p-4 rounded-xl bg-surface-container-low border border-outline-variant/20 hover:border-primary/40 hover:bg-surface-container-high transition-all cursor-pointer group"
                              onClick={() => { setPreviousProcess(null); setTab("graph"); setSelectedNode(p); }}
                            >
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-1.5">
                                  {isCross && (
                                    <span className="text-[9px] font-data text-tertiary/90 bg-tertiary/10 border border-tertiary/20 px-1.5 py-0.5 rounded">跨社区</span>
                                  )}
                                  <span className="text-[9px] font-data text-primary/70 border border-primary/20 px-1.5 py-0.5 rounded">
                                    {p.properties.stepCount} 步
                                  </span>
                                </div>
                              </div>
                              <p className="text-sm font-semibold text-on-surface group-hover:text-primary transition-colors mb-1">{p.properties.name}</p>
                              {steps.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-2">
                                  {steps.slice(0, 5).map((s, i) => {
                                    const node = nodeMap.get(s.symbolId);
                                    return (
                                      <span key={i} className="text-[9px] font-data text-on-surface-variant/60 bg-surface-container-high/50 px-1.5 py-0.5 rounded">
                                        {node?.properties.name ?? s.symbolId.slice(0, 8)}
                                      </span>
                                    );
                                  })}
                                  {steps.length > 5 && (
                                    <span className="text-[9px] text-on-surface-variant/40 font-data">+{steps.length - 5}</span>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}

                        {/* Other processes (collapsible) */}
                        {otherProcesses.length > 0 && (
                          <button
                            onClick={() => setShowOtherProcesses(!showOtherProcesses)}
                            className="w-full flex items-center justify-center gap-1.5 text-[10px] text-on-surface-variant/50 hover:text-on-surface py-2 rounded-lg border border-outline-variant/10 hover:bg-surface-container-high/50 transition-colors"
                          >
                            {showOtherProcesses ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                            {showOtherProcesses ? "收起" : `其他 ${otherProcesses.length} 个流程`}
                          </button>
                        )}
                        {showOtherProcesses && otherProcesses.map(p => (
                          <div
                            key={p.id}
                            className="p-3 rounded-lg bg-surface-container-low/50 border border-outline-variant/10 hover:border-primary/30 transition-all cursor-pointer group"
                            onClick={() => { setPreviousProcess(null); setTab("graph"); setSelectedNode(p); }}
                          >
                            <div className="flex items-center justify-between">
                              <p className="text-xs text-on-surface-variant group-hover:text-on-surface transition-colors truncate">{p.properties.name}</p>
                              <span className="text-[9px] text-on-surface-variant/40 font-data shrink-0 ml-2">{p.properties.stepCount} 步</span>
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Communities */}
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                          <div className="h-3 w-3 bg-secondary/20 rounded-sm border border-secondary/40" />
                          <h4 className="text-xs font-bold text-on-surface uppercase tracking-widest">逻辑聚类</h4>
                        </div>
                        {graphData.communities?.map(c => (
                          <div
                            key={c.id}
                            className="p-4 rounded-xl bg-surface-container-low border border-outline-variant/20 hover:border-secondary/40 hover:bg-surface-container-high transition-all cursor-pointer group"
                            onClick={() => { setPreviousProcess(null); setTab("graph"); setSelectedNode(c); }}
                          >
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-[9px] font-data text-secondary/70 border border-secondary/20 px-1.5 py-0.5 rounded">
                                {c.properties.memberCount} 成员
                              </span>
                              {c.properties.cohesion != null && (
                                <span className={`text-[9px] font-data px-1.5 py-0.5 rounded ${
                                  (c.properties.cohesion as number) >= 0.5
                                    ? "text-secondary/70 border border-secondary/20"
                                    : "text-tertiary/70 border border-tertiary/20"
                                }`}>
                                  内聚度 {((c.properties.cohesion as number) * 100).toFixed(0)}%
                                </span>
                              )}
                            </div>
                            <p className="text-sm font-semibold text-on-surface group-hover:text-secondary transition-colors">{c.properties.name}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                ) : (
                  <GlassPanel className="py-20 flex items-center justify-center">
                    <p className="text-sm text-on-surface-variant/40 italic font-display">
                      智能引擎正在分析代码深度关系，请稍后...
                    </p>
                  </GlassPanel>
                )}
              </div>
            )}

            {tab === "search" && (
              <div className="space-y-6">
                {/* 搜索输入框区域 */}
                {zoektRun && (
                  <form onSubmit={handleSearch} className="flex gap-2">
                    <div className="relative flex-1 group">
                      <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-on-surface-variant/40 group-focus-within:text-primary transition-colors">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="11" cy="11" r="8" />
                          <line x1="21" y1="21" x2="16.65" y2="16.65" />
                        </svg>
                      </div>
                      <input
                        type="text"
                        value={customSearchQuery}
                        onChange={(e) => setCustomSearchQuery(e.target.value)}
                        placeholder="输入关键词进行实时全代码搜索..."
                        className="w-full h-11 bg-surface-container-low border border-outline-variant/30 rounded-xl pl-10 pr-4 text-sm font-data text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:border-primary/50 focus:ring-4 focus:ring-primary/5 transition-all"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={isSearching || !customSearchQuery.trim()}
                      className="h-11 px-6 bg-primary text-on-primary text-xs font-bold uppercase tracking-widest rounded-xl hover:shadow-lg hover:shadow-primary/20 disabled:opacity-50 disabled:hover:shadow-none transition-all"
                    >
                      {isSearching ? "搜索中..." : "搜索"}
                    </button>
                  </form>
                )}

                {/* 状态反馈区 */}
                {searchError && (
                  <GlassPanel className="bg-tertiary-container/20 border-tertiary/30 py-3">
                    <p className="text-sm text-tertiary">{searchError}</p>
                  </GlassPanel>
                )}

                {zoektRun ? (
                  zoektRun.status === "failed" ? (
                    <GlassPanel className="bg-tertiary-container/20 border-tertiary/30">
                      <p className="text-sm text-tertiary">Zoekt 搜索失败：{zoektRun.error}</p>
                    </GlassPanel>
                  ) : currentSearchResults.length === 0 ? (
                    <GlassPanel className="py-16 flex flex-col items-center text-center space-y-3">
                      <p className="text-sm text-on-surface-variant/50">
                        {zoektRun.status === "running"
                          ? "全速构建索引中..."
                          : isSearching 
                            ? "搜索中..."
                            : zoektIndexedOnly && !interactiveResults
                              ? "代码全文索引已建立。请输入关键词开始实时搜索。"
                              : `在当前代码库中未发现「${currentSearchQuery}」的特征匹配。`}
                      </p>
                    </GlassPanel>
                  ) : (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-xs text-on-surface-variant/60 font-data">
                        <span className="text-primary font-bold">{currentSearchResults.length}</span> 个文件匹配关键词
                        <span className="bg-surface-container-high px-2 py-0.5 rounded font-mono text-on-surface">「{currentSearchQuery}」</span>
                        {interactiveResults && (
                          <span className="text-[10px] bg-secondary/10 text-secondary border border-secondary/20 px-1.5 py-0.5 rounded ml-auto uppercase tracking-wider font-bold">
                            实时结果
                          </span>
                        )}
                      </div>
                      {currentSearchResults.map((file, fi) => (
                        <GlassPanel key={fi} className="p-0 overflow-hidden">
                          <div className="flex items-center gap-2 px-4 py-2.5 bg-surface-container-high/50 border-b border-outline-variant/10">
                            <span className="font-mono text-xs text-secondary truncate">{file.file}</span>
                            <span className="text-[10px] text-on-surface-variant/40 shrink-0">{file.matches.length} 处</span>
                          </div>
                          <div className="divide-y divide-outline-variant/10">
                            {file.matches.map((m, mi) => (
                              <div key={mi} className="flex gap-3 px-4 py-2 hover:bg-surface-container-high/30 transition-colors">
                                <span className="shrink-0 w-10 text-right text-[10px] font-data text-on-surface-variant/30 pt-0.5">
                                  {m.line_number}
                                </span>
                                <code className="text-xs font-mono text-on-surface/80 whitespace-pre-wrap break-all leading-relaxed">
                                  {m.line_content}
                                </code>
                              </div>
                            ))}
                          </div>
                        </GlassPanel>
                      ))}
                    </div>
                  )
                ) : (
                  <GlassPanel className="py-16 flex flex-col items-center text-center space-y-3">
                    <p className="text-sm text-on-surface-variant/40 italic">
                      本次分析未启用 Zoekt 代码搜索。新建任务时开启「代码搜索」即可。
                    </p>
                  </GlassPanel>
                )}
              </div>
            )}

            {tab === "ai_summary" && (
              <GlassPanel className="p-8 border-primary/10">
                {task.ai_summary ? (
                  <div className="prose prose-invert prose-sm max-w-none prose-headings:text-primary prose-strong:text-primary-fixed">
                    <MarkdownRenderer content={task.ai_summary} />
                  </div>
                ) : (
                  <div className="py-12 flex flex-col items-center text-center space-y-4">
                    <div className={`p-4 rounded-full ${task.ai_enabled ? "bg-primary/5 text-primary animate-pulse" : "bg-outline/5 text-outline-variant"}`}>
                      <Bot size={32} />
                    </div>
                    <p className="text-sm text-on-surface-variant/50 max-w-md leading-relaxed">
                      {task.ai_enabled
                        ? "AI 正在对本次分析结果进行深度摘要合成。这通常需要 10-20 秒，请保持关注。"
                        : "本次任务未启用 AI 摘要功能。你可以通过创建新任务并开启 AI 选项来获取自动化的架构解读。"}
                    </p>
                  </div>
                )}
              </GlassPanel>
            )}
          </div>
        </div>

        {/* Dynamic Sidebar: context panel for selected node */}
        {showSidebar && (
          <div className="sticky top-6 h-[calc(100vh-6rem)] overflow-y-auto animate-in slide-in-from-right-8 duration-500 space-y-2">
            {/* Breadcrumb: back to parent process */}
            {previousProcess && !isIntelligenceNode && (
              <button
                onClick={() => {
                  setSelectedNode(previousProcess);
                  setPreviousProcess(null);
                }}
                className="flex items-center gap-2 w-full px-3 py-2.5 text-xs text-on-surface-variant hover:text-primary transition-all rounded-lg bg-surface-container-low hover:bg-surface-container-high border border-outline-variant/20 group"
              >
                <span className="group-hover:-translate-x-0.5 transition-transform inline-block shrink-0 text-base leading-none">←</span>
                <span className="truncate">返回：{previousProcess.properties.name}</span>
              </button>
            )}
            {isIntelligenceNode ? (
              <IntelligencePanel
                node={selectedNode!}
                nodeMap={nodeMap}
                edges={graphData?.edges ?? []}
                onNodeClick={handleNodeClick}
                repo={repoName || undefined}
              />
            ) : (
              <CodePanel node={selectedNode!} repoName={repoName} />
            )}
          </div>
        )}
      </div>
        </div>
      </div>

      {task.repository_id && (
        <FloatingChat
          repoId={task.repository_id}
          currentPageFilePaths={tab === "documentation" ? wikiPageFilePaths : undefined}
          hidden={tab === "documentation" && showDocChat}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="删除分析任务"
        description="确定要删除此分析任务吗？所有相关的工具运行记录将被一同删除，此操作不可撤销。"
        confirmLabel="删除"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}

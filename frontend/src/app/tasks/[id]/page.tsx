"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import GraphViewer from "@/components/ui/GraphViewer";
import CodePanel from "@/components/ui/CodePanel";
import IntelligencePanel from "@/components/ui/IntelligencePanel";
import FloatingChat from "@/components/ui/FloatingChat";
import WikiViewer from "@/components/ui/WikiViewer";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { api } from "@/lib/api";
import type { TaskDetail, GraphNode, GraphData } from "@/lib/types";
import { Clock, Tag, Cpu, ChevronDown, ChevronUp, Bot } from "lucide-react";

const KEY_PROCESS_LIMIT = 10;

function scoreProcess(p: GraphNode): number {
  const isCross = p.properties.processType === "cross_community";
  const steps = p.properties.stepCount ?? 0;
  return (isCross ? 100 : 0) + steps;
}

const detailTabs = ["documentation", "graph", "findings", "search", "ai_summary"] as const;
type Tab = (typeof detailTabs)[number];

const INTELLIGENCE_LABELS = new Set(["Process", "Community"]);

export default function TaskDetailPage() {
  const params = useParams();
  const router = useRouter();
  const taskId = params.id as string;
  const [tab, setTab] = useState<Tab>("documentation");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [showMetadata, setShowMetadata] = useState(false);
  const [showOtherProcesses, setShowOtherProcesses] = useState(false);
  const [previousProcess, setPreviousProcess] = useState<GraphNode | null>(null);

  // Derive graph data — safe with task=null (useMemo must run unconditionally)
  const graphRun = task?.tool_runs.find((r) => r.tool_name === "gitnexus");
  const graphData = (graphRun?.result?.graph as GraphData) ?? null;
  const repoName = (graphRun?.result?.metadata as Record<string, unknown>)?.repo_name as string ?? "";

  // Zoekt search results
  const zoektRun = task?.tool_runs.find((r) => r.tool_name === "zoekt");
  type SearchMatch = { line_number: number; line_content: string };
  type SearchFile = { file: string; repo: string; matches: SearchMatch[] };
  const searchResults = (zoektRun?.result?.search_results as SearchFile[]) ?? [];
  const searchQuery = (zoektRun?.result?.query as string) ?? "";
  const zoektIndexedOnly = zoektRun?.result?.indexed === true && !searchQuery;

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

  const handleCancel = async () => {
    try {
      await api.tasks.cancel(taskId);
    } catch (e) {
      console.error("Failed to cancel task:", e);
    }
  };

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
      <div className="p-6">
        <p className="text-tertiary">{error}</p>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="p-6">
        <p className="text-on-surface-variant/50">加载任务中...</p>
      </div>
    );
  }

  const showSidebar = tab === "graph" && selectedNode;
  const isIntelligenceNode = selectedNode && INTELLIGENCE_LABELS.has(selectedNode.label);

  return (
    <div className="max-w-[1600px] mx-auto space-y-6 pb-20">
      {/* Refined Header & Metadata Area */}
      <div className="space-y-4">
        <div className="flex items-end justify-between border-b border-outline-variant/30 pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <h2 className="font-display text-2xl font-bold text-on-surface tracking-tight">
                {repoName || "项目分析"}
              </h2>
              <StatusBadge status={task.status as "running" | "completed" | "failed" | "pending"} />
              {(task.status === "pending" || task.status === "running") && (
                <button
                  onClick={handleCancel}
                  className="p-1.5 rounded-lg hover:bg-surface-container-highest/50 text-on-surface-variant/50 hover:text-tertiary transition-colors"
                  title="停止任务"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              )}
              {(task.status === "completed" || task.status === "failed" || task.status === "cancelled") && (
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="p-1.5 rounded-lg hover:bg-surface-container-highest/50 text-on-surface-variant/50 hover:text-tertiary transition-colors"
                  title="删除任务"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                </button>
              )}
            </div>
            <div className="flex items-center gap-4 text-xs text-on-surface-variant/70 font-data">
              <span className="flex items-center gap-1.5">
                <Tag size={12} className="text-primary/60" />
                {task.id.slice(0, 8)}
              </span>
              <span className="flex items-center gap-1.5">
                <Cpu size={12} className="text-secondary/60" />
                {task.task_type}
              </span>
              <span className="flex items-center gap-1.5">
                <Clock size={12} className="text-on-surface-variant/40" />
                {new Date(task.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
          
          <button 
            onClick={() => setShowMetadata(!showMetadata)}
            className="flex items-center gap-1 text-[10px] uppercase tracking-widest text-on-surface-variant hover:text-primary transition-colors font-bold"
          >
            任务详情 {showMetadata ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>

        {showMetadata && (
          <div className="animate-in slide-in-from-top-2 duration-300">
            <GlassPanel className="bg-surface-container-lowest/30 border-dashed border-outline-variant/50">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 p-1">
                <div className="space-y-1">
                  <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold">工具链</p>
                  <p className="text-xs font-data text-on-surface">{task.tools.join(", ")}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold">AI 增强</p>
                  <p className="text-xs font-data text-on-surface">{task.ai_enabled ? "已启用" : "已禁用"}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold">开始时间</p>
                  <p className="text-xs font-data text-on-surface">{task.started_at ? new Date(task.started_at).toLocaleTimeString() : "-"}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold">完成时间</p>
                  <p className="text-xs font-data text-on-surface">{task.completed_at ? new Date(task.completed_at).toLocaleTimeString() : "-"}</p>
                </div>
              </div>
              {task.tool_runs.length > 0 && (
                <div className="mt-4 pt-4 border-t border-outline-variant/20">
                  <p className="text-[10px] uppercase tracking-wider text-on-surface-variant/50 font-bold mb-2">执行状态</p>
                  <div className="flex gap-2 flex-wrap">
                    {task.tool_runs.map((run) => (
                      <div key={run.id} className="flex items-center gap-2 bg-surface-container-high/50 rounded-full px-3 py-1 border border-outline-variant/10">
                        <span className="font-data text-[10px] text-on-surface-variant">{run.tool_name}</span>
                        <div className={`w-1.5 h-1.5 rounded-full ${
                          run.status === "completed" ? "bg-primary" : 
                          run.status === "failed" ? "bg-tertiary" : 
                          run.status === "running" ? "bg-secondary animate-pulse" : "bg-outline"
                        }`} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </GlassPanel>
          </div>
        )}
      </div>

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
                    search: `搜索${searchResults.length > 0 ? ` (${searchResults.length})` : ""}`,
                    ai_summary: "AI 摘要"
                  }[t]}
                </button>
              ))}
            </div>
            
          </div>

          {/* Tab Content Panels */}
          <div className="animate-in fade-in duration-500">
            {tab === "documentation" && (
              <GlassPanel className="p-0 overflow-hidden">
                <WikiViewer taskId={taskId} />
              </GlassPanel>
            )}

            {tab === "graph" && (
              <div className="space-y-6 h-[700px] flex flex-col">
                {graphData ? (
                  <div className="flex-1 min-h-0 relative">
                    <GraphViewer
                      nodes={graphData.nodes}
                      edges={graphData.edges}
                      selectedNodeId={selectedNode?.id ?? null}
                      onNodeClick={handleNodeClick}
                    />
                    <div className="absolute top-4 left-4 pointer-events-none">
                      <div className="bg-surface-container-high/80 backdrop-blur-md px-3 py-1.5 rounded border border-outline-variant/30">
                        <p className="text-[10px] text-primary font-bold uppercase tracking-wider flex items-center gap-2">
                          <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
                          Neural Intelligence Active
                        </p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <GlassPanel className="flex-1 flex items-center justify-center text-on-surface-variant/50">
                    <p className="text-sm italic">
                      {task.status === "running" ? "神经连接构建中..." : "未发现图谱数据。"}
                    </p>
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
              <div className="space-y-4">
                {zoektRun ? (
                  zoektRun.status === "failed" ? (
                    <GlassPanel className="bg-tertiary-container/20 border-tertiary/30">
                      <p className="text-sm text-tertiary">Zoekt 搜索失败：{zoektRun.error}</p>
                    </GlassPanel>
                  ) : searchResults.length === 0 ? (
                    <GlassPanel className="py-16 flex flex-col items-center text-center space-y-3">
                      <p className="text-sm text-on-surface-variant/50">
                        {zoektRun.status === "running"
                          ? "全速构建索引中..."
                          : zoektIndexedOnly
                            ? "代码全文索引已建立。由于本次未提供搜索关键词，已完成预索引并进入待命状态。"
                            : `在当前代码库中未发现「${searchQuery}」的特征匹配。`}
                      </p>
                    </GlassPanel>
                  ) : (
                    <>
                      <div className="flex items-center gap-2 text-xs text-on-surface-variant/60 font-data">
                        <span className="text-primary font-bold">{searchResults.length}</span> 个文件匹配关键词
                        <span className="bg-surface-container-high px-2 py-0.5 rounded font-mono text-on-surface">「{searchQuery}」</span>
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
                    </>
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
              />
            ) : (
              <CodePanel node={selectedNode!} repoName={repoName} />
            )}
          </div>
        )}
      </div>

      {/* Floating AI Chat */}
      <FloatingChat taskId={taskId} />

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

"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import ProgressBar from "@/components/ui/ProgressBar";
import MarkdownRenderer from "@/components/ui/MarkdownRenderer";
import MermaidRenderer from "@/components/ui/MermaidRenderer";
import GraphViewer from "@/components/ui/GraphViewer";
import CodePanel from "@/components/ui/CodePanel";
import FloatingChat from "@/components/ui/FloatingChat";
import { api } from "@/lib/api";
import type { TaskDetail, GraphNode, GraphData } from "@/lib/types";
import { Clock, Tag, Cpu, Code, ChevronDown, ChevronUp, Bot } from "lucide-react";

const detailTabs = ["documentation", "graph", "findings", "ai_summary"] as const;
type Tab = (typeof detailTabs)[number];

export default function TaskDetailPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [tab, setTab] = useState<Tab>("documentation");
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [showMetadata, setShowMetadata] = useState(false);

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

  const docRun = task.tool_runs.find((r) => r.tool_name === "deepwiki");
  const documentation = (docRun?.result?.documentation as string) ?? "";
  const diagrams = (docRun?.result?.diagrams as Array<{ type: string; content: string }>) ?? [];

  const graphRun = task.tool_runs.find((r) => r.tool_name === "gitnexus");
  const graphData = (graphRun?.result?.graph as GraphData) ?? null;
  const repoName = (graphRun?.result?.metadata as Record<string, unknown>)?.repo_name as string ?? "";

  const showCodeInSidebar = tab === "graph" && selectedNode;

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
      <div className={`grid gap-8 transition-all duration-500 ease-in-out ${showCodeInSidebar ? "grid-cols-[1fr_520px]" : "grid-cols-1"}`}>
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
                    if (t !== "graph") setSelectedNode(null);
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
                    ai_summary: "AI 摘要"
                  }[t]}
                </button>
              ))}
            </div>
            
            {tab === "documentation" && documentation && (
              <div className="hidden sm:flex items-center gap-2 text-[10px] text-on-surface-variant/50 font-data">
                <Code size={12} />
                {documentation.length} 字符
              </div>
            )}
          </div>

          {/* Tab Content Panels */}
          <div className="animate-in fade-in duration-500">
            {tab === "documentation" && (
              <GlassPanel className="p-8">
                {documentation ? (
                  <div className="prose prose-invert prose-sm max-w-none prose-headings:font-display prose-headings:tracking-tight prose-a:text-primary hover:prose-a:text-primary-fixed">
                    <MarkdownRenderer content={documentation} />
                    {diagrams.length > 0 && (
                      <div className="mt-12 pt-8 border-t border-outline-variant/20 space-y-8">
                        <div className="flex items-center gap-2">
                          <div className="h-4 w-1 bg-primary rounded-full" />
                          <h4 className="text-xs font-bold uppercase tracking-[0.2em] text-on-surface-variant">
                            系统架构图谱
                          </h4>
                        </div>
                        {diagrams.map((d, i) => (
                          <div key={i} className="bg-surface-container-lowest/20 rounded-xl p-6 border border-outline-variant/10">
                            <MermaidRenderer chart={d.content} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="py-20 flex flex-col items-center justify-center space-y-4">
                    <div className="w-12 h-12 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
                    <p className="text-sm text-on-surface-variant/50 font-display italic">
                      {task.status === "running" ? "正在通过深度语义扫描生成文档..." : "等待数据回传..."}
                    </p>
                  </div>
                )}
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
                      onNodeClick={setSelectedNode}
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
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                      <GlassPanel className="bg-primary/5 border-primary/20 group hover:bg-primary/10 transition-colors">
                        <h4 className="text-[10px] uppercase tracking-[0.2em] text-primary/70 font-bold mb-4">执行流概览</h4>
                        <div className="flex items-baseline gap-2">
                          <p className="text-5xl font-display font-black text-primary">{graphData.processes?.length || 0}</p>
                          <span className="text-xs text-on-surface-variant">关键路径</span>
                        </div>
                      </GlassPanel>
                      <GlassPanel className="bg-secondary/5 border-secondary/20 group hover:bg-secondary/10 transition-colors">
                        <h4 className="text-[10px] uppercase tracking-[0.2em] text-secondary/70 font-bold mb-4">逻辑社区聚类</h4>
                        <div className="flex items-baseline gap-2">
                          <p className="text-5xl font-display font-black text-secondary">{graphData.communities?.length || 0}</p>
                          <span className="text-xs text-on-surface-variant">内聚模块</span>
                        </div>
                      </GlassPanel>
                    </div>
                    
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                          <div className="h-3 w-3 bg-primary/20 rounded-sm border border-primary/40" />
                          <h4 className="text-xs font-bold text-on-surface uppercase tracking-widest">核心业务流</h4>
                        </div>
                        {graphData.processes?.map(p => (
                          <div 
                            key={p.id} 
                            className="p-4 rounded-xl bg-surface-container-low border border-outline-variant/20 hover:border-primary/40 hover:bg-surface-container-high transition-all cursor-pointer group"
                            onClick={() => { setTab("graph"); setSelectedNode(p); }}
                          >
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-[9px] font-data text-primary/70 border border-primary/20 px-1.5 py-0.5 rounded">{p.properties.processType}</span>
                              <span className="text-[9px] text-on-surface-variant/40 font-data">{p.properties.stepCount} Steps</span>
                            </div>
                            <p className="text-sm font-semibold text-on-surface group-hover:text-primary transition-colors">{p.properties.name}</p>
                          </div>
                        ))}
                      </div>
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                          <div className="h-3 w-3 bg-secondary/20 rounded-sm border border-secondary/40" />
                          <h4 className="text-xs font-bold text-on-surface uppercase tracking-widest">逻辑聚类分析</h4>
                        </div>
                        {graphData.communities?.map(c => (
                          <div 
                            key={c.id} 
                            className="p-4 rounded-xl bg-surface-container-low border border-outline-variant/20 hover:border-secondary/40 hover:bg-surface-container-high transition-all cursor-pointer group"
                            onClick={() => { setTab("graph"); setSelectedNode(c); }}
                          >
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-[9px] font-data text-secondary/70 border border-secondary/20 px-1.5 py-0.5 rounded">Community</span>
                              <span className="text-[9px] text-on-surface-variant/40 font-data">{c.properties.memberCount} Members</span>
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

        {/* Dynamic Sidebar: Only shown for CodePanel */}
        {showCodeInSidebar && (
          <div className="sticky top-6 h-[calc(100vh-6rem)] animate-in slide-in-from-right-8 duration-500">
            <CodePanel node={selectedNode!} repoName={repoName} />
          </div>
        )}
      </div>

      {/* Floating AI Chat */}
      <FloatingChat taskId={taskId} />
    </div>
  );
}

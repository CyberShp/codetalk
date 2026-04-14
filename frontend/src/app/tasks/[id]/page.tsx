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
import { api } from "@/lib/api";
import type { TaskDetail, GraphNode, GraphData } from "@/lib/types";

const detailTabs = ["documentation", "graph", "findings"] as const;
type Tab = (typeof detailTabs)[number];

export default function TaskDetailPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [tab, setTab] = useState<Tab>("documentation");
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold text-on-surface">
            任务 {task.id.slice(0, 8)}
          </h2>
          <p className="text-sm text-on-surface-variant mt-0.5">
            {task.task_type} &middot; {task.tools.join(", ")}
          </p>
        </div>
        <StatusBadge status={task.status as "running" | "completed" | "failed" | "pending"} />
      </div>

      <ProgressBar value={task.progress} />

      {task.error && (
        <GlassPanel className="bg-tertiary-container/20">
          <p className="text-sm text-tertiary">{task.error}</p>
        </GlassPanel>
      )}

      <div className="grid grid-cols-[1fr_320px] gap-6">
        {/* Main Content */}
        <div className="space-y-4">
          {/* Tabs */}
          <div className="flex gap-1 bg-surface-container-low rounded-lg p-1 w-fit">
            {detailTabs.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 text-xs rounded-md capitalize transition-colors ${
                  tab === t
                    ? "bg-surface-container-high text-on-surface"
                    : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                {{ documentation: "文档", graph: "图谱", findings: "发现" }[t]}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          {tab === "documentation" && (
            <GlassPanel>
              {documentation ? (
                <>
                  <MarkdownRenderer content={documentation} />
                  {diagrams.length > 0 && (
                    <div className="mt-6 space-y-4">
                      <h4 className="text-xs text-on-surface-variant">
                        架构图
                      </h4>
                      {diagrams.map((d, i) => (
                        <MermaidRenderer key={i} chart={d.content} />
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-sm text-on-surface-variant/50">
                  {task.status === "running"
                    ? "文档生成中..."
                    : task.status === "pending"
                      ? "等待启动..."
                      : "未生成文档。"}
                </p>
              )}
            </GlassPanel>
          )}

          {tab === "graph" && (
            <div className="space-y-4">
              {graphData ? (
                <GraphViewer
                  nodes={graphData.nodes}
                  edges={graphData.edges}
                  selectedNodeId={selectedNode?.id ?? null}
                  onNodeClick={setSelectedNode}
                />
              ) : (
                <GlassPanel>
                  <p className="text-sm text-on-surface-variant/50">
                    {task.status === "running"
                      ? "知识图谱生成中..."
                      : task.tools.includes("gitnexus")
                        ? "无图谱数据。GitNexus 可能尚未完成。"
                        : "将 GitNexus 添加到任务工具以生成知识图谱。"}
                  </p>
                </GlassPanel>
              )}

              {/* Function-level code panel (below graph) */}
              {selectedNode && (
                <CodePanel node={selectedNode} repoName={repoName} />
              )}

              {/* Tool execution timeline (collapsed) */}
              {task.tool_runs.length > 0 && (
                <GlassPanel>
                  <h4 className="text-xs text-on-surface-variant mb-3">
                    执行时间线
                  </h4>
                  <div className="flex gap-2 flex-wrap">
                    {task.tool_runs.map((run) => (
                      <div
                        key={run.id}
                        className="flex items-center gap-2 bg-surface-container-lowest/50 rounded px-3 py-1.5"
                      >
                        <span className="font-data text-[10px] text-primary-fixed-dim">
                          {run.tool_name}
                        </span>
                        <StatusBadge status={run.status as "running" | "completed" | "failed" | "pending"} />
                      </div>
                    ))}
                  </div>
                </GlassPanel>
              )}
            </div>
          )}

          {tab === "findings" && (
            <GlassPanel>
              <p className="text-sm text-on-surface-variant/50">
                暂无其他工具的发现。更多分析工具（Zoekt、Joern、CodeCompass）将在后续阶段支持。
              </p>
            </GlassPanel>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <GlassPanel>
            <h4 className="text-xs text-on-surface-variant mb-3">
              AI 摘要
            </h4>
            {task.ai_summary ? (
              <p className="text-sm text-on-surface/80 leading-relaxed">
                {task.ai_summary}
              </p>
            ) : (
              <p className="text-sm text-on-surface-variant/50">
                {task.ai_enabled
                  ? task.status === "running"
                    ? "生成摘要中..."
                    : "未生成摘要。"
                  : "AI 分析已禁用"}
              </p>
            )}
          </GlassPanel>

          <GlassPanel>
            <h4 className="text-xs text-on-surface-variant mb-3">
              任务信息
            </h4>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">类型</dt>
                <dd className="text-on-surface font-data text-xs">{task.task_type}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">AI</dt>
                <dd className="text-on-surface font-data text-xs">
                  {task.ai_enabled ? "已启用" : "已禁用"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-on-surface-variant">创建时间</dt>
                <dd className="text-on-surface font-data text-xs">
                  {new Date(task.created_at).toLocaleString()}
                </dd>
              </div>
              {task.started_at && (
                <div className="flex justify-between">
                  <dt className="text-on-surface-variant">开始时间</dt>
                  <dd className="text-on-surface font-data text-xs">
                    {new Date(task.started_at).toLocaleTimeString()}
                  </dd>
                </div>
              )}
              {task.completed_at && (
                <div className="flex justify-between">
                  <dt className="text-on-surface-variant">完成时间</dt>
                  <dd className="text-on-surface font-data text-xs">
                    {new Date(task.completed_at).toLocaleTimeString()}
                  </dd>
                </div>
              )}
            </dl>
          </GlassPanel>
        </div>
      </div>
    </div>
  );
}

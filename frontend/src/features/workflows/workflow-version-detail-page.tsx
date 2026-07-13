"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, GitBranch } from "lucide-react";
import { useEffect, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { AuthoringGraphV2, WorkflowVersion } from "@/lib/types/workflow";

export function WorkflowVersionDetailPage({ workflowId, versionId }: { workflowId: string; versionId: string }) {
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void workflowsApi.version(workflowId, versionId).then(setVersion).catch((cause) => setError(cause instanceof Error ? cause.message : "版本加载失败")); }, [versionId, workflowId]);
  if (error) return <div className="ct-v2-empty-state is-error"><p>{error}</p></div>;
  if (!version) return <div className="ct-v2-page-loading">正在读取版本…</div>;
  const graph = version.authoring_graph.schema_version === 2
    ? version.authoring_graph as AuthoringGraphV2
    : null;
  return <main className="ct-v2-library">
    <header className="ct-v2-page-header"><div><Link className="ct-v2-back-link" href={`/workflows/${encodeURIComponent(workflowId)}/versions`}><ArrowLeft size={15} />版本列表</Link><h1>{graph?.name ?? workflowId} · V{version.version_number}</h1><span>这是不可修改的发布快照，任务运行会引用这个具体版本。</span></div><span className={`ct-v2-status is-${version.state}`}>{version.state === "published" ? "已发布" : version.state === "draft" ? "草稿" : "已归档"}</span></header>
    <section className="ct-v2-version-summary"><div><GitBranch size={16} /><strong>{graph?.nodes.length ?? 0}</strong><span>画布节点</span></div><div><CheckCircle2 size={16} /><strong>{version.compiled_plan?.topological_order.length ?? 0}</strong><span>执行节点</span></div><div><strong>{graph?.nodes.filter((node) => node.kind === "output").length ?? 0}</strong><span>输出契约</span></div></section>
    <section className="ct-v2-version-plan"><h2>冻结的执行计划</h2>{version.compiled_plan ? <ol>{version.compiled_plan.topological_order.map((nodeId, index) => { const node = version.compiled_plan?.nodes.find((item) => item.node_id === nodeId); return <li key={nodeId}><span>{index + 1}</span><strong>{nodeId}</strong><em>{node?.provider || node?.type}</em><small>{node?.depends_on.length ? `依赖 ${node.depends_on.join("、")}` : "无前置依赖"}</small></li>; })}</ol> : <p>该历史版本没有 V2 编译计划。</p>}</section>
    <details className="ct-v2-readonly-json"><summary>查看只读编译 JSON</summary><pre>{JSON.stringify(version.compiled_definition, null, 2)}</pre></details>
  </main>;
}

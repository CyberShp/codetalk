"use client";

import Link from "next/link";
import { AlertTriangle, ArrowLeft, CheckCircle2, CopyPlus, GitBranch, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkflowMigrationPreview, WorkflowVersion } from "@/lib/types/workflow";

export function WorkflowVersionDetailPage({ workflowId, versionId }: { workflowId: string; versionId: string }) {
  const router = useRouter();
  const [version, setVersion] = useState<WorkflowVersion | null>(null);
  const [error, setError] = useState("");
  const [migrationError, setMigrationError] = useState("");
  const [preview, setPreview] = useState<WorkflowMigrationPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [copying, setCopying] = useState(false);
  useEffect(() => { void workflowsApi.version(workflowId, versionId).then(setVersion).catch((cause) => setError(cause instanceof Error ? cause.message : "版本加载失败")); }, [versionId, workflowId]);
  if (error) return <div className="ct-v2-empty-state is-error"><p>{error}</p></div>;
  if (!version) return <div className="ct-v2-page-loading">正在读取版本…</div>;
  const graph = version.authoring_graph as Record<string, unknown>;
  const graphNodes = Array.isArray(graph.nodes) ? graph.nodes.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
  const definition = version.compiled_definition as Record<string, unknown> | null;
  const definitionSteps = Array.isArray(definition?.steps) ? definition.steps.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
  const displayName = typeof graph.name === "string" && graph.name.trim()
    ? graph.name
    : typeof definition?.name === "string" && definition.name.trim()
      ? definition.name
      : "历史工作流";
  const schemaVersion = Number(graph.schema_version || 0);
  const canMigrate = schemaVersion === 1 || schemaVersion === 2;
  const nodeLabel = (nodeId: string, index: number) => {
    const graphNode = graphNodes.find((item) => item.id === nodeId);
    if (typeof graphNode?.label === "string" && graphNode.label.trim()) return graphNode.label;
    const step = definitionSteps.find((item) => item.id === nodeId);
    if (typeof step?.name === "string" && step.name.trim()) return step.name;
    if (typeof step?.label === "string" && step.label.trim()) return step.label;
    return `执行步骤 ${index + 1}`;
  };
  const loadPreview = async () => {
    setPreviewing(true);
    setMigrationError("");
    try {
      setPreview(await workflowsApi.previewVersionMigration(workflowId, versionId));
    } catch (cause) {
      setMigrationError(cause instanceof Error ? cause.message : "迁移预览加载失败");
    } finally {
      setPreviewing(false);
    }
  };
  const copyToV3 = async () => {
    setCopying(true);
    setMigrationError("");
    try {
      if (!preview) return;
      const copied = await workflowsApi.copyVersionToV3(workflowId, versionId, {
        migration_contract_version: preview.migration_contract_version,
        preview_confirmed: true,
        confirmation_token: preview.confirmation_token,
      });
      router.push(copied.designer_url);
    } catch (cause) {
      setMigrationError(cause instanceof Error ? cause.message : "创建 V3 副本失败");
      setCopying(false);
    }
  };
  return <main className="ct-v2-library">
    <header className="ct-v2-page-header"><div><Link className="ct-v2-back-link" href={`/workflows/${encodeURIComponent(workflowId)}/versions`}><ArrowLeft size={15} />版本列表</Link><h1>{displayName} · V{version.version_number}</h1><span>这是不可修改的发布快照，任务运行会引用这个具体版本。</span></div><div>{canMigrate && <button className="ct-v2-primary-button" type="button" onClick={() => void loadPreview()} disabled={previewing}>{previewing ? <Loader2 size={15} className="animate-spin" /> : <CopyPlus size={15} />}{previewing ? "正在生成预览" : "预览并复制为 V3"}</button>}<span className={`ct-v2-status is-${version.state}`}>{version.state === "published" ? "已发布" : version.state === "draft" ? "草稿" : "已归档"}</span></div></header>
    <section className="ct-v2-version-summary"><div><GitBranch size={16} /><strong>{graphNodes.length}</strong><span>画布节点</span></div><div><CheckCircle2 size={16} /><strong>{version.compiled_plan?.topological_order.length ?? 0}</strong><span>执行节点</span></div><div><strong>{graphNodes.filter((node) => node.kind === "output").length}</strong><span>输出契约</span></div></section>
    {migrationError && <div className="ct-v2-notice is-error" role="alert">{migrationError}</div>}
    {preview && <section className="ct-v2-version-plan ct-v2-migration-preview" aria-label="V3 迁移预览"><h2><AlertTriangle size={17} />V3 迁移预览</h2><p>源版本保持只读，下面列出复制后需要复核的变化。</p><dl><div><dt>版本</dt><dd>Schema {preview.source_schema_version} → Schema {preview.target_schema_version}</dd></div><div><dt>输出</dt><dd>{preview.output_changes.source_output_count} 个源输出，迁移 {preview.output_changes.migrated_output_count} 个，丢弃 {preview.output_changes.dropped_output_count} 个</dd></div></dl><h3>仍启用的专业规则</h3>{preview.enabled_professional_rules.length ? <ul>{preview.enabled_professional_rules.map((rule) => <li key={rule}>{rule}</li>)}</ul> : <p>无专业规则。</p>}<h3>需要显式重建的节点</h3>{preview.incompatible_nodes.length ? <ul>{preview.incompatible_nodes.map((node) => <li key={`${node.kind}-${node.label}`}><strong>{node.label}</strong><span>{node.reason}</span></li>)}</ul> : <p>没有不兼容节点。</p>}<p><strong>回滚影响：</strong>{preview.rollback_effect}</p><button className="ct-v2-primary-button" type="button" disabled={!preview.can_apply || copying} onClick={() => void copyToV3()}>{copying && <Loader2 size={15} className="animate-spin" />}{copying ? "正在创建副本" : "确认创建 V3 副本"}</button></section>}
    <section className="ct-v2-version-plan"><h2>冻结的执行计划</h2>{version.compiled_plan ? <ol>{version.compiled_plan.topological_order.map((nodeId, index) => { const node = version.compiled_plan?.nodes.find((item) => item.node_id === nodeId); return <li key={nodeId}><span>{index + 1}</span><strong>{nodeLabel(nodeId, index)}</strong><em>{node?.provider || node?.type}</em><small>{node?.depends_on.length ? `${node.depends_on.length} 个前置步骤` : "无前置步骤"}</small></li>; })}</ol> : <p>该历史版本没有冻结执行计划。</p>}</section>
    <details className="ct-v2-readonly-json"><summary>高级诊断：查看只读编译 JSON</summary><pre>{JSON.stringify(version.compiled_definition, null, 2)}</pre></details>
  </main>;
}

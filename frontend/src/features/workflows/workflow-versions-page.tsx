"use client";

import Link from "next/link";
import { ArrowLeft, Edit3, Eye, GitBranch, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkflowDetail, WorkflowVersion } from "@/lib/types/workflow";

export function WorkflowVersionsPage({ workflowId }: { workflowId: string }) {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    void Promise.all([workflowsApi.get(workflowId), workflowsApi.versions(workflowId)])
      .then(([workflow, result]) => { setDetail(workflow); setVersions(result.items); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "版本加载失败"));
  }, [workflowId]);
  const migrationOnly = detail?.editor_mode === "read_only_legacy" || detail?.editor_mode === "legacy";
  const createDraft = async () => {
    const publishedId = detail?.v2?.published_version_id;
    if (publishedId) {
      const published = await workflowsApi.version(workflowId, publishedId);
      if (requiresMigrationPreview(published)) {
        window.location.href = `/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(publishedId)}`;
        return;
      }
    }
    await workflowsApi.createDraft(workflowId, publishedId ?? undefined);
    window.location.href = `/workflows/${encodeURIComponent(workflowId)}`;
  };
  return <main className="ct-v2-library">
    <header className="ct-v2-page-header"><div><Link className="ct-v2-back-link" href="/workflows"><ArrowLeft size={15} />工作流库</Link><h1>{detail?.presentation?.label ?? detail?.name ?? "工作流"} · 版本</h1></div>{detail?.v2 && !detail.v2.current_draft_version_id && detail.v2.status === "active" && <button type="button" className="ct-v2-primary-button" onClick={() => void createDraft()}><Plus size={15} />{migrationOnly ? "查看 V3 迁移预览" : "创建新草稿"}</button>}</header>
    {error && <div className="ct-v2-notice is-error">{error}</div>}
    <section className="ct-v2-table-shell"><table className="ct-v2-table"><thead><tr><th>版本</th><th>状态</th><th>来源</th><th>创建时间</th><th>发布时间</th><th>操作</th></tr></thead><tbody>{versions.map((version) => <tr key={version.version_id}><td><strong>V{version.version_number}</strong></td><td><span className={`ct-v2-status is-${version.state}`}>{version.state === "draft" ? "草稿" : version.state === "published" ? "已发布" : "已归档"}</span></td><td>{version.based_on_version_id ? <span><GitBranch size={13} /> V{versions.find((item) => item.version_id === version.based_on_version_id)?.version_number ?? "?"}</span> : "初始版本"}</td><td>{formatDate(version.created_at)}</td><td>{version.published_at ? formatDate(version.published_at) : "—"}</td><td><div className="ct-v2-row-actions">{version.state === "draft" ? <Link href={`/workflows/${encodeURIComponent(workflowId)}`} title="编辑草稿"><Edit3 size={15} /></Link> : <Link href={`/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(version.version_id)}`} title="查看只读版本"><Eye size={15} /></Link>}</div></td></tr>)}</tbody></table></section>
  </main>;
}

function requiresMigrationPreview(version: WorkflowVersion) {
  return version.editor_mode === "read_only_legacy" ||
    version.editor_mode === "legacy" ||
    version.authoring_graph.schema_version === 1 ||
    version.authoring_graph.schema_version === 2;
}

function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }

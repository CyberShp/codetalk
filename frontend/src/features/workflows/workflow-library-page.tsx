"use client";

import Link from "next/link";
import {
  Archive,
  CopyPlus,
  Edit3,
  History,
  Plus,
  Search,
  Workflow,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkflowListItem, WorkflowVersion } from "@/lib/types/workflow";

export function WorkflowLibraryPage() {
  const [items, setItems] = useState<WorkflowListItem[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("active");
  const [draftFilter, setDraftFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await workflowsApi.list());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "工作流列表加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const visible = useMemo(() => items.filter((item) => {
    const header = item.v2;
    if (!header) return status === "all" && draftFilter === "all";
    if (status !== "all" && header.status !== status) return false;
    if (draftFilter === "yes" && !header.current_draft_version_id) return false;
    if (draftFilter === "no" && header.current_draft_version_id) return false;
    const haystack = `${item.presentation?.label ?? item.name} ${item.description ?? ""}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  }), [draftFilter, items, query, status]);

  return (
    <main className="ct-v2-library">
      <header className="ct-v2-page-header">
        <div>
          <p>工作流中心</p>
          <h1>工作流</h1>
          <span>管理可复用的执行规范、草稿和发布版本。</span>
        </div>
        <Link className="ct-v2-primary-link" href="/workflows/new"><Plus size={16} />新建工作流</Link>
      </header>

      <section className="ct-v2-filter-bar" aria-label="工作流筛选">
        <label className="ct-v2-search-field"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或描述" /></label>
        <label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="active">使用中</option><option value="archived">已归档</option><option value="all">全部</option></select></label>
        <label><span>草稿</span><select value={draftFilter} onChange={(event) => setDraftFilter(event.target.value)}><option value="all">全部</option><option value="yes">有草稿</option><option value="no">无草稿</option></select></label>
        <output>{visible.length} 个工作流</output>
      </section>

      {error && <div className="ct-v2-notice is-error"><span>{error}</span><button type="button" onClick={() => void load()}>重试</button></div>}
      <section className="ct-v2-table-shell" aria-busy={loading}>
        <table className="ct-v2-table">
          <thead><tr><th>名称</th><th>状态</th><th>已发布版本</th><th>草稿状态</th><th>最近更新</th><th>操作</th></tr></thead>
          <tbody>
            {visible.map((item) => <WorkflowRow key={item.id} item={item} onChanged={load} />)}
          </tbody>
        </table>
        {!loading && !visible.length && (
          <div className="ct-v2-table-empty"><Workflow size={25} /><strong>没有匹配的工作流</strong><span>调整筛选，或创建第一个结构化工作流。</span></div>
        )}
        {loading && <div className="ct-v2-table-empty"><span>正在加载工作流…</span></div>}
      </section>
    </main>
  );
}

function WorkflowRow({ item, onChanged }: { item: WorkflowListItem; onChanged: () => Promise<void> }) {
  const header = item.v2;
  if (!header) return null;
  const displayName = item.presentation?.label ?? item.name;
  const editDraft = header.current_draft_version_id;
  const migrationOnly = item.editor_mode === "read_only_legacy" || item.editor_mode === "legacy";
  const createDraft = async () => {
    const publishedId = header.published_version_id;
    if (publishedId) {
      const published = await workflowsApi.version(item.id, publishedId);
      if (requiresMigrationPreview(published)) {
        window.location.href = `/workflows/${encodeURIComponent(item.id)}/versions/${encodeURIComponent(publishedId)}`;
        return;
      }
    }
    await workflowsApi.createDraft(item.id, publishedId ?? undefined);
    await onChanged();
  };
  return (
    <tr>
      <td><Link href={`/workflows/${encodeURIComponent(item.id)}`}><strong>{displayName}</strong>{item.presentation?.lifecycle === "legacy" && <span className="ct-v2-workflow-badge is-legacy">Legacy</span>}{item.presentation?.scope === "professional" && <span className="ct-v2-workflow-badge">专业</span>}</Link></td>
      <td><span className={`ct-v2-status is-${header.status}`}>{header.status === "active" ? "使用中" : "已归档"}</span></td>
      <td>{header.published_version_id ? `V${item.version || 1}` : "未发布"}</td>
      <td>{editDraft ? <span className="ct-v2-status is-draft">编辑中</span> : "无草稿"}</td>
      <td><time dateTime={header.updated_at}>{formatDate(header.updated_at)}</time></td>
      <td>
        <div className="ct-v2-row-actions">
          {editDraft ? <Link href={`/workflows/${encodeURIComponent(item.id)}`} title="编辑草稿"><Edit3 size={15} /></Link> : header.status === "active" && <button type="button" onClick={() => void createDraft()} title={migrationOnly ? "查看 V3 迁移预览" : "创建新草稿"}><CopyPlus size={15} /></button>}
          <Link href={`/workflows/${encodeURIComponent(item.id)}/versions`} title="查看版本"><History size={15} /></Link>
          {header.status === "active" && <button type="button" onClick={async () => { if (!window.confirm(`归档“${item.name}”？历史版本和运行不会删除。`)) return; await workflowsApi.archive(item.id); await onChanged(); }} title="归档"><Archive size={15} /></button>}
        </div>
      </td>
    </tr>
  );
}

function requiresMigrationPreview(version: WorkflowVersion) {
  return version.editor_mode === "read_only_legacy" ||
    version.editor_mode === "legacy" ||
    version.authoring_graph.schema_version === 1 ||
    version.authoring_graph.schema_version === 2;
}

function formatDate(value: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

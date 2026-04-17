"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import DataTable from "@/components/ui/DataTable";
import CyberInput from "@/components/ui/CyberInput";
import NewAnalysisModal from "@/components/ui/NewAnalysisModal";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { api } from "@/lib/api";
import type { Project, Repository, SourceType } from "@/lib/types";

export default function AssetsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewRepo, setShowNewRepo] = useState(false);
  const [showAnalysis, setShowAnalysis] = useState<string | null>(null);
  const [syncingRepos, setSyncingRepos] = useState<Set<string>>(new Set());
  const [syncErrors, setSyncErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<{id: string; name: string} | null>(null);
  const [deleteRepoTarget, setDeleteRepoTarget] = useState<{id: string; name: string} | null>(null);

  const loadProjects = useCallback(async () => {
    try {
      const data = await api.projects.list();
      setProjects(data);
      if (data.length > 0 && !selectedProject) {
        setSelectedProject(data[0].id);
      }
    } catch (e) {
      console.error("Failed to load projects:", e);
    } finally {
      setLoading(false);
    }
  }, [selectedProject]);

  const loadRepos = useCallback(async () => {
    if (!selectedProject) {
      setRepos([]);
      return;
    }
    try {
      const data = await api.projects.repos(selectedProject);
      setRepos(data);
    } catch (e) {
      console.error("Failed to load repos:", e);
    }
  }, [selectedProject]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    loadRepos();
  }, [loadRepos]);

  const handleDeleteProject = async () => {
    if (!deleteProjectTarget) return;
    try {
      await api.projects.delete(deleteProjectTarget.id);
      setDeleteProjectTarget(null);
      if (selectedProject === deleteProjectTarget.id) setSelectedProject(null);
      loadProjects();
    } catch (e) {
      console.error("Failed to delete project:", e);
    }
  };

  const handleDeleteRepo = async () => {
    if (!deleteRepoTarget) return;
    try {
      await api.repos.delete(deleteRepoTarget.id);
      setDeleteRepoTarget(null);
      loadRepos();
    } catch (e) {
      console.error("Failed to delete repo:", e);
    }
  };

  const handleSync = async (repoId: string) => {
    setSyncingRepos((s) => new Set(s).add(repoId));
    setSyncErrors((e) => { const next = { ...e }; delete next[repoId]; return next; });
    try {
      await api.repos.sync(repoId);
      loadRepos();
    } catch (e) {
      setSyncErrors((prev) => ({
        ...prev,
        [repoId]: e instanceof Error ? e.message : "同步失败",
      }));
    } finally {
      setSyncingRepos((s) => {
        const next = new Set(s);
        next.delete(repoId);
        return next;
      });
    }
  };

  const handleCancelSync = async (repoId: string) => {
    try {
      await api.repos.cancelSync(repoId);
    } catch (e) {
      console.error("Failed to cancel sync:", e);
    }
  };

  const repoColumns = [
    {
      key: "name",
      header: "仓库",
      render: (r: Repository) => (
        <span className="text-on-surface font-medium">{r.name}</span>
      ),
    },
    {
      key: "source",
      header: "来源",
      render: (r: Repository) => (
        <span className="font-data text-xs text-on-surface-variant">
          {r.source_type}
        </span>
      ),
    },
    {
      key: "uri",
      header: "URI",
      render: (r: Repository) => (
        <span className="font-data text-xs text-primary-fixed-dim truncate block max-w-xs">
          {r.source_uri}
        </span>
      ),
    },
    {
      key: "branch",
      header: "分支",
      render: (r: Repository) => (
        <span className="font-data text-xs text-on-surface-variant">
          {r.branch}
        </span>
      ),
    },
    {
      key: "indexed",
      header: "最近索引",
      render: (r: Repository) => (
        <span className="font-data text-xs text-on-surface-variant">
          {r.last_indexed_at
            ? new Date(r.last_indexed_at).toLocaleDateString()
            : "\u2014"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      className: "w-48",
      render: (r: Repository) => (
        <div className="flex gap-2 items-center">
          {syncingRepos.has(r.id) ? (
            <button
              onClick={(e) => { e.stopPropagation(); handleCancelSync(r.id); }}
              className="px-3 py-1 text-xs font-medium rounded-md bg-surface-container-high text-on-surface-variant hover:text-tertiary transition-colors"
            >
              取消
            </button>
          ) : (
            <button
              onClick={(e) => { e.stopPropagation(); handleSync(r.id); }}
              className="px-3 py-1 text-xs font-medium rounded-md bg-surface-container-high text-on-surface-variant hover:text-on-surface transition-colors"
            >
              同步
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); setShowAnalysis(r.id); }}
            disabled={!r.last_indexed_at}
            title={r.last_indexed_at ? "创建分析任务" : "请先同步仓库"}
            className="px-3 py-1 text-xs font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_8px_rgba(164,230,255,0.2)] transition-shadow disabled:opacity-30"
          >
            分析
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setDeleteRepoTarget({id: r.id, name: r.name}); }}
            className="p-1.5 rounded-lg hover:bg-surface-container-highest/50 text-on-surface-variant/50 hover:text-tertiary transition-colors"
            title="删除仓库"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold text-on-surface">
          资产
        </h2>
        <button
          onClick={() => setShowNewProject(true)}
          className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow"
        >
          新建项目
        </button>
      </div>

      <div className="grid grid-cols-[240px_1fr] gap-6">
        {/* Project Tree */}
        <GlassPanel className="h-fit">
          <h3 className="text-xs text-on-surface-variant mb-3">
            项目
          </h3>
          {loading ? (
            <p className="text-xs text-on-surface-variant/50">加载中...</p>
          ) : projects.length === 0 ? (
            <p className="text-xs text-on-surface-variant/50">
              暂无项目。创建一个以开始使用。
            </p>
          ) : (
            <div className="space-y-1">
              {projects.map((p) => (
                <div
                  key={p.id}
                  className={`group flex items-center gap-1 rounded-md transition-colors ${
                    selectedProject === p.id
                      ? "bg-surface-container-high"
                      : "hover:bg-surface-container"
                  }`}
                >
                  <button
                    onClick={() => setSelectedProject(p.id)}
                    className={`flex-1 text-left px-3 py-2 text-sm ${
                      selectedProject === p.id
                        ? "text-primary"
                        : "text-on-surface-variant hover:text-on-surface"
                    }`}
                  >
                    <p className="font-medium">{p.name}</p>
                    <p className="text-xs text-on-surface-variant/60 mt-0.5">
                      {p.repo_count} repo{p.repo_count !== 1 ? "s" : ""}
                    </p>
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeleteProjectTarget({id: p.id, name: p.name}); }}
                    className="p-1.5 mr-1 rounded-lg opacity-0 group-hover:opacity-100 text-on-surface-variant/50 hover:text-tertiary transition-all"
                    title="删除项目"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="3 6 5 6 21 6" />
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Repo Table */}
        <GlassPanel>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xs text-on-surface-variant">
              仓库
              {selectedProject && (
                <span className="text-primary ml-2">
                  {projects.find((p) => p.id === selectedProject)?.name}
                </span>
              )}
            </h3>
            {selectedProject && (
              <button
                onClick={() => setShowNewRepo(true)}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-surface-container-high text-on-surface-variant hover:text-on-surface transition-colors"
              >
                添加仓库
              </button>
            )}
          </div>
          {/* Sync errors */}
          {Object.entries(syncErrors).map(([repoId, msg]) => (
            <div
              key={repoId}
              className="mb-3 px-3 py-2 rounded-md bg-tertiary-container/20 text-xs text-tertiary"
            >
              {repos.find((r) => r.id === repoId)?.name ?? repoId}: {msg}
            </div>
          ))}
          {repos.length > 0 ? (
            <DataTable columns={repoColumns} data={repos} keyField="id" />
          ) : (
            <p className="text-sm text-on-surface-variant/50">
              {selectedProject
                ? "暂无仓库。在上方添加一个。"
                : "选择一个项目以查看仓库。"}
            </p>
          )}
        </GlassPanel>
      </div>

      {/* New Project Modal */}
      {showNewProject && (
        <NewProjectModal
          onClose={() => setShowNewProject(false)}
          onCreated={() => {
            setShowNewProject(false);
            loadProjects();
          }}
        />
      )}

      {/* New Repo Modal */}
      {showNewRepo && selectedProject && (
        <NewRepoModal
          projectId={selectedProject}
          onClose={() => setShowNewRepo(false)}
          onCreated={() => {
            setShowNewRepo(false);
            loadRepos();
            loadProjects();
          }}
        />
      )}

      {/* Analysis Modal */}
      {showAnalysis && (
        <NewAnalysisModal
          repositoryId={showAnalysis}
          onClose={() => setShowAnalysis(null)}
        />
      )}

      <ConfirmDialog
        open={!!deleteProjectTarget}
        title="删除项目"
        description={`确定要删除项目「${deleteProjectTarget?.name}」吗？所有相关仓库及分析任务将被一同删除，此操作不可撤销。`}
        confirmLabel="删除项目"
        variant="danger"
        onConfirm={handleDeleteProject}
        onCancel={() => setDeleteProjectTarget(null)}
      />
      <ConfirmDialog
        open={!!deleteRepoTarget}
        title="删除仓库"
        description={`确定要删除仓库「${deleteRepoTarget?.name}」吗？所有相关分析任务将被一同删除，此操作不可撤销。`}
        confirmLabel="删除仓库"
        variant="danger"
        onConfirm={handleDeleteRepo}
        onCancel={() => setDeleteRepoTarget(null)}
      />
    </div>
  );
}

function NewProjectModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await api.projects.create({ name: name.trim(), description: description.trim() || undefined });
      onCreated();
    } catch (e) {
      console.error("Failed to create project:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface/60 backdrop-blur-sm">
      <GlassPanel className="w-full max-w-md">
        <h3 className="font-display text-sm font-semibold text-on-surface mb-4">
          新建项目
        </h3>
        <div className="space-y-3">
          <CyberInput
            label="项目名称"
            placeholder="my-project"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <CyberInput
            label="描述"
            placeholder="可选描述"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="flex justify-end gap-3 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-on-surface-variant hover:text-on-surface"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim()}
            className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary disabled:opacity-40"
          >
            {saving ? "创建中..." : "创建"}
          </button>
        </div>
      </GlassPanel>
    </div>
  );
}

function NewRepoModal({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [sourceType, setSourceType] = useState<SourceType>("git_url");
  const [name, setName] = useState("");
  const [uri, setUri] = useState("");
  const [branch, setBranch] = useState("main");
  const [saving, setSaving] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim() || !uri.trim()) return;
    setSaving(true);
    try {
      await api.projects.addRepo(projectId, {
        name: name.trim(),
        source_type: sourceType,
        source_uri: uri.trim(),
        branch: branch.trim() || "main",
      });
      onCreated();
    } catch (e) {
      console.error("Failed to add repo:", e);
    } finally {
      setSaving(false);
    }
  };

  const sourceTypes: { value: SourceType; label: string; disabled?: boolean }[] = [
    { value: "git_url", label: "Git URL" },
    { value: "local_path", label: "本地路径" },
    { value: "zip_upload", label: "上传（即将推出）", disabled: true },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface/60 backdrop-blur-sm">
      <GlassPanel className="w-full max-w-md">
        <h3 className="font-display text-sm font-semibold text-on-surface mb-4">
          添加仓库
        </h3>

        {/* Source Type Tabs */}
        <div className="flex gap-1 bg-surface-container-low rounded-lg p-1 mb-4">
          {sourceTypes.map((st) => (
            <button
              key={st.value}
              onClick={() => !st.disabled && setSourceType(st.value)}
              disabled={st.disabled}
              className={`flex-1 px-3 py-1.5 text-xs rounded-md transition-colors ${
                st.disabled
                  ? "text-on-surface-variant/30 cursor-not-allowed"
                  : sourceType === st.value
                    ? "bg-surface-container-high text-on-surface"
                    : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {st.label}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <CyberInput
            label="仓库名称"
            placeholder="my-repo"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <CyberInput
            label={sourceType === "git_url" ? "Git URL" : "路径（当前 runtime 共享仓库目录下）"}
            placeholder={
              sourceType === "git_url"
                ? "https://github.com/owner/repo.git"
                : "/absolute/path/to/shared-repos/myproject"
            }
            value={uri}
            onChange={(e) => setUri(e.target.value)}
          />
          {sourceType === "git_url" && (
            <CyberInput
              label="分支"
              placeholder="main"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
          )}
        </div>

        <div className="flex justify-end gap-3 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-on-surface-variant hover:text-on-surface"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim() || !uri.trim()}
            className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary disabled:opacity-40"
          >
            {saving ? "添加中..." : "添加"}
          </button>
        </div>
      </GlassPanel>
    </div>
  );
}

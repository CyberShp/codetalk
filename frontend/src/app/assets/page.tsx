"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import DataTable from "@/components/ui/DataTable";
import CyberInput from "@/components/ui/CyberInput";
import { api } from "@/lib/api";
import type { Project, Repository, SourceType } from "@/lib/types";

export default function AssetsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewRepo, setShowNewRepo] = useState(false);
  const [loading, setLoading] = useState(true);

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

  const repoColumns = [
    {
      key: "name",
      header: "Repository",
      render: (r: Repository) => (
        <span className="text-on-surface font-medium">{r.name}</span>
      ),
    },
    {
      key: "source",
      header: "Source",
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
      header: "Branch",
      render: (r: Repository) => (
        <span className="font-data text-xs text-on-surface-variant">
          {r.branch}
        </span>
      ),
    },
    {
      key: "indexed",
      header: "Last Indexed",
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
      className: "w-24",
      render: (r: Repository) => (
        <button
          onClick={async () => {
            try {
              const aiEnabled = localStorage.getItem("codetalks_ai_enabled") !== "false";
              const task = await api.tasks.create({
                repository_id: r.id,
                task_type: "full_repo",
                tools: ["deepwiki"],
                ai_enabled: aiEnabled,
              });
              router.push(`/tasks/${task.id}`);
            } catch (e) {
              console.error("Failed to create task:", e);
            }
          }}
          className="px-3 py-1 text-xs font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_8px_rgba(164,230,255,0.2)] transition-shadow"
        >
          Analyze
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold text-on-surface">
          Assets
        </h2>
        <button
          onClick={() => setShowNewProject(true)}
          className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow"
        >
          Add Project
        </button>
      </div>

      <div className="grid grid-cols-[240px_1fr] gap-6">
        {/* Project Tree */}
        <GlassPanel className="h-fit">
          <h3 className="text-xs text-on-surface-variant uppercase tracking-wider mb-3">
            Projects
          </h3>
          {loading ? (
            <p className="text-xs text-on-surface-variant/50">Loading...</p>
          ) : projects.length === 0 ? (
            <p className="text-xs text-on-surface-variant/50">
              No projects yet. Create one to get started.
            </p>
          ) : (
            <div className="space-y-1">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setSelectedProject(p.id)}
                  className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${
                    selectedProject === p.id
                      ? "bg-surface-container-high text-primary"
                      : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                  }`}
                >
                  <p className="font-medium">{p.name}</p>
                  <p className="text-xs text-on-surface-variant/60 mt-0.5">
                    {p.repo_count} repo{p.repo_count !== 1 ? "s" : ""}
                  </p>
                </button>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Repo Table */}
        <GlassPanel>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xs text-on-surface-variant uppercase tracking-wider">
              Repositories
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
                Add Repository
              </button>
            )}
          </div>
          {repos.length > 0 ? (
            <DataTable columns={repoColumns} data={repos} keyField="id" />
          ) : (
            <p className="text-sm text-on-surface-variant/50">
              {selectedProject
                ? "No repositories yet. Add one above."
                : "Select a project to view repositories."}
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
          New Project
        </h3>
        <div className="space-y-3">
          <CyberInput
            label="Project Name"
            placeholder="my-project"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <CyberInput
            label="Description"
            placeholder="Optional description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="flex justify-end gap-3 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-on-surface-variant hover:text-on-surface"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim()}
            className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary disabled:opacity-40"
          >
            {saving ? "Creating..." : "Create"}
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
    { value: "local_path", label: "Local Path" },
    { value: "zip_upload", label: "Upload (Soon)", disabled: true },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface/60 backdrop-blur-sm">
      <GlassPanel className="w-full max-w-md">
        <h3 className="font-display text-sm font-semibold text-on-surface mb-4">
          Add Repository
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
            label="Repository Name"
            placeholder="my-repo"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <CyberInput
            label={sourceType === "git_url" ? "Git URL" : "Path (under /data/repos/)"}
            placeholder={
              sourceType === "git_url"
                ? "https://github.com/owner/repo.git"
                : "/data/repos/myproject"
            }
            value={uri}
            onChange={(e) => setUri(e.target.value)}
          />
          {sourceType === "git_url" && (
            <CyberInput
              label="Branch"
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
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim() || !uri.trim()}
            className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary disabled:opacity-40"
          >
            {saving ? "Adding..." : "Add"}
          </button>
        </div>
      </GlassPanel>
    </div>
  );
}

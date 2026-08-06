"use client";

import {
  AlertTriangle,
  Check,
  FileOutput,
  FolderKanban,
  History,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Tag,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  bindFeatureArtifactProfile,
  bindWorkspaceArtifactProfile,
  createArtifactProfile,
  listArtifactProfiles,
  listArtifactProfileVersions,
  restoreArtifactProfileVersion,
  setDefaultArtifactProfile,
  updateArtifactProfile,
  type ArtifactDefinition,
  type ArtifactFormat,
  type ArtifactProfile,
  type ArtifactProfileDraft,
} from "@/lib/artifact-profiles";
import { api } from "@/lib/api";
import type { Workspace } from "@/lib/types";

const FORMAT_OPTIONS: { value: ArtifactFormat; label: string }[] = [
  { value: "markdown", label: "Markdown" },
  { value: "json", label: "JSON" },
  { value: "csv", label: "CSV" },
  { value: "xlsx", label: "XLSX" },
  { value: "text", label: "Text" },
];

const EMPTY_ARTIFACT: ArtifactDefinition = {
  id: "",
  filename: "",
  format: "markdown",
  required: true,
  instructions: "",
};

function emptyDraft(): ArtifactProfileDraft {
  return { name: "", description: "", scope: {}, artifacts: [{ ...EMPTY_ARTIFACT }] };
}

function profileDraft(profile: ArtifactProfile): ArtifactProfileDraft {
  return {
    name: profile.name,
    description: profile.description,
    scope: profile.scope,
    artifacts: profile.artifacts.map((item) => ({ ...item })),
  };
}

export function ArtifactProfilesView({
  onSelect,
}: {
  onSelect?: (profile: ArtifactProfile) => void;
}) {
  const [profiles, setProfiles] = useState<ArtifactProfile[]>([]);
  const [selected, setSelected] = useState<ArtifactProfile | null>(null);
  const [versions, setVersions] = useState<ArtifactProfile[]>([]);
  const [draft, setDraft] = useState<ArtifactProfileDraft>(emptyDraft);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [bindingWorkspaceId, setBindingWorkspaceId] = useState("");
  const [bindingFeatureTag, setBindingFeatureTag] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const loadProfiles = useCallback(async (selectId?: string) => {
    const loaded = await listArtifactProfiles();
    setProfiles(loaded);
    if (selectId) {
      const next = loaded.find((item) => item.id === selectId) ?? null;
      setSelected(next);
      if (next) setDraft(profileDraft(next));
    }
  }, []);

  useEffect(() => {
    setBusy("load");
    loadProfiles()
      .catch((error) => setMessage(error instanceof Error ? error.message : "加载失败"))
      .finally(() => setBusy(""));
  }, [loadProfiles]);

  useEffect(() => {
    api.workspaces.list().then(setWorkspaces).catch(() => setWorkspaces([]));
  }, []);

  useEffect(() => {
    if (!selected) {
      setVersions([]);
      return;
    }
    listArtifactProfileVersions(selected.id)
      .then(setVersions)
      .catch(() => setVersions([]));
  }, [selected]);

  const canSave = useMemo(
    () =>
      Boolean(
        draft.name.trim() &&
          draft.artifacts.length &&
          draft.artifacts.every(
            (item) => item.id.trim() && item.filename.trim() && item.format,
          ),
      ),
    [draft],
  );

  function chooseProfile(profile: ArtifactProfile) {
    setSelected(profile);
    setDraft(profileDraft(profile));
    setMessage("");
    onSelect?.(profile);
  }

  function updateArtifact(index: number, patch: Partial<ArtifactDefinition>) {
    setDraft((current) => ({
      ...current,
      artifacts: current.artifacts.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    }));
  }

  async function saveProfile() {
    if (!canSave) return;
    setBusy("save");
    setMessage("");
    try {
      const saved = selected
        ? await updateArtifactProfile(selected.id, selected.version, draft)
        : await createArtifactProfile(draft);
      await loadProfiles(saved.id);
      setMessage(`已保存版本 ${saved.version}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy("");
    }
  }

  async function restoreVersion(version: number) {
    if (!selected) return;
    setBusy(`restore-${version}`);
    try {
      const restored = await restoreArtifactProfileVersion(selected.id, version);
      await loadProfiles(restored.id);
      setMessage(`已恢复为新版本 ${restored.version}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "恢复失败");
    } finally {
      setBusy("");
    }
  }

  async function makeDefault() {
    if (!selected) return;
    setBusy("default");
    try {
      await setDefaultArtifactProfile(selected.id);
      setMessage("已设为本机默认档案");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "设置失败");
    } finally {
      setBusy("");
    }
  }

  async function bindWorkspace() {
    if (!selected || !bindingWorkspaceId) return;
    setBusy("bind-workspace");
    try {
      await bindWorkspaceArtifactProfile(bindingWorkspaceId, selected.id);
      setMessage("已更新工作空间绑定");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "绑定失败");
    } finally {
      setBusy("");
    }
  }

  async function bindFeatureTag() {
    const featureTag = bindingFeatureTag.trim();
    if (!selected || !featureTag) return;
    setBusy("bind-feature");
    try {
      await bindFeatureArtifactProfile(featureTag, selected.id);
      setBindingFeatureTag("");
      setMessage(`已绑定特性标签：${featureTag}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "绑定失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="ct-artifact-profiles-workspace grid min-h-0 gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="min-h-0 border-r border-outline-variant/30 pr-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <FileOutput size={16} className="shrink-0 text-primary" />
            <h2 className="truncate text-sm font-semibold text-on-surface">交付件档案</h2>
          </div>
          <button
            type="button"
            title="新建档案"
            onClick={() => {
              setSelected(null);
              setDraft(emptyDraft());
              setMessage("");
            }}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-outline-variant/30 text-on-surface hover:bg-surface-container"
          >
            <Plus size={15} />
          </button>
        </div>
        <div className="space-y-1">
          {profiles.map((profile) => (
            <button
              key={profile.id}
              type="button"
              onClick={() => chooseProfile(profile)}
              className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                selected?.id === profile.id
                  ? "bg-primary/10 text-primary"
                  : "text-on-surface hover:bg-surface-container"
              }`}
            >
              <span className="block truncate text-sm font-medium">{profile.name}</span>
              <span className="mt-0.5 block text-xs text-on-surface-variant">
                v{profile.version} · {profile.artifacts.length} 个产物
              </span>
            </button>
          ))}
          {!profiles.length && busy !== "load" && (
            <p className="px-3 py-4 text-xs text-on-surface-variant">暂无档案</p>
          )}
        </div>
      </aside>

      <section className="min-w-0 space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs text-on-surface-variant">档案名称</span>
            <input
              value={draft.name}
              onChange={(event) => setDraft((item) => ({ ...item, name: event.target.value }))}
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-on-surface-variant">说明</span>
            <input
              value={draft.description ?? ""}
              onChange={(event) =>
                setDraft((item) => ({ ...item, description: event.target.value }))
              }
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
            />
          </label>
        </div>

        <div className="overflow-x-auto border-y border-outline-variant/30">
          <div className="grid min-w-[760px] grid-cols-[150px_190px_120px_72px_minmax(180px,1fr)_36px] gap-2 bg-surface-container px-3 py-2 text-xs font-medium text-on-surface-variant">
            <span>产物 ID</span>
            <span>文件名</span>
            <span>格式</span>
            <span>必需</span>
            <span>内容约定</span>
            <span />
          </div>
          {draft.artifacts.map((artifact, index) => (
            <div
              key={`${index}-${artifact.id}`}
              className="grid min-w-[760px] grid-cols-[150px_190px_120px_72px_minmax(180px,1fr)_36px] gap-2 border-t border-outline-variant/20 px-3 py-2"
            >
              <input
                aria-label={`Artifact ${index + 1} id`}
                value={artifact.id}
                onChange={(event) => updateArtifact(index, { id: event.target.value })}
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5 font-data text-xs text-on-surface"
              />
              <input
                aria-label={`Artifact ${index + 1} filename`}
                value={artifact.filename}
                onChange={(event) => updateArtifact(index, { filename: event.target.value })}
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5 font-data text-xs text-on-surface"
              />
              <select
                aria-label={`Artifact ${index + 1} format`}
                value={artifact.format}
                onChange={(event) =>
                  updateArtifact(index, { format: event.target.value as ArtifactFormat })
                }
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5 text-xs text-on-surface"
              >
                {FORMAT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-2 text-xs text-on-surface">
                <input
                  type="checkbox"
                  checked={artifact.required}
                  onChange={(event) => updateArtifact(index, { required: event.target.checked })}
                />
                <Check size={13} />
              </label>
              <input
                aria-label={`Artifact ${index + 1} instructions`}
                value={artifact.instructions ?? ""}
                onChange={(event) => updateArtifact(index, { instructions: event.target.value })}
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5 text-xs text-on-surface"
              />
              <button
                type="button"
                title="删除产物"
                disabled={draft.artifacts.length === 1}
                onClick={() =>
                  setDraft((item) => ({
                    ...item,
                    artifacts: item.artifacts.filter((_, itemIndex) => itemIndex !== index),
                  }))
                }
                className="grid h-8 w-8 place-items-center rounded-lg text-on-surface-variant hover:bg-error/10 hover:text-error disabled:opacity-30"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() =>
              setDraft((item) => ({
                ...item,
                artifacts: [...item.artifacts, { ...EMPTY_ARTIFACT }],
              }))
            }
            className="inline-flex items-center gap-2 rounded-lg border border-outline-variant/30 px-3 py-2 text-sm text-on-surface hover:bg-surface-container"
          >
            <Plus size={14} />
            添加产物
          </button>
          <button
            type="button"
            onClick={saveProfile}
            disabled={!canSave || Boolean(busy)}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary disabled:opacity-50"
          >
            {busy === "save" ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存版本
          </button>
          {selected && (
            <button
              type="button"
              onClick={makeDefault}
              disabled={Boolean(busy)}
              className="inline-flex items-center gap-2 rounded-lg border border-outline-variant/30 px-3 py-2 text-sm text-on-surface hover:bg-surface-container disabled:opacity-50"
            >
              <Check size={14} />
              设为本机默认
            </button>
          )}
        </div>

        <div className="flex items-start gap-2 border-l-2 border-amber-400 px-3 py-2 text-xs text-on-surface-variant">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
          <span>证据校验、路径校验和清单生成不能被档案关闭。</span>
        </div>

        {selected && (
          <section className="border-y border-outline-variant/30 py-3">
            <h3 className="mb-3 text-sm font-semibold text-on-surface">自动匹配</h3>
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="flex min-w-0 items-end gap-2">
                <label className="min-w-0 flex-1">
                  <span className="mb-1 flex items-center gap-1.5 text-xs text-on-surface-variant">
                    <FolderKanban size={13} />
                    工作空间绑定
                  </span>
                  <select
                    aria-label="工作空间绑定"
                    value={bindingWorkspaceId}
                    onChange={(event) => setBindingWorkspaceId(event.target.value)}
                    className="h-9 w-full rounded-lg border border-outline-variant/30 bg-surface px-2 text-sm text-on-surface"
                  >
                    <option value="">选择工作空间</option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  title="保存工作空间绑定"
                  aria-label="保存工作空间绑定"
                  disabled={!bindingWorkspaceId || Boolean(busy)}
                  onClick={() => void bindWorkspace()}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-outline-variant/30 text-primary hover:bg-primary/10 disabled:opacity-40"
                >
                  {busy === "bind-workspace" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Save size={14} />
                  )}
                </button>
              </div>
              <div className="flex min-w-0 items-end gap-2">
                <label className="min-w-0 flex-1">
                  <span className="mb-1 flex items-center gap-1.5 text-xs text-on-surface-variant">
                    <Tag size={13} />
                    特性标签绑定
                  </span>
                  <input
                    aria-label="特性标签绑定"
                    value={bindingFeatureTag}
                    onChange={(event) => setBindingFeatureTag(event.target.value)}
                    className="h-9 w-full rounded-lg border border-outline-variant/30 bg-surface px-3 text-sm text-on-surface"
                  />
                </label>
                <button
                  type="button"
                  title="保存特性标签绑定"
                  aria-label="保存特性标签绑定"
                  disabled={!bindingFeatureTag.trim() || Boolean(busy)}
                  onClick={() => void bindFeatureTag()}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-outline-variant/30 text-primary hover:bg-primary/10 disabled:opacity-40"
                >
                  {busy === "bind-feature" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Save size={14} />
                  )}
                </button>
              </div>
            </div>
          </section>
        )}

        {selected && versions.length > 1 && (
          <div className="border-t border-outline-variant/30 pt-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-on-surface">
              <History size={15} />
              版本记录
            </div>
            <div className="space-y-1">
              {versions.map((version) => (
                <div
                  key={version.version}
                  className="flex items-center justify-between gap-3 px-2 py-1.5 text-xs text-on-surface"
                >
                  <span>v{version.version} · {version.name}</span>
                  {version.version !== selected.version && (
                    <button
                      type="button"
                      onClick={() => restoreVersion(version.version)}
                      disabled={Boolean(busy)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-primary hover:bg-primary/10 disabled:opacity-50"
                    >
                      {busy === `restore-${version.version}` ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <RotateCcw size={13} />
                      )}
                      恢复此版本
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {message && <p className="text-xs text-on-surface-variant">{message}</p>}
      </section>
    </div>
  );
}

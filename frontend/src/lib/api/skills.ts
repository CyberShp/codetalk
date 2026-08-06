import { request, requestForm } from "@/lib/api";
import type { SkillBuild, SkillDraft, SkillDraftFileWrite, SkillPreset, SkillProject, SkillReview, SkillVersion } from "@/lib/types/skill";

export const skillsApi = {
  listPresets: () =>
    request<{ items: SkillPreset[] }>("/api/skills/presets"),
  createProject: (payload: { name: string; pack_id?: string }) =>
    request<SkillProject>("/api/skills/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getProject: (projectId: string) =>
    request<SkillProject>(`/api/skills/projects/${encodeURIComponent(projectId)}`),
  createDraftFromSource: (
    projectId: string,
    payload: { source_root: string; source_scenario_id: string; skill_id: string },
  ) =>
    request<SkillDraft>(`/api/skills/projects/${encodeURIComponent(projectId)}/drafts/from-source`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  writeDraftFile: (draftId: string, payload: { relative_path: string; content: string }) =>
    request<SkillDraftFileWrite>(`/api/skills/drafts/${encodeURIComponent(draftId)}/files`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importPackage: (projectId: string, file: File, skillIdPrefix = "skill.imported") => {
    const body = new FormData();
    body.set("file", file);
    body.set("skill_id_prefix", skillIdPrefix);
    return requestForm<{ archive_digest: string; archive_root: string; drafts: SkillDraft[] }>(
      `/api/skills/projects/${encodeURIComponent(projectId)}/imports`,
      body,
    );
  },
  buildDraft: (draftId: string) =>
    request<SkillBuild>(`/api/skills/drafts/${encodeURIComponent(draftId)}/builds`, {
      method: "POST",
    }),
  getBuild: (buildId: string) =>
    request<SkillBuild>(`/api/skills/builds/${encodeURIComponent(buildId)}`),
  runReview: (buildId: string, payload: Record<string, unknown> = { scope: "full" }) =>
    request<SkillReview>(`/api/skills/builds/${encodeURIComponent(buildId)}/reviews/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  publishBuild: (buildId: string) =>
    request<SkillVersion>(`/api/skills/builds/${encodeURIComponent(buildId)}/publish`, {
      method: "POST",
    }),
  listVersions: (query: { skill_id?: string } = {}) => {
    const params = new URLSearchParams();
    if (query.skill_id) params.set("skill_id", query.skill_id);
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<{ items: SkillVersion[] }>(`/api/skills/versions${suffix}`);
  },
  getVersion: (versionId: string) =>
    request<SkillVersion>(`/api/skills/versions/${encodeURIComponent(versionId)}`),
  getVersionManifest: (versionId: string) =>
    request<Record<string, unknown>>(`/api/skills/versions/${encodeURIComponent(versionId)}/manifest`),
  getVersionIr: (versionId: string) =>
    request<Record<string, unknown>>(`/api/skills/versions/${encodeURIComponent(versionId)}/ir`),
};

import { currentApiBase } from "./api";

export type ArtifactFormat = "markdown" | "json" | "csv" | "xlsx" | "text";

export interface ArtifactDefinition {
  id: string;
  filename: string;
  format: ArtifactFormat;
  required: boolean;
  schema?: Record<string, unknown>;
  instructions?: string;
}

export interface ArtifactProfile {
  id: string;
  version: number;
  name: string;
  description: string;
  scope: Record<string, unknown>;
  artifacts: ArtifactDefinition[];
  created_at: string;
  restored_from_version?: number | null;
}

export interface ArtifactProfileDraft {
  name: string;
  description?: string;
  scope?: Record<string, unknown>;
  artifacts: ArtifactDefinition[];
}

export interface ArtifactProfileResolution {
  source: string;
  profile: ArtifactProfile | null;
}

async function profileRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${currentApiBase()}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : `交付件档案请求失败 (${response.status})`;
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listArtifactProfiles(): Promise<ArtifactProfile[]> {
  return profileRequest("/api/workbench/artifact-profiles");
}

export function createArtifactProfile(
  profile: ArtifactProfileDraft,
): Promise<ArtifactProfile> {
  return profileRequest("/api/workbench/artifact-profiles", {
    method: "POST",
    body: JSON.stringify(profile),
  });
}

export function updateArtifactProfile(
  profileId: string,
  expectedVersion: number,
  profile: ArtifactProfileDraft,
): Promise<ArtifactProfile> {
  return profileRequest(
    `/api/workbench/artifact-profiles/${encodeURIComponent(profileId)}`,
    {
      method: "PUT",
      body: JSON.stringify({ expected_version: expectedVersion, profile }),
    },
  );
}

export function listArtifactProfileVersions(
  profileId: string,
): Promise<ArtifactProfile[]> {
  return profileRequest(
    `/api/workbench/artifact-profiles/${encodeURIComponent(profileId)}/versions`,
  );
}

export function restoreArtifactProfileVersion(
  profileId: string,
  version: number,
): Promise<ArtifactProfile> {
  return profileRequest(
    `/api/workbench/artifact-profiles/${encodeURIComponent(profileId)}/restore/${version}`,
    { method: "POST" },
  );
}

export function setDefaultArtifactProfile(profileId: string): Promise<void> {
  return profileRequest("/api/workbench/artifact-profiles/default", {
    method: "PUT",
    body: JSON.stringify({ profile_id: profileId }),
  });
}

export function bindWorkspaceArtifactProfile(
  workspaceId: string,
  profileId: string,
): Promise<void> {
  return profileRequest(
    `/api/workbench/artifact-profiles/bindings/workspaces/${encodeURIComponent(workspaceId)}`,
    {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    },
  );
}

export function bindFeatureArtifactProfile(
  featureTag: string,
  profileId: string,
): Promise<void> {
  return profileRequest(
    `/api/workbench/artifact-profiles/bindings/feature-tags/${encodeURIComponent(featureTag)}`,
    {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    },
  );
}

export function resolveArtifactProfile(input: {
  selected_profile_id?: string;
  workspace_id?: string;
  feature_tags?: string[];
}): Promise<ArtifactProfileResolution> {
  return profileRequest("/api/workbench/artifact-profiles/resolve", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

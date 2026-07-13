import { request, requestForm } from "@/lib/api";
import type {
  SemanticCase,
  SemanticCaseFacets,
  SemanticCaseListResult,
  SemanticImportCommitResult,
  SemanticImportPreview,
} from "@/lib/types/semantic";

export interface SemanticCaseQuery {
  q?: string;
  feature?: string;
  module?: string;
  test_level?: string;
  interface?: string;
  tag?: string;
  status?: string;
  source?: string;
  page?: number;
  page_size?: number;
}

const path = (semanticId: string) =>
  `/api/workbench/semantic-cases/${encodeURIComponent(semanticId)}`;

export const semanticLibraryApi = {
  list: (query: SemanticCaseQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    return request<SemanticCaseListResult>(
      `/api/workbench/semantic-cases${params.size ? `?${params}` : ""}`,
    );
  },
  get: (semanticId: string) => request<SemanticCase>(path(semanticId)),
  update: (semanticId: string, data: Partial<SemanticCase>) =>
    request<SemanticCase>(path(semanticId), {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deprecate: (semanticId: string) =>
    request<SemanticCase>(`${path(semanticId)}/deprecate`, { method: "POST" }),
  restore: (semanticId: string) =>
    request<SemanticCase>(`${path(semanticId)}/restore`, { method: "POST" }),
  create: (data: Record<string, unknown>) =>
    request<{ semantic_id: string; case_id: string }>("/api/workbench/semantic-cases", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  facets: () =>
    request<SemanticCaseFacets>("/api/workbench/semantic-cases/facets"),
  previewImport: (file: File, options: Record<string, unknown>) => {
    const body = new FormData();
    body.append("file", file);
    body.append("options_json", JSON.stringify(options));
    return requestForm<SemanticImportPreview>(
      "/api/workbench/semantic-cases/import/preview",
      body,
    );
  },
  commitImport: (
    previewId: string,
    conflictStrategy: "skip" | "overwrite" | "create_new",
  ) =>
    request<SemanticImportCommitResult>("/api/workbench/semantic-cases/import/commit", {
      method: "POST",
      body: JSON.stringify({
        preview_id: previewId,
        conflict_strategy: conflictStrategy,
      }),
    }),
};

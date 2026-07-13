import { request } from "@/lib/api";
import type {
  EvidenceAssetListResult,
  EvidenceFacets,
  EvidenceMemoryItem,
  EvidenceSourceSlice,
} from "@/lib/types/evidence";

export interface EvidenceAssetQuery {
  q?: string;
  workspace_id?: string;
  kind?: string;
  status?: string;
  source?: string;
  page?: number;
  page_size?: number;
}

export const evidenceLibraryApi = {
  list: (query: EvidenceAssetQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    return request<EvidenceAssetListResult>(
      `/api/workbench/evidence${params.size ? `?${params}` : ""}`,
    );
  },
  get: (evidenceId: string) =>
    request<EvidenceMemoryItem & { source_slices: EvidenceSourceSlice[] }>(
      `/api/workbench/evidence/${encodeURIComponent(evidenceId)}`,
    ),
  facets: () => request<EvidenceFacets>("/api/workbench/evidence/facets"),
};

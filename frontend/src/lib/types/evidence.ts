import type { AssetFacet } from "@/lib/types/semantic";

export interface EvidenceSourceSlice {
  slice_id: string;
  evidence_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  sha256: string;
  current_sha256?: string;
  integrity_status?: string;
  validation_error?: string;
  excerpt: string;
  created_at: string;
}

export interface EvidenceMemoryItem {
  evidence_id: string;
  run_id: string;
  workspace_id: string;
  kind: string;
  subject_key: string;
  status: string;
  source: string;
  path: string;
  symbol: string;
  reason: string;
  confidence?: number | null;
  text: string;
  provenance: Record<string, unknown>;
  source_slices?: EvidenceSourceSlice[];
  source_read_status?: string;
  usable_as_source_evidence?: boolean;
  created_at: string;
  updated_at: string;
}

export interface EvidenceAssetListResult {
  items: EvidenceMemoryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface EvidenceFacets {
  workspaces: AssetFacet[];
  kinds: AssetFacet[];
  statuses: AssetFacet[];
  sources: AssetFacet[];
}

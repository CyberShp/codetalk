export interface SemanticCase {
  semantic_id: string;
  case_id: string;
  feature: string;
  module: string;
  test_level: string;
  scenario: string;
  terms: string[];
  tags: string[];
  preconditions: string[];
  actions: string[];
  expected: string[];
  assertion_style: string;
  interface: string;
  source_ref: string;
  status: string;
  created_at: string;
  updated_at: string;
  counts?: { preconditions: number; actions: number; expected: number };
  matched_fields?: string[];
  references?: Array<Record<string, unknown>>;
  raw?: Record<string, unknown>;
}

export type AssetFacet = { value: string; count: number };

export interface SemanticCaseFacets {
  features: AssetFacet[];
  modules: AssetFacet[];
  test_levels: AssetFacet[];
  interfaces: AssetFacet[];
  tags: AssetFacet[];
  statuses: AssetFacet[];
  sources: AssetFacet[];
}

export interface SemanticCaseListResult {
  items: SemanticCase[];
  total: number;
  page: number;
  page_size: number;
  matched_fields: string[];
}

export interface SemanticImportPreviewRow {
  index: number;
  case: Record<string, unknown>;
  errors: string[];
  warnings: string[];
}

export interface SemanticImportPreview {
  preview_id: string;
  source_ref: string;
  total_count: number;
  valid_count: number;
  invalid_count: number;
  missing_case_id: number;
  missing_scenario: number;
  missing_expected: number;
  duplicate_case_ids: string[];
  possible_duplicate_scenarios: string[];
  unknown_fields: string[];
  mapping: Record<string, string>;
  rows: SemanticImportPreviewRow[];
}

export interface SemanticImportCommitResult {
  import_id: string;
  imported_count: number;
  skipped_count: number;
  failed_count: number;
  imported: Array<{ index: number; semantic_id: string; case_id: string }>;
  failed: Array<{ index: number; case: Record<string, unknown>; reasons: string[] }>;
  failure_download_url: string;
}

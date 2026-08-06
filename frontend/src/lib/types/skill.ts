export interface SkillProject {
  project_id: string;
  name: string;
  pack_id: string;
  created_at: string;
  updated_at: string;
}

export interface SkillPreset {
  scenario_id: string;
  skill_id: string;
  label: string;
  description: string;
  source_root: string;
}

export interface SkillDraft {
  draft_id: string;
  project_id: string;
  skill_id: string;
  source_scenario_id: string;
  filesystem_path: string;
  created_at: string;
  updated_at: string;
}

export interface SkillBuild {
  build_id: string;
  draft_id: string;
  status: string;
  version_id: string | null;
  content_digest: string;
  zip_digest: string;
  build_root: string;
  zip_path: string;
  unpacked_root: string;
  ir_path: string;
  validation_report_path: string;
  file_digest_map_path: string;
  manifest_path: string;
}

export interface SkillReview {
  review_id: string;
  build_id: string;
  review_kind: "full" | "incremental" | string;
  decision: "approved" | "changes_requested" | string;
  content_digest: string;
  review_evidence_digest: string;
  record_path: string;
  review_evidence: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  version_id: string;
  project_id: string;
  draft_id: string;
  build_id: string;
  skill_id: string;
  content_digest: string;
  review_evidence_digest: string;
  version_root: string;
  source_zip_path: string;
  unpacked_root: string;
  ir_path: string;
  validation_report_path: string;
  review_records_path: string;
  manifest_path: string;
  created_at: string;
}

export interface SkillDraftFileWrite {
  draft_id: string;
  relative_path: string;
  digest: string;
  file_count: number;
}

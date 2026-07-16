---
feature_ids:
  - workbench-quality-repair
topics:
  - quality-gate
  - iscsi
  - artifact-routing
doc_kind: bug-report
created: 2026-07-16
---

# Professional conflict artifact routing

## Bug diagnosis capsule

| Field | Content |
|---|---|
| Symptom | A combined report contains an invalid black-box oracle, but the quality issue is attributed to `report.md`. Two repair attempts regenerate unrelated stages and leave the offending case unchanged. |
| Evidence | Run `task_run_0f242450603e4c28933c43096b8eeb9f`; `BB-NEW01` says final login succeeds into Operational Negotiation (`CSG=1`). The audit reports `iscsi_final_login_stage_alternatives` against `report.md`. |
| Root cause | `_professional_section_artifact()` only inspects the nearest Markdown heading. The nearest heading is the case title (`### BB-NEW01`), so it misses the parent `## 黑盒测试用例` heading and falls back to `report.md`. The issue also omits the matched statement, so the repair prompt cannot identify the exact row. |
| Diagnostic strategy | Replay the professional constraint matcher against the saved report, print the matched statement, nearest heading and routed artifact, then compare the structured `black_box_cases.json`. |
| Timeout strategy | If a focused test cannot reproduce within 15 minutes, preserve the run artifacts and inspect the report materializer instead of widening regexes. |
| Warning strategy | Stop if a fix weakens the professional constraint or routes all report conflicts to one artifact. |
| User-visible correction | Quality repair edits only the affected black-box case and the deterministic report is rebuilt from corrected structured artifacts. |
| Acceptance | A conflict below a nested `### BB-*` heading is routed to `black_box_cases.json`, includes the offending excerpt and case heading, and the existing correction cases remain accepted. |

## Root cause analysis

The domain constraint worked correctly. The defect was loss of Markdown hierarchy during issue routing: only the leaf heading was considered. Because repair feedback is artifact-scoped, this fallback expanded one row-level defect into broad regeneration of `business_flow.md`, `sfmea.json` and `black_box_cases.json`. The broad repair reduced other findings but did not reliably patch the offending case.

## Fix

Walk headings backwards until a recognized parent section is found, while retaining the leaf heading and matched statement as diagnostic metadata. Do not weaken the iSCSI constraint.

## Verification

Use focused unit tests for nested heading routing and conflict metadata, then rerun the real browser workflow from task creation through report download.

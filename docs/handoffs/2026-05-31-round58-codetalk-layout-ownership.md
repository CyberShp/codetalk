# Round 58 Handoff: CodeTalk Owns Report Layout Artifacts

## What

- Workspace plan-driven reports now prepend deterministic CodeTalk artifacts per section:
  - `### CodeTalk Evidence Table`
  - `### CodeTalk Diagram` for sections marked `requires_mermaid`
  - `### CodeTalk SFMEA Grid` for sections marked `requires_sfmea`
- Legacy workspace report prompts are sanitized at runtime so the AI writes prose/bullets/code excerpts only. The AI prompt no longer carries Mermaid code fences or Markdown table grids; legacy full-report output is decorated with CodeTalk artifacts before validation.
- DeepWiki page generation prompt now delegates the source details block, source table, and diagram to CodeTalk. `WikiOrchestrator._generate_page()` prepends deterministic wiki artifacts to generated page content.
- DeepWiki/workspace reading layer now tolerates invalid legacy Mermaid blocks: `MermaidRenderer` uses `suppressErrorRendering` and `mermaid.parse(..., { suppressErrors: true })` before rendering, preventing Mermaid's injected `Syntax error in text` document from polluting old cached pages.
- Added `settings.deepwiki_base_url` as a backward-compatible alias for older DeepWiki routes still using that name.

Touched files:
- `backend/app/services/report_artifacts.py`
- `backend/app/services/report_generator.py`
- `backend/app/prompts/templates.py`
- `backend/app/services/wiki_prompts.py`
- `backend/app/services/wiki_orchestrator.py`
- `backend/app/services/wiki_artifacts.py`
- `backend/app/config.py`
- `frontend/src/components/ui/MermaidRenderer.tsx`
- `backend/tests/test_report_codetalk_artifacts.py`
- `backend/tests/test_wiki_orchestrator.py`

## Why

- Intranet/small LLMs are prone to truncating or corrupting Markdown tables, Mermaid diagrams, and SFMEA grids. Those are layout artifacts, not reasoning artifacts.
- The product target is: AI generates the analytical prose; CodeTalk owns document assembly, formatting, tables, graphs, and stable rendering.
- Existing cached DeepWiki pages can still contain old invalid Mermaid. Future prompt fixes are not enough for the current reading experience, so the renderer also needs to fail closed.
- `embedding` only consuming about 12k tokens is a signal that DeepWiki likely did a shallow/index-summary style ingestion for the smoke-sized repo or skipped/failed deeper document embedding. It should not be interpreted as "full repo/document reading succeeded"; report generation must surface coverage and should not rely on embedding token count alone as accuracy evidence.

## Tradeoff

- Deterministic artifacts are intentionally generic in this round. They prove ownership and prevent truncation, but they are not yet a fully semantic architecture graph.
- Runtime prompt sanitization keeps old templates working without rewriting every Chinese template block. This is lower-risk, but future cleanup should delete obsolete raw template examples to reduce confusion during maintenance.
- Old cached DeepWiki content is not rewritten automatically. The reader now handles invalid Mermaid safely; newly regenerated pages will get CodeTalk source table/page graph artifacts.
- The `deepwiki_base_url` alias preserves old route compatibility instead of touching every route in this round.

## Open Questions

- Should CodeTalk generate richer semantic tables/diagrams from GitNexus/CGC relationships instead of the current evidence/source summaries?
- Should cached DeepWiki pages be migrated/redecorated on read, so old pages also display `CodeTalk Source Table` and `CodeTalk Page Graph` without regeneration?
- `backend/tests/test_repo_wiki_routes.py` imports now pass after the config alias, but all 9 tests return 404 because `repo_wiki` routes are not mounted on the current `app.main` path expected by the tests. This appears pre-existing/unrelated to the layout change.
- `backend/tests/e2e/test_deepwiki.py` timed out at 120s in this environment. Treat as blocked by test harness/external service setup unless narrowed further.
- `fast-context` remains unavailable: `Windsurf API Key not found`.

## Next Action

1. Dev AI should review the layout ownership boundary:
   - AI prompts should ask for prose and evidence-backed facts only.
   - CodeTalk should own Markdown tables, Mermaid blocks, SFMEA grids, details/source sections, and export formatting.
2. Consider a follow-up migration for cached DeepWiki pages:
   - Either decorate page content on read when CodeTalk artifacts are missing, or bulk rewrite cache entries once.
3. Fix/mount `repo_wiki` routes or update stale route tests so `backend/tests/test_repo_wiki_routes.py` can become useful regression coverage again.
4. If richer accuracy is desired, build CodeTalk semantic artifact generators from GitNexus/CGC edges rather than relying on generic evidence rows.

## Verification

- `backend/tests/test_report_codetalk_artifacts.py backend/tests/test_wiki_orchestrator.py`: 12 passed.
- `backend/tests/test_report_codetalk_artifacts.py backend/tests/test_round2_fixes.py backend/tests/test_export_service.py::TestExportWorkspaceReports`: 20 passed.
- `backend/tests/test_workspace_pipeline.py`: 15 passed, 6 existing asyncio-marker warnings.
- `backend/tests/test_export_service.py`: 20 passed, 12 existing asyncio-marker warnings.
- `npm run lint -- src/components/ui/MermaidRenderer.tsx`: passed.
- Runtime prompt audit: no `EXTENSIVELY use Mermaid diagrams`, `Use Markdown tables for structured data`, Mermaid fences, or table separator grids in imported workspace/wiki prompt constants.
- Smoke generation:
  - `backend/data/outputs/codetalk-artifact-smoke-round58/11-模块地图.md`
  - status completed; contains CodeTalk evidence table and diagram; captured AI prompt has no Mermaid fence/table grid.
- Direct export API after backend restart:
  - `GET /api/workspaces/975784c8-8061-43d0-ab3c-77c7548ec940/export?format=md&task_id=bb8b2abf-bdcf-4ff0-8fb3-8a9ab93c922a`
  - 200, `application/zip`, filename `workspace-975784c8-bb8b2abf.zip`, 53339 bytes.
- Browser point-click regression:
  - Opened workspace page, clicked `模块地图`, report rendered via Markdown renderer with no console errors.
  - Clicked `MD` export button; in-app browser reports downloads unsupported, but click produced no console errors and backend direct export endpoint passed.
  - Opened DeepWiki repo page and clicked `DeepWiki Smoke`; after Mermaid renderer fix/reload, `Syntax error in text` no longer appears and console errors are empty.
  - Screenshots:
    - `frontend/manual-round58-workspace-report-export.png`
    - `frontend/manual-round58-deepwiki-mermaid-safe.png`

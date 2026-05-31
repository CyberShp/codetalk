# Round 59 Handoff: Repo Wiki Routes and DeepWiki E2E Regression

## What

- Mounted `repo_wiki.router` in `backend/app/main.py`, fixing `/api/repos/{repo_id}/wiki*` routes returning 404.
- Synchronized the e2e test app builder in `backend/tests/e2e/conftest.py` with the runtime router set, including `repo_wiki.router` and the existing `ws.router`.
- Restarted the backend on port 8100 so the running instance has the fixed route table.

## Why

- `backend/app/api/repo_wiki.py` already defined the repo-level wiki endpoints, but `app.main` never included the router. The tests were correct to expect these endpoints to exist.
- The e2e app builder claims to mirror `main.py`; if it omits routers, route-level regressions can hide in either direction.

## Tradeoff

- This is a minimal routing fix. It does not change repo-wiki business behavior, cache semantics, or background generation logic.
- The e2e test suite was previously reported as timed out at 120s. After the route fix and rerun, `test_deepwiki.py` completed in about 65s; no production code change was needed for that part.

## Open Questions

- `fast-context` remains unavailable because `WINDSURF_API_KEY` is not configured.
- The repo-level wiki API still shares pieces from `app.api.wiki`; a later cleanup could reduce coupling, but it was not needed for this fix.

## Next Action

- Dev AI can review router registration consistency whenever new API modules are added: runtime `app.main` and e2e `_build_e2e_app()` should stay in lockstep.
- Consider adding a lightweight route registration test for all public routers to catch missing `include_router()` earlier.

## Verification

- `backend/tests/test_repo_wiki_routes.py`: 9 passed.
- `backend/tests/e2e/test_deepwiki.py`: 17 passed.
- Combined run: `backend/tests/test_repo_wiki_routes.py backend/tests/e2e/test_deepwiki.py`: 26 passed.
- Layout ownership regression guard: `backend/tests/test_report_codetalk_artifacts.py backend/tests/test_wiki_orchestrator.py`: 12 passed.
- `py_compile backend/app/main.py backend/tests/e2e/conftest.py`: passed.
- Runtime backend restarted on port 8100, listener PID `67336`.
- Browser/API smoke:
  - Opened `http://localhost:8100/api/repos/11111111-1111-4111-8111-111111111111/wiki/status`
  - Response: `{"running":false,"current":0,"total":0,"page_title":"","error":null}`
  - Opened `http://localhost:3005/deepwiki`; console errors empty.

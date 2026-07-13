---
feature_ids:
  - workbench-v2
topics:
  - independent-review
  - remediation
doc_kind: review-response
created: 2026-07-14
---

# Workbench V2 Phase 8 Review Response

## Findings resolved

1. P1 input leakage: compiled port bindings are now executed and every Agent handoff is scoped to
   connected inputs. Tests cover unbound user text at preparation and runtime.
2. P1 status coupling: `needs_rework` is completed execution with blocked quality, and legacy terminal
   values normalize on read.
3. P1 rollback bypass: layouts gate all four direct V2 route families; real false-flag browser
   coverage verifies the legacy destinations.
4. P1 legacy draft: a published migrated definition is converted into an editable schema-2 graph
   when a new draft is created.
5. P1 secret leak: public event payloads recursively redact exception strings before persistence.
6. P2 mutable failure policy: request `stop_on_error` no longer rewrites frozen V2 plan nodes.
7. P2 truncated pagination: common pages use SQL count/pagination; status filtering scans all rows in
   bounded batches before applying the requested page.
8. P2 built-in overrides: server rejects Provider/MCP/Skills overrides for non-Agent nodes and the
   Task wizard lists only Agent nodes.
9. P2 SSE recovery: transient errors refresh state without closing EventSource; native reconnect stays
   active.
10. P3 pause semantics: the cockpit freezes at an event-ID boundary and keeps prior rows visible.

## First re-review findings resolved

1. P1 legacy event disclosure: `list_after` and `list_before` now recursively redact a persisted
   event at the public read boundary. A regression test directly seeds an upgrade-era JSONL record
   containing nested bearer/API-key values and proves both paging directions return only redacted
   content.
2. P2 pause/reconnect history loss: quiet refreshes merge the latest tail instead of replacing pages
   already loaded by the user. Pausing also captures an independent visible-event snapshot, so more
   than one live-window of new events or a transient SSE recovery cannot blank or rewrite what the
   user paused to inspect.

## Additional browser defect resolved

The post-review real browser run exposed a Task-center select whose React delegated event did not
commit App Router navigation. The implementation now binds the native select change event and performs
a complete URL replacement. The E2E proves the URL filter applies, and explicitly clears a no-longer-
applicable execution filter after a new Attempt changes the latest task state.

## Verification

- Backend final full suite: `2260 passed, 8 skipped` in `1232.35s`.
- Frontend: ESLint, TypeScript, and production build passed.
- Static frontend contracts: `45 passed`.
- Post-fix focused backend event/scheduler tests: `14 passed`.
- Real Chromium journeys after rebuilding against the isolated API: `10 passed` in `23.9s`, including false-flag rollback, desktop containment, mobile
  Evidence view, workflow creation/publication, Task creation/Attempt lineage, cockpit pause, and asset
  management. No network interception or mock was used.
- `git diff --check`: passed.

## Final independent decision

The original independent reviewer re-inspected both remediation paths and independently ran five
backend event/API tests, seven frontend release contracts, and `git diff --check`. Final findings:
P0 none, P1 none, P2 none. One non-blocking P3 notes that the browser suite does not actively inject
an SSE transport failure. Decision: **APPROVED**.

## Declared residual boundary

The supported deployment is one backend process. Migration-backup and Attempt-number locks are
process-local; multi-worker deployment against one data directory is not supported until database-
owned coordination is implemented. Windows real-machine acceptance is deferred by the Goal, while
automated command-resolution coverage remains green.

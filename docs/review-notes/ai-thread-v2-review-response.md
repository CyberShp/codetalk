---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - independent-review
  - red-green
  - ai-thread
doc_kind: review-response
created: 2026-07-15
---

# AI Thread V2 Review Response

Review target: `ai-thread-v2-integration`

## Red-to-Green findings

| Finding | Root cause | Resolution | Focused evidence |
|---|---|---|---|
| P1 superseded built-in version accepted | AI Task Draft checked publication but not the active built-in header pointer | Reject any reserved built-in version other than `published_version_id` | `test_ai_task_draft_rejects_superseded_builtin_version` |
| P1 runtime execution drift | Public Run snapshot was frozen, but the scheduler reloaded mutable command/args/env | Persist a private execution snapshot with the Run; scheduler uses it while public payloads remove it | `test_scheduler_uses_run_runtime_snapshot_not_current_conversation_runtime` |
| P1 provider capacity bypass | Custom runtime schema did not persist provider | Add validated provider storage, migration, inference, API field, and Settings selection | `test_custom_agent_runtime_persists_explicit_provider` |
| P1 exact Attempt evidence displaced | Workspace refs filled the 14-ref cap before Task Run artifacts | Load exact Task Run refs first and preserve frozen workflow/current-node facts | `test_task_run_context_keeps_exact_attempt_refs_when_workspace_is_busy` |
| P1 duplicate Run discussion threads | Create-or-open performed link lookup and create without one operation boundary | Serialize by Task Run and recheck existing links before creation | concurrent `test_run_cockpit_bridge_reuses_ai_thread_and_keeps_context_public` |
| P1 Agent prompt leaked host data | Only selected diagnostics/references were redacted | Redact the complete prompt, replace the bound repo with `<workspace>`, and remove other host-local absolute paths without corrupting URLs | `test_agent_prompt_redacts_reference_secrets_and_absolute_paths` |
| P2 Task Draft was not idempotent | Message and Run ownership were checked independently; repeat POST created another Task | Require the exact message/Run pair and replay the existing source/workflow Task with HTTP 200 | `test_ai_task_draft_validates_source_pair_and_replays_idempotently` |
| P2 queue position became stale | Queue callback fired only when first enqueued | Recompute remaining waiter positions after release/cancel and suppress unchanged notifications | `test_agent_run_coordinator_refreshes_remaining_queue_positions` |

## Additional browser finding

Real Agent E2E exposed a macOS sandbox policy gap for `python wrapper.py` and equivalent launchers:
the command binary was readable but an absolute wrapper/config path in argv was not. The bridge now
adds only configured, existing absolute argument paths to the read-only boundary. The repository and
all undeclared host paths remain protected. `test_stream_runtime_allows_configured_local_wrapper_script_readonly`
and the real source-injection browser test cover the fix.

## Gate evidence

- Core backend: `333 passed in 82.61s`.
- Agent CLI sandbox: `16 passed`.
- Agent Runtime/CLI regression: `128 passed`; pytest printed completion but an existing non-daemon
  test thread required process shutdown after the result.
- ESLint, TypeScript, and Next.js production build: passed.
- Browser: eight main AI/Workbench tests, one sandboxed source Agent test, and one quality-retry test passed.
- `git diff --check`: clean; synthetic redaction keys are confined to tests.

## Re-review request

Please verify each original finding against this response and the current branch. Approval requires
an explicit statement that no unresolved P0/P1/P2 remains.

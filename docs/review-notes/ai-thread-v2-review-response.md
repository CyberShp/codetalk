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

- Core backend: `336 passed in 79.52s`.
- Agent CLI sandbox: `17 passed`.
- Agent Runtime/CLI regression: `128 passed`; pytest printed completion but an existing non-daemon
  test thread required process shutdown after the result.
- ESLint, TypeScript, and Next.js production build: passed.
- Browser: eight main AI/Workbench tests, one sandboxed source Agent test, and one quality-retry test passed.
- `git diff --check`: clean; synthetic redaction keys are confined to tests.

## Second re-review response

| Finding | Resolution | Focused evidence |
|---|---|---|
| P1 dynamic prompt widened sandbox reads | Capture only configured `args`/`resume_args` before transports add prompt or session values; dynamic argv is never scanned for paths | `test_stream_runtime_never_allows_an_absolute_prompt_as_a_read_path` |
| P1 assistant task card failed source pairing | Accept an assistant message when its `run_id` is the selected Run; user sources still must equal `input_message_id`; one-sided source pairs are rejected | `test_ai_task_draft_accepts_assistant_card_and_source_less_replay` |
| P1 queue callback failure stranded capacity | Initial callback now executes inside slot cleanup; refresh callback failures are logged without interrupting release | `test_agent_run_coordinator_cleans_waiter_when_queue_callback_fails` |
| P2 source-less draft replay duplicated Tasks | Serialize draft creation per conversation, resolve workflow/version inside the boundary, and replay empty-source links as well as sourced links | `test_ai_task_draft_accepts_assistant_card_and_source_less_replay`; real V2 browser loop |
| P3 operation locks leaked | Reference-count lock users and evict the registry entry after success, error, or cancellation | Task Draft and concurrent Run-to-AI tests assert empty registries |

The next re-review identified an adjacent P1 grant/cancel handoff race. `_Waiter` now records explicit
grant ownership; if cancellation lands before the slot context resumes, `_cancel_waiter()` returns
the granted provider capacity and wakes the next eligible waiter. The deterministic regression is
`test_agent_run_coordinator_releases_a_granted_waiter_cancelled_before_resume`.

The configured-wrapper sandbox exception remains read-only. Its path comes only from administrator
runtime configuration; user prompt text, generated session identifiers, and transport-added argv do
not participate in the read allowlist.

## Re-review request

Please verify each original finding against this response and the current branch. Approval requires
an explicit statement that no unresolved P0/P1/P2 remains.

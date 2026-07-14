---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - independent-review
  - ai-thread
  - workbench-v2
doc_kind: review-request
created: 2026-07-15
---

# Review Request: AI Thread V2 Integration

Review-Target-ID: ai-thread-v2-integration
Branch: `codex/ai-thread-v2-integration`

## What

Review the full diff from `831493ec` through the current branch: immutable AI Run snapshots,
AI/Workbench links, AI-to-Task Draft and Run-to-AI bridges, atomic conversation scheduling, Agent
capacity coordination, per-message Run Cards, structured public timelines, Run Cockpit explanations,
artifact-grounded follow-up Agents, compatibility migrations, and the real browser acceptance flow.

## Why

The release must provide one truthful AI Thread -> Published Workflow Version -> Task -> immutable
Attempt -> DAG Scheduler -> Run Cockpit -> linked AI loop. It must not simulate workflow execution by
merging multiple nodes into one prompt, fabricate historical configuration, or expose private model
reasoning and diagnostics.

## Original Requirements

> “完成 CodeTalk 的真实闭环：AI Thread → 创建 V2 Task Draft → 固定 Published Workflow Version
> → 六步任务配置 → 创建不可变 Run Attempt → 使用 Compiled Execution Plan 和 DAG Scheduler 运行
> → 在 Run Cockpit 展示真实节点、事件、质量和交付件 → 从 Run Cockpit 创建或打开关联 AI Thread。”

- Source: `/Users/shepard/.codex/attachments/ed885349-4b9a-4f58-a9ef-1bec7850cdc3/pasted-text.txt`
- Please judge the implementation against this product chain and all 25 Definition of Done items.

## Tradeoff

AI and Workbench share object contracts and links but not a new runner. The coordinator covers AI
Agent subprocesses now; Workbench Agent nodes retain their established runner, matching the goal's
explicitly permitted boundary. Legacy routes and rows remain readable for one release but are no
longer primary V2 actions.

## Open Questions

- Can any browser input, selected provider, Skill, MCP, workflow version, or artifact contract be
  lost or replaced between AI Thread, Task Draft, wizard, immutable Attempt, and Agent prompt?
- Can concurrent posts, duplicate kicks, cancel/failure advancement, or spawn errors create two
  running Agents or strand queued work?
- Can current runtime/workflow state leak into historical Run Cards or legacy rows?
- Can a declared custom output, malicious relative path, local absolute path, secret, raw command,
  or private diagnostic escape into public artifacts or Agent context?
- Does any control still imply a structured action while only editing the composer, or imply DAG
  execution while only constraining one AI answer?
- Does the real E2E actually exercise the compiled two-node DAG and exact Attempt loop without a
  mocked business response?

## Next Action

Return findings first, ordered P0/P1/P2/P3 with tight file/line references and a concrete failure
scenario. Review source and tests, not only the test totals. Explicitly state whether any P0/P1/P2
remains; do not approve if the product chain, data truth, security, or user-visible semantics diverge.

## Review Sandbox

- Path: `/tmp/cat-cafe-review/ai-thread-v2-integration/kierkegaard`
- Start command: use repository-native backend/frontend commands or Playwright webServer from a
  detached read-only checkout.
- Reserved ports if browser verification is needed: `web=3201`, `api=3202`.

## Self-check evidence

- Quality gate: `docs/review-notes/ai-thread-v2-quality-gate.md`.
- Backend relevant suite: `327 passed in 99.75s`.
- Frontend lint, TypeScript, production build: exit 0.
- Real Chromium integration and Workbench group: `7 passed in 43.6s` on `3013/3014`.
- Focused retry and source-first Agent browser regressions: passed.
- `git diff --check` and root artifact hygiene: clean.

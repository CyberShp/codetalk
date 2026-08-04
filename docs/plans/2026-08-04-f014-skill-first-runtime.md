---
feature_ids: [F014]
topics: [implementation-plan, skill-first, acceptance, subagents, tdd]
doc_kind: plan
created: 2026-08-04
---

# F014 Skill-first Product and Runtime Implementation Plan

**Feature:** F014 - `docs/features/F014-skill-first-runtime.md`
**Goal:** Replace the Workflow product path with a complete, immutable, verifiable Skill-first path while preserving the supplied Codetalks v2.4 semantics.
**Acceptance Criteria:** AC-A1 through AC-E6 in the F014 spec.
**Architecture:** Compile mutable Skill Project files into immutable Skill Versions and a terminal Skill IR. Task Run orchestrates the frozen Skill Invocation through the existing Harness, Attempt, event, checkpoint, artifact, cancellation, recovery, and cockpit mechanisms on main.
**Tech Stack:** Python 3.11+, FastAPI, Pydantic, JSON Schema 2020-12, SQLite, filesystem ZIP artifacts, pytest, Next.js, TypeScript, Playwright.
**Frontend validation:** Yes - browser operation, screenshots, and responsive checks are mandatory.

---

## 1. Finish Line

The feature is complete only when the supplied archive imports as five Skills,
the module-analysis Skill publishes immutably, a Task directly binds it, a real
CodeAgent executes all nine steps, an independent Judge controls READY, selected
delivery filters presentation rather than execution, restart/cancel are durable,
and no live Workflow product path or professional `ai_staged_execution` entry
remains.

We are not building dynamic pruning, multi-Skill orchestration, F012/F013
capabilities, a marketplace, multi-user control, object storage, or a new
distributed runtime.

## 2. Terminal Contracts

Create under `backend/app/schemas/skills/`:

1. `codetalk-skill-v1.schema.json`
2. `codetalk-skill-pack-v1.schema.json`
3. `skill-ir-v1.schema.json`
4. `skill-review-v1.schema.json`
5. `skill-run-invocation-v1.schema.json`
6. `agent-capability-report-v1.schema.json`

Stable IDs are semantic (`step.flow_analysis`, `artifact.flow_model`,
`delivery.full_report`) and never derived from filenames. The Run Invocation is
the only frozen bridge from Skill domain to runtime. It owns the Skill digest,
input snapshot reference, selected deliveries, runtime/capability report,
session reference, artifact root, and Judge declaration.

## 3. Acceptance Strategy

### 3.1 Deterministic archive and contract gate

Pin the source SHA-256 and create a checked-in minimal fixture with the same
semantic topology. Test archive traversal, absolute paths, symlinks, duplicate
normalized Unicode paths, invalid encodings, missing references, duplicate IDs,
cycles, missing artifact producers, unconsumed outputs, undeclared scripts, and
multi-scenario Skill rejection.

Golden assertions for the supplied source:

- 37 archive files accounted for;
- five independent scenario Skills in one Pack;
- module analysis contains nine ordered steps;
- three mandatory core-rule acknowledgements;
- 37 required artifact declarations;
- eight formal outputs;
- required depth Judge with isolated-session policy;
- `run_guard.py` declared with timeout, cwd, exit code, logs, and write scope.

### 3.2 Build and immutability gate

Build twice from identical bytes and compare IR, package ZIP, file digest map,
and release digest. Mutation of a released path must fail. External Draft edits
must appear only after rescan. AI patches are stored as proposals and require an
explicit apply decision followed by a new deterministic build.

### 3.3 Runtime contract gate

Agent 生命周期不等同于一个子进程的生命周期。验收时必须分别观察：

- **Run Attempt**：CodeTalk 的持久任务真相源；
- **Agent Session**：可恢复的上下文、Session ID、能力和 checkpoint；
- **Agent Process**：一次可被杀死和重建的具体执行进程。

先使用确定性 Fake Agent 跑完整矩阵，再对每个真实 Runtime 跑其支持的
同一份契约：

| 场景 | 操作 | 必须观察到的结果 |
|---|---|---|
| 创建启动 | capability discovery -> preflight -> create -> start | 冻结 Invocation；事件严格有序；只出现一个活动 Session |
| 正常事件 | 输出文本、工具调用、写工件、等待输入、恢复 | 事件持久化；Agent 自述不能直接改变 Run 状态；checkpoint 后才完成步骤 |
| 杀进程 | 在步骤中强杀 Agent Process | Run 不误报成功；已提交工件保留；临时输出丢弃；新进程从 checkpoint 恢复 |
| CodeTalk 重启 | 在运行中重启后端 | 启动扫描找回未完成 Run；不重跑已完成步骤；不读取可变 Draft |
| Session 失效 | 删除/损坏 Session 或返回 session-not-found | 记录原因；只允许一次 clean-session recovery；禁止无限 resume 循环 |
| 重复取消 | 连续发出两次取消 | 结果幂等；父子进程全部终止；取消后禁止写正式工件、启动 Judge 或 completed |
| 分层超时 | 分别触发 queue、Agent、script、validation、overall timeout | 终态和原因可区分；超时清理完整；剩余预算不被伪装成 Agent 执行时间 |
| Judge 隔离 | Producer 完成后启动 Judge | 两个独立 Session；Judge 无 Producer 对话；未审为 PENDING_VALIDATION，通过才 READY |
| 能力降级 | Runtime 不支持 resume/tool/cancel | capability report 明确 unsupported；按契约降级或阻断，禁止静默忽略 |

Fake Agent 必须能主动发出 message、tool、artifact、waiting、resume、failure、
completion，并能被测试控制在任意 checkpoint 前后终止。所有场景都断言
checkpoint-before-projection、一个且仅一个终态、进程树清理，以及运行时不能
访问可变 Draft 文件。

### 3.4 Real vertical gate

Run `codetalks-module-full-analysis` with the company CodeAgent against a local
source/design fixture. Record source SHA, Skill Version/digest, invocation,
capability report, event log, checkpoint, artifact manifest, Producer session,
Judge session, and delivery package. No secrets or environment-specific paths
may enter committed evidence.

### 3.5 Product and removal gate

Operate Task creation, Skill/Pack views, Run Cockpit, blocked/degraded state,
Judge transition, delivery selection, cancel, and restart in a real browser.
After the vertical gate is green, source/API/route tests must prove the old
Workflow product surface is absent without deleting reusable runtime machinery.

## 4. TDD Delivery Sequence

### Task 0: Architecture boundary and asset matrix

**Files:**

- Create: `docs/decisions/adr-027-skill-first-product-model.md`
- Create: `docs/decisions/adr-028-skill-build-release-review.md`
- Create: `docs/decisions/adr-029-skill-runtime-boundary.md`
- Create: `docs/contracts/AGENT_RUNTIME_CONTRACT.md`
- Create: `docs/plans/skill-first-existing-asset-matrix.md`

Freeze the three terminal decisions before production code: the product model,
the build/release/review authority model, and the Skill-to-runtime boundary.
The Runtime Contract must describe only capabilities available from main or
explicitly required by F014; it must not copy F013 code or event vocabulary.
The asset matrix classifies every Workflow, Task, Run, Harness, checkpoint,
event, artifact, delivery, cockpit, and staged-analysis module as reuse, adapt,
remove, or exclude, with caller/callee evidence.

### Task 1: Source-to-target trace fixture

**Files:**

- Create: `backend/tests/fixtures/skills/codetalks-v2.4/source-inventory.json`
- Create: `backend/tests/fixtures/skills/codetalks-v2.4/expected-ir-summary.json`
- Create: `backend/tests/test_skill_source_inventory.py`

Write failing tests for the pinned archive inventory, UTF-8 filenames, scenario
split, step/artifact/output counts, and source-to-IR traceability. The test may
use a caller-provided archive path locally; CI uses the checked-in minimal
semantic fixture and never depends on Downloads.

### Task 2: Six schemas and adversarial fixtures

**Files:**

- Create: `backend/app/schemas/skills/*.schema.json`
- Create: `backend/tests/fixtures/skills/contracts/`
- Create: `backend/tests/test_skill_schemas.py`
- Modify: `backend/requirements.txt`

Write positive and negative fixtures first, verify schema failures, then add the
minimal schemas. Unknown terminal fields fail closed. Schema IDs and references
must resolve without network access.

### Task 3: Safe importer and Pack split

**Files:**

- Create: `backend/app/services/skill_package_importer.py`
- Create: `backend/app/services/skill_package_paths.py`
- Create: `backend/tests/test_skill_package_importer.py`

Reject unsafe archives before extraction, normalize and retain UTF-8 paths,
produce an inventory with content hashes, and split the five source workflow
variants into independent draft Skills. Do not infer scenarios from prose when
the source manifest already declares them.

### Task 4: Deterministic validator and IR compiler

**Files:**

- Create: `backend/app/services/skill_package_validator.py`
- Create: `backend/app/services/skill_ir_compiler.py`
- Create: `backend/tests/test_skill_package_validator.py`
- Create: `backend/tests/test_skill_ir_compiler.py`

Validate references, IDs, dependencies, producers/consumers, outputs, scripts,
Judge contract, and file paths. Compile only validated input. Golden tests bind
every IR field to a source file or explicit deterministic default.

### Task 5: Skill store and immutable build

**Files:**

- Create: `backend/app/services/skill_store.py`
- Create: `backend/app/services/skill_build_pipeline.py`
- Create: `backend/tests/test_skill_store.py`
- Create: `backend/tests/test_skill_build_pipeline.py`

Store mutable Draft content in filesystem directories and metadata in the
existing Workbench SQLite database. Publish ZIP, unpacked copy, IR, validation,
reviews, and digest manifest atomically. Do not add an object-store class.

### Task 6: Review records and patch decisions

**Files:**

- Create: `backend/app/services/skill_review.py`
- Create: `backend/tests/test_skill_review.py`

Test seeded semantic contradictions, incremental/full scope, patch proposal,
explicit apply/reject, and release audit retention. No review operation mutates
a Draft or publishes a Version implicitly.

### Task 7: Skill APIs

**Files:**

- Create: `backend/app/api/skills.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_skills_api.py`

Add project/draft/build/review/release/import/read APIs with exact 4xx behavior.
The main Agent performs the shared `main.py` registration to avoid ownership
conflicts.

### Task 8: Task binding migration

**Files:**

- Modify: `backend/app/services/workbench_task_store.py`
- Modify: `backend/app/api/workbench_v2_tasks.py`
- Modify: `backend/tests/test_workbench_task_store.py`

Replace Workflow binding with Skill Version/digest. Because the product is not
deployed, use one explicit destructive schema migration with backup and tests;
do not retain dual Workflow/Skill write paths or a binding table.

### Task 9: Frozen invocation and runtime adapter

**Files:**

- Create: `backend/app/services/skill_run_invocation.py`
- Create: `backend/app/services/skill_run_executor.py`
- Modify: `backend/app/services/workbench_task_run.py`
- Modify: `backend/app/services/workbench_workflow_runner.py`
- Create: `backend/tests/test_skill_run_invocation.py`
- Create: `backend/tests/test_skill_run_executor.py`
- Create: `backend/tests/test_skill_agent_lifecycle.py`

Freeze invocation before execution and translate it through the existing
Harness. The main Agent owns modifications to runner hot files. First make every
create/start/event/kill/restart/session-loss/cancel/timeout case red against the
Fake Agent, then implement the smallest common lifecycle contract. Run the same
contract against company CodeAgent; add Claude Code and OpenCode only after the
common contract passes.

### Task 10: Judge and delivery

**Files:**

- Create: `backend/app/services/skill_judge.py`
- Modify: `backend/app/services/workbench_deliverables.py`
- Create: `backend/tests/test_skill_judge.py`
- Modify: `backend/tests/test_workbench_deliverables.py`

Prove session isolation, input/artifact scope, `PENDING_VALIDATION -> READY`, and
full-execution/selective-delivery behavior. A missing optional Judge warns; a
missing Skill-required Judge prevents READY.

### Task 11: Skill-first product UI

**Files:**

- Create: `frontend/src/features/skills/`
- Modify: `frontend/src/features/tasks/task-wizard.tsx`
- Modify: `frontend/src/features/runs/run-cockpit-page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/e2e/skill-first-task-run.spec.ts`

Pass a UI Design Gate before implementation. Render project/version/review,
Task inputs/deliveries, current step, next action, capability/Judge state, and
artifact/delivery distinction on existing product surfaces.

### Task 12: Legacy removal and final evidence

**Files:**

- Delete: `frontend/src/features/workflows/`
- Delete/modify: Workflow-only API/store/preset modules after call-site audit
- Create: `docs/reports/f014-skill-first-acceptance.md`
- Create: `docs/review-notes/f014-*.md`

Run source and route gates before deletion, remove only product-specific
Workflow code, and retain generic scheduler/checkpoint/event/artifact machinery.
Map every AC to a command, log, artifact, screenshot, or independent verdict.

## 5. Sub-Agent Plan

Maximum concurrency is three child Agents plus the main integrator. A child may
own one production path in a wave. Shared integration files are never assigned
to multiple Agents.

### Wave 0: Contract discovery

| Agent | Ownership | Output |
|---|---|---|
| archive-contract | read-only ZIP analysis and fixture tests | 37-file trace map and semantic golden summary |
| schema-contract | schemas and schema fixtures only | six schemas with red/green evidence |
| runtime-seam | read-only existing Harness/Attempt audit | exact reuse/adapt/remove matrix |

Main Agent resolves contract disagreements and owns the terminal schema. No
production runtime edits occur in this wave.

### Wave 1: Skill domain

| Agent | Ownership | Output |
|---|---|---|
| importer | `skill_package_importer.py`, path helper, importer tests | safe Pack/Skill import |
| compiler | validator/compiler modules and tests | deterministic IR and diagnostics |
| store-review | store/build/review modules and tests | immutable releases and explicit patch decisions |

Main Agent owns database migration, API registration, and cross-module types.

### Wave 2: Vertical runtime

| Agent | Ownership | Output |
|---|---|---|
| invocation | new invocation/executor modules and tests | frozen runtime bridge |
| judge-delivery | Judge and delivery modules/tests | isolated validation and filtering |
| frontend | new Skill UI and isolated component/E2E tests | Skill-first user journey |

Main Agent alone modifies `workbench_task_store.py`, `workbench_task_run.py`,
`workbench_workflow_runner.py`, `main.py`, and shared frontend API/types after
receiving five-part handoffs.

### Wave 3: Acceptance

| Agent | Role | Constraint |
|---|---|---|
| regression | run backend/frontend/Playwright and restart/cancel matrix | no production edits |
| runtime reviewer | adversarial contract, isolation, recovery review | did not author reviewed code |
| Vision Guardian | compare original decisions and ZIP semantics to real UX | distinct from author and reviewer |

Any review fix returns to the owning implementation Agent, then to the same
reviewer. The main Agent cannot approve its own integration changes.

Every handoff includes What, Why, Tradeoff, Open Questions, and Next Action plus
the exact red/green commands and changed paths.

## 6. Development and Test Cadence

不采用“全部开发完再测试”。执行节奏固定为三层：

### 6.1 Task 内 Red-Green-Refactor

每个 Task 都按以下顺序完成后才允许提交：

1. 写一个能够证明缺失行为的失败测试；
2. 运行并保存准确的失败原因，确认不是 fixture 或环境误报；
3. 写最小实现使该测试通过；
4. 运行当前模块的全部测试，防止局部通过、相邻回归；
5. 重构后再次运行模块测试；
6. 提交一个可独立验证、会保留在终态系统中的改动。

### 6.2 Phase 集成门禁

| Phase | 进入下一阶段前必须通过 |
|---|---|
| A Contracts/build | 六份 Schema 正反例、ZIP 安全、37/37 inventory、IR golden、重复构建 digest |
| B Domain/review | Store、Build、Rescan、Release immutability、AI patch 不自动应用、API 4xx |
| C Task/Runtime | Task binding、冻结 Invocation、Fake Agent 生命周期九场景、完整 backend 回归 |
| D Official Skill/Judge | 真实 CodeAgent、九步骤/37 工件/八输出、进程和后端重启、独立 Judge |
| E Product/removal | Playwright 用户链路、响应式截图、旧 Workflow source/route gate、完整前后端回归 |

Phase 门禁失败就停在当前阶段修复，不能把红测留给下一阶段，也不能用“最终
会统一修”作为通过理由。

### 6.3 Final acceptance

全部阶段完成后仍需执行一次最终全量验收，但它不是第一次测试。最终验收基于
final SHA 重跑完整 backend、frontend build/lint、Playwright、真实 CodeAgent、
restart/cancel/session-loss、Judge 隔离和旧路径删除门禁，并由未参与实现的
reviewer 与 Vision Guardian 分别签字。

## 7. Required Commands and Evidence

Initial focused gates:

```bash
cd backend
python -m pytest -q tests/test_skill_schemas.py tests/test_skill_package_importer.py
python -m pytest -q tests/test_skill_package_validator.py tests/test_skill_ir_compiler.py
python -m pytest -q tests/test_skill_store.py tests/test_skill_build_pipeline.py tests/test_skill_review.py
python -m pytest -q tests/test_skill_run_invocation.py tests/test_skill_run_executor.py tests/test_skill_agent_lifecycle.py tests/test_skill_judge.py
```

Integration gates:

```bash
cd backend
python -m pytest -q tests/test_workbench_task_store.py tests/test_workbench_task_run.py tests/test_workbench_deliverables.py
python -m pytest -q
cd ../frontend
npm run lint
npm run build
npx playwright test e2e/skill-first-task-run.spec.ts --project=chromium
```

Evidence is incomplete unless it records command, final SHA, main sync state,
exit code, test counts, relevant artifact paths, screenshots, and the independent
review verdict.

## 8. Stop Conditions

Stop before implementation when the company CodeAgent contract cannot be
observed, the local fixture cannot represent the chosen acceptance scenario,
or the UI Design Gate is not approved. Ordinary red tests, implementation
failures, and expected archive mutations are not blockers.

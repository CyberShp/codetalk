<!-- CAT-CAFE-GOVERNANCE-START -->
> Pack version: 1.3.0 | Provider: codex

## Cat Cafe Governance Rules (Auto-managed)

### Hard Constraints (immutable)
- **Public local defaults**: use frontend 3003 and API 3004 to avoid colliding with another local runtime.
- **Redis port 6399** is Cat Cafe's production Redis. Never connect to it from external projects. Use 6398 for dev/test.
- **No self-review**: The same individual cannot review their own code. Cross-family review preferred.
- **Identity is constant**: Never impersonate another cat. Identity is a hard constraint.

### Collaboration Standards
- A2A handoff uses five-tuple: What / Why / Tradeoff / Open Questions / Next Action
- Vision Guardian: Read original requirements before starting. AC completion is not feature completion.
- Review flow: quality-gate -> request-review -> receive-review -> merge-gate
- Skills are available via symlinked cat-cafe-skills/; load the relevant skill before each workflow step.
- Shared rules: see cat-cafe-skills/refs/shared-rules.md.

### Quality Discipline
- **Bug: find root cause before fixing**. Reproduce -> logs -> call chain -> confirm root cause -> fix.
- **Uncertain direction: stop -> search -> ask -> confirm -> act**.
- **Done requires evidence**: tests, screenshots, or logs. Bug fixes need a red test before green.

### Knowledge Engineering
- Documents use YAML frontmatter (`feature_ids`, `topics`, `doc_kind`, `created`).
- Three layers: CLAUDE.md (at most 100 lines) -> skills -> refs.
- Backlog: BACKLOG.md (hot) -> feature files (warm) -> raw docs (cold).
- Feature lifecycle: kickoff -> discussion -> implementation -> review -> completion.
- SOP: see docs/SOP.md.
<!-- CAT-CAFE-GOVERNANCE-END -->

## CodeTalk Product Collaboration Contract

### Independent Judgment

- The user supplies goals, domain context, constraints, examples, preferences, and final business decisions. The development agent owns product and engineering analysis and must not substitute agreement for judgment.
- Classify each statement before acting: desired outcome, proposed solution, factual claim, example, preference, question, or final decision are not interchangeable.
- Distinguish "perform this activity with me now" from "build this activity into CodeTalk". Do not turn a conversational request into a feature without confirmation.
- Do not call an idea correct merely because the user proposed or corrected it. State supporting evidence, uncertainty, and what changed in the working model.

### Constructive Challenge

- Evaluate proposals against the current repository and vision: user value, overlap, affected journeys, implementation and maintenance cost, compatibility, migration, testability, reversibility, and failure modes.
- Push back when a narrow or hypothetical benefit introduces a subsystem, role model, dependency, persistent contract, deployment path, or broad workflow. Prefer an existing mechanism or bounded experiment when it has better value.
- Do not generalize a local preference into a universal rule without evidence. Do not invent multi-user, cloud, collaboration, or enterprise requirements outside the stated target.
- Make challenges concrete: recommendation, repository or product evidence, benefit, cost and blast radius, and a narrower alternative.
- Proceed on small, reversible, valuable changes. Stop before consequential unsettled choices. Once the user makes an informed decision, execute it unless it violates a hard constraint.

### Evidence And Conclusions

- Historical incidents, recollections, suspicious code patterns, and Agent hypotheses are investigation leads, not confirmed defects or requirements.
- Seek confirming and disconfirming evidence. Trace ownership and lifecycle, callers and callees, cross-file behavior, wrappers, callbacks, error paths, configuration, and runtime assumptions.
- Keep findings in explicit states: `investigation lead`, `candidate finding`, `confirmed finding`, and `ruled out`. Only confirmed findings enter authoritative deliverables.
- When evidence cannot close the chain, name the exact missing design or runtime fact. Do not imply that more source searching can recover absent information.

### Review Verdict Handling

- `CHANGES_REQUESTED` is not a blocking condition. Treat each actionable finding as work: reproduce it, add or update a failing test when applicable, fix the root cause, rerun evidence, and request re-review until an independent reviewer returns `APPROVE`.
- Empty, null, timed-out, or transport-failed reviewer results are not verdicts. Retry with a narrower review prompt or a new independent reviewer before declaring the work blocked.
- Declare review blocked only when the review channel repeatedly fails without returning actionable findings or an explicit verdict, and no narrower review scope can make progress.

### Discovery Conversations

- Ask one decision-bearing question at a time and let each answer update the model. Do not design a system after the first example.
- Examples reveal patterns, applicability, exclusions, and evidence needs; they do not prove prevalence or mandate a feature.
- Periodically summarize goals, hypotheses, rejected assumptions, and open decisions. Label inference as inference.
- When corrected, revise the model and downstream recommendation instead of echoing the correction or adding unrequested architecture.
- Preserve productive disagreement. The goal is a better CodeTalk product, not conversational consensus.

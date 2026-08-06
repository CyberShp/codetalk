---
feature_ids: [F014]
topics: [review-response, task-binding, task-8]
doc_kind: review-response
created: 2026-08-05
---

# F014 Task 8 Task Binding Review Response

## Decision

Task 8 received independent `APPROVE`.

Final reviewer: Godel (`019fd2c7-2b72-7c41-bdfb-55454212953e`)

Final verdict:

```text
No confirmed findings in F014 Task 8 Task binding scope.

VERDICT: APPROVE
```

## Evidence Summary

The reviewer confirmed:

- Task rows store `skill_id`, `skill_version_id`, and `skill_content_digest`.
- Task update rejects Skill identity and digest mutation.
- Task clone preserves immutable Skill identity and digest.
- Task create requires `skill_version_id`, rejects legacy extra fields, resolves the version through `SkillStore`, and stores version-derived Skill identity and digest.
- Task detail and compile resolve from the stored version plus expected digest.
- Run creation freezes Skill version, digest, compiled plan, effective definition, selected deliveries, and invocation into `task_bundle`.
- Legacy `workflow_id` remains only in compatibility/run summaries, not as new Task binding authority.

Focused reviewer evidence:

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_workbench_task_store.py \
  tests/test_skills_api.py \
  tests/test_skill_store.py
=> 40 passed
```

## Next Action

Task 8 may proceed to merge gate. Later gates must still verify frozen
invocation, Judge delivery, and final formal acceptance on the final SHA.

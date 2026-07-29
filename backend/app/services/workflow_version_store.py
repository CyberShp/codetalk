"""Immutable workflow headers and versions for Workbench V2."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.workbench_sqlite_backup import ensure_workbench_migration_backup
from app.services.workflow_graph import compile_legacy_workflow


WORKFLOW_SCHEMA_VERSION = 2
_WORKFLOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_V3_DRAFT_REVISION_KEY = "__codetalk_draft_revision"


class WorkflowVersionError(ValueError):
    pass


class WorkflowDraftExistsError(WorkflowVersionError):
    pass


class PublishedWorkflowVersionError(WorkflowVersionError):
    pass


class StaleWorkflowDraftError(WorkflowVersionError):
    """A V3 canvas mutation was based on an older persisted draft."""


class ExpectedWorkflowDraftRevisionError(WorkflowVersionError):
    """A V3 canvas mutation omitted its required compare-and-swap revision."""


@dataclass(frozen=True)
class WorkflowHeader:
    workflow_id: str
    name: str
    description: str
    status: str
    published_version_id: str | None
    current_draft_version_id: str | None
    created_at: str
    updated_at: str
    archived_at: str | None


@dataclass(frozen=True)
class WorkflowVersion:
    version_id: str
    workflow_id: str
    version_number: int
    state: str
    authoring_graph: dict[str, Any]
    compiled_definition: dict[str, Any] | None
    compiled_plan: dict[str, Any] | None
    validation: dict[str, Any] | None
    based_on_version_id: str | None
    created_at: str
    updated_at: str
    published_at: str | None

    @property
    def draft_revision(self) -> int | None:
        """Server-only V3 compare-and-swap revision, excluded from legacy JSON."""
        return getattr(self, "_draft_revision", None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_version_id() -> str:
    return f"wfv_{uuid.uuid4().hex}"


class WorkflowVersionStore:
    """SQLite store that keeps published workflow versions immutable."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_and_migrate(self) -> dict[str, int]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_workbench_migration_backup(self.db_path)
        migrated = 0
        upgraded = 0
        with self._connect() as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._table_exists(db, "workflow_definitions"):
                    rows = db.execute(
                        """
                        SELECT workflow_id, name, definition_json, created_at, updated_at
                        FROM workflow_definitions
                        ORDER BY created_at, workflow_id
                        """
                    ).fetchall()
                    for row in rows:
                        exists = db.execute(
                            "SELECT 1 FROM workflow_headers WHERE workflow_id = ?",
                            (str(row["workflow_id"]),),
                        ).fetchone()
                        if exists:
                            continue
                        self._migrate_legacy_row(db, row)
                        migrated += 1
                db.execute(
                    """
                    INSERT INTO workbench_schema_meta(component, version, updated_at)
                    VALUES ('workflow_versions', ?, ?)
                    ON CONFLICT(component) DO UPDATE SET
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (WORKFLOW_SCHEMA_VERSION, _now()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "migrated_workflows": migrated,
            "upgraded_workflows": upgraded,
        }

    def ensure_legacy_published_workflows(
        self, definitions: list[dict[str, Any]]
    ) -> int:
        """Publish canonical read-only snapshots without trusting same-ID legacy rows."""
        self.initialize_and_migrate()
        ensured = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for raw_definition in definitions:
                    definition = _json_object(raw_definition, "compiled_definition")
                    workflow_id = _validated_workflow_id(str(definition.get("id") or ""))
                    header = db.execute(
                        "SELECT * FROM workflow_headers WHERE workflow_id = ?",
                        (workflow_id,),
                    ).fetchone()
                    published_version_id = (
                        str(header["published_version_id"])
                        if header is not None and header["published_version_id"]
                        else None
                    )
                    published_snapshot = None
                    if published_version_id:
                        published = db.execute(
                            """
                            SELECT state, authoring_graph_json, compiled_definition_json,
                                   compiled_plan_json, validation_json
                            FROM workflow_versions WHERE version_id = ?
                            """,
                            (published_version_id,),
                        ).fetchone()
                        if published is not None:
                            published_graph = _load_optional(
                                published["authoring_graph_json"]
                            )
                            published_definition = _load_optional(
                                published["compiled_definition_json"]
                            )
                            published_plan = _load_optional(
                                published["compiled_plan_json"]
                            )
                            if (
                                published_plan is None
                                and isinstance(published_graph, dict)
                                and published_graph.get("schema_version") == 1
                                and isinstance(
                                    published_graph.get("legacy_definition"), dict
                                )
                                and isinstance(published_definition, dict)
                            ):
                                published_plan = compile_legacy_workflow(
                                    published_definition,
                                    workflow_version_id=published_version_id,
                                )
                            published_snapshot = {
                                "state": str(published["state"]),
                                "authoring_graph": published_graph,
                                "compiled_definition": published_definition,
                                "compiled_plan": published_plan,
                                "validation": _load_optional(
                                    published["validation_json"]
                                ),
                            }
                    expected_graph = _legacy_authoring_graph(definition, workflow_id)
                    expected_validation = _legacy_validation()
                    expected_plan = (
                        compile_legacy_workflow(
                            definition,
                            workflow_version_id=published_version_id,
                        )
                        if published_version_id
                        else None
                    )
                    snapshot_is_canonical = published_snapshot == {
                        "state": "published",
                        "authoring_graph": expected_graph,
                        "compiled_definition": definition,
                        "compiled_plan": expected_plan,
                        "validation": expected_validation,
                    }
                    header_is_canonical = bool(
                        header is not None
                        and str(header["status"]) == "active"
                        and not header["archived_at"]
                        and not header["current_draft_version_id"]
                    )
                    if snapshot_is_canonical and header_is_canonical:
                        continue

                    now = _now()
                    current_draft_version_id = (
                        str(header["current_draft_version_id"])
                        if header is not None and header["current_draft_version_id"]
                        else None
                    )
                    if current_draft_version_id:
                        db.execute(
                            """
                            UPDATE workflow_versions
                            SET state = 'archived', updated_at = ?
                            WHERE version_id = ? AND state = 'draft'
                            """,
                            (now, current_draft_version_id),
                        )
                    if snapshot_is_canonical:
                        db.execute(
                            """
                            UPDATE workflow_headers
                            SET name = ?, description = ?, status = 'active',
                                current_draft_version_id = NULL,
                                archived_at = NULL, updated_at = ?
                            WHERE workflow_id = ?
                            """,
                            (
                                str(definition.get("name") or workflow_id),
                                str(definition.get("description") or ""),
                                now,
                                workflow_id,
                            ),
                        )
                        ensured += 1
                        continue

                    version_id = _new_version_id()
                    version_number = 1
                    if header is not None:
                        row = db.execute(
                            """
                            SELECT COALESCE(MAX(version_number), 0) AS max_version
                            FROM workflow_versions WHERE workflow_id = ?
                            """,
                            (workflow_id,),
                        ).fetchone()
                        version_number = int(row["max_version"] or 0) + 1
                    graph = _legacy_authoring_graph(definition, workflow_id)
                    validation = _legacy_validation()
                    plan = compile_legacy_workflow(
                        definition,
                        workflow_version_id=version_id,
                    )
                    if header is None:
                        db.execute(
                            """
                            INSERT INTO workflow_headers(
                                workflow_id, name, description, status,
                                published_version_id, current_draft_version_id,
                                created_at, updated_at, archived_at
                            ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?, NULL)
                            """,
                            (
                                workflow_id,
                                str(definition.get("name") or workflow_id),
                                str(definition.get("description") or ""),
                                version_id,
                                now,
                                now,
                            ),
                        )
                    db.execute(
                        """
                        INSERT INTO workflow_versions(
                            version_id, workflow_id, version_number, state,
                            authoring_graph_json, compiled_definition_json,
                            compiled_plan_json, validation_json, based_on_version_id,
                            created_at, updated_at, published_at
                        ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id,
                            workflow_id,
                            version_number,
                            _dump(graph),
                            _dump(definition),
                            _dump(plan),
                            _dump(validation),
                            published_version_id,
                            now,
                            now,
                            now,
                        ),
                    )
                    if header is not None:
                        db.execute(
                            """
                            UPDATE workflow_headers
                            SET name = ?, description = ?, status = 'active',
                                published_version_id = ?, current_draft_version_id = NULL,
                                archived_at = NULL, updated_at = ?
                            WHERE workflow_id = ?
                            """,
                            (
                                str(definition.get("name") or workflow_id),
                                str(definition.get("description") or ""),
                                version_id,
                                now,
                                workflow_id,
                            ),
                        )
                    ensured += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        return ensured

    def create_workflow(
        self,
        *,
        workflow_id: str,
        name: str,
        description: str,
        authoring_graph: dict[str, Any],
    ) -> tuple[WorkflowHeader, WorkflowVersion]:
        graph = _json_object(authoring_graph, "authoring_graph")
        if graph.get("schema_version") == 3:
            raise WorkflowVersionError(
                "V3 canvas workflows must be created through create_canvas_workflow"
            )
        return self._create_workflow(
            workflow_id=workflow_id,
            name=name,
            description=description,
            authoring_graph=graph,
        )

    def _create_workflow(
        self,
        *,
        workflow_id: str,
        name: str,
        description: str,
        authoring_graph: dict[str, Any],
    ) -> tuple[WorkflowHeader, WorkflowVersion]:
        workflow_id = _validated_workflow_id(workflow_id)
        name = _required_text(name, "name")
        graph = _json_object(authoring_graph, "authoring_graph")
        if graph.get("schema_version") == 3:
            graph = _with_v3_draft_revision(graph, 1)
        self.initialize_and_migrate()
        now = _now()
        version_id = _new_version_id()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    INSERT INTO workflow_headers(
                        workflow_id, name, description, status,
                        published_version_id, current_draft_version_id,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, 'active', NULL, ?, ?, ?, NULL)
                    """,
                    (workflow_id, name, str(description or ""), version_id, now, now),
                )
                db.execute(
                    """
                    INSERT INTO workflow_versions(
                        version_id, workflow_id, version_number, state,
                        authoring_graph_json, compiled_definition_json,
                        compiled_plan_json, validation_json, based_on_version_id,
                        created_at, updated_at, published_at
                    ) VALUES (?, ?, 1, 'draft', ?, NULL, NULL, NULL, NULL, ?, ?, NULL)
                    """,
                    (version_id, workflow_id, _dump(graph), now, now),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise WorkflowVersionError(f"workflow already exists: {workflow_id}") from exc
        return self.get_workflow(workflow_id), self.get_version(version_id)

    def create_canvas_workflow(
        self,
        *,
        workflow_id: str,
        name: str,
        description: str,
        authoring_graph: dict[str, Any],
    ) -> tuple[WorkflowHeader, WorkflowVersion]:
        """Persist a server-created V3 canvas draft.

        This intentionally shares the immutable version store with historical
        workflows; only the authoring command that produced its graph differs.
        """
        if authoring_graph.get("schema_version") != 3:
            raise WorkflowVersionError("canvas workflows must use schema_version 3")
        if str(authoring_graph.get("workflow_id") or "") != workflow_id:
            raise WorkflowVersionError("canvas graph workflow_id does not match header")
        return self._create_workflow(
            workflow_id=workflow_id,
            name=name,
            description=description,
            authoring_graph=authoring_graph,
        )

    def copy_workflow_as_custom_draft(
        self,
        source_workflow_id: str,
        *,
        workflow_id: str,
        name: str,
        description: str | None = None,
    ) -> tuple[WorkflowHeader, WorkflowVersion]:
        """Create an editable copy of an existing V2 workflow.

        V1 history remains read-only and must use the explicit copy-to-V3
        migration command. The source workflow is never mutated.
        """
        source = self.get_workflow(source_workflow_id)
        source_version_id = source.published_version_id or source.current_draft_version_id
        if not source_version_id:
            raise WorkflowVersionError("source workflow has no version to copy")
        source_version = self.get_version(source_version_id)
        graph = _editable_graph_from_base(source_version, source)
        graph["workflow_id"] = workflow_id
        graph["name"] = name
        graph["description"] = (
            source.description if description is None else str(description)
        )
        graph["migration"] = {
            "source": "workflow_copy",
            "source_workflow_id": source.workflow_id,
            "source_version_id": source_version.version_id,
        }
        return self.create_workflow(
            workflow_id=workflow_id,
            name=name,
            description=graph["description"],
            authoring_graph=graph,
        )

    def get_workflow(self, workflow_id: str) -> WorkflowHeader:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_headers WHERE workflow_id = ?",
                (_validated_workflow_id(workflow_id),),
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return _header_from_row(row)

    def list_workflows(self, *, include_archived: bool = False) -> list[WorkflowHeader]:
        self.initialize_and_migrate()
        sql = "SELECT * FROM workflow_headers"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY updated_at DESC, workflow_id"
        with self._connect() as db:
            rows = db.execute(sql).fetchall()
        return [_header_from_row(row) for row in rows]

    def retire_workflows(self, workflow_ids: set[str] | frozenset[str]) -> int:
        """Archive workflow headers without deleting published history or task snapshots."""

        normalized_ids = sorted(
            {_validated_workflow_id(workflow_id) for workflow_id in workflow_ids}
        )
        if not normalized_ids:
            return 0
        self.initialize_and_migrate()
        changed = 0
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for workflow_id in normalized_ids:
                    header = db.execute(
                        "SELECT status, current_draft_version_id FROM workflow_headers WHERE workflow_id = ?",
                        (workflow_id,),
                    ).fetchone()
                    if header is None:
                        continue
                    draft_id = (
                        str(header["current_draft_version_id"])
                        if header["current_draft_version_id"]
                        else None
                    )
                    if str(header["status"]) == "archived" and draft_id is None:
                        continue
                    if draft_id:
                        db.execute(
                            """
                            UPDATE workflow_versions
                            SET state = 'archived', updated_at = ?
                            WHERE version_id = ? AND state = 'draft'
                            """,
                            (now, draft_id),
                        )
                    db.execute(
                        """
                        UPDATE workflow_headers
                        SET status = 'archived', current_draft_version_id = NULL,
                            archived_at = COALESCE(archived_at, ?), updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (now, now, workflow_id),
                    )
                    changed += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        return changed

    def update_workflow(
        self,
        workflow_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> WorkflowHeader:
        current = self.get_workflow(workflow_id)
        next_name = current.name if name is None else _required_text(name, "name")
        next_description = current.description if description is None else str(description)
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_headers
                SET name = ?, description = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (next_name, next_description, _now(), current.workflow_id),
            )
        return self.get_workflow(current.workflow_id)

    def archive_workflow(self, workflow_id: str) -> WorkflowHeader:
        current = self.get_workflow(workflow_id)
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_headers
                SET status = 'archived', archived_at = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (now, now, current.workflow_id),
            )
        return self.get_workflow(current.workflow_id)

    def create_draft(
        self,
        workflow_id: str,
        *,
        based_on_version_id: str | None = None,
    ) -> WorkflowVersion:
        header = self.get_workflow(workflow_id)
        if header.current_draft_version_id:
            raise WorkflowDraftExistsError(
                f"workflow already has a current draft: {header.current_draft_version_id}"
            )
        base_id = based_on_version_id or header.published_version_id
        base = self.get_version(base_id) if base_id else None
        if base is not None and base.workflow_id != header.workflow_id:
            raise WorkflowVersionError("based_on_version_id belongs to another workflow")
        version_number = self._next_version_number(header.workflow_id)
        version_id = _new_version_id()
        now = _now()
        graph = _editable_graph_from_base(base, header) if base else _empty_graph(header)
        if graph.get("schema_version") == 3:
            graph = _with_v3_draft_revision(graph, 1)
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    INSERT INTO workflow_versions(
                        version_id, workflow_id, version_number, state,
                        authoring_graph_json, compiled_definition_json,
                        compiled_plan_json, validation_json, based_on_version_id,
                        created_at, updated_at, published_at
                    ) VALUES (?, ?, ?, 'draft', ?, NULL, NULL, NULL, ?, ?, ?, NULL)
                    """,
                    (
                        version_id,
                        header.workflow_id,
                        version_number,
                        _dump(graph),
                        base.version_id if base else None,
                        now,
                        now,
                    ),
                )
                updated = db.execute(
                    """
                    UPDATE workflow_headers
                    SET current_draft_version_id = ?, updated_at = ?
                    WHERE workflow_id = ? AND current_draft_version_id IS NULL
                    """,
                    (version_id, now, header.workflow_id),
                )
                if updated.rowcount != 1:
                    raise WorkflowDraftExistsError("workflow draft was created concurrently")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_version(version_id)

    def get_version(self, version_id: str | None) -> WorkflowVersion:
        if not version_id:
            raise KeyError(version_id)
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_versions WHERE version_id = ?",
                (str(version_id),),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _version_from_row(row)

    def list_versions(self, workflow_id: str) -> list[WorkflowVersion]:
        workflow_id = self.get_workflow(workflow_id).workflow_id
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM workflow_versions
                WHERE workflow_id = ?
                ORDER BY version_number DESC, created_at DESC
                """,
                (workflow_id,),
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def update_draft(
        self,
        version_id: str,
        *,
        authoring_graph: dict[str, Any],
        expected_revision: int | None = None,
        validation: dict[str, Any] | None = None,
        compiled_definition: dict[str, Any] | None = None,
        compiled_plan: dict[str, Any] | None = None,
    ) -> WorkflowVersion:
        current = self.get_version(version_id)
        if current.state != "draft":
            raise PublishedWorkflowVersionError(
                f"published workflow version is immutable: {version_id}"
            )
        graph = _json_object(authoring_graph, "authoring_graph")
        _assert_draft_graph_contract(current.authoring_graph, graph)
        if current.authoring_graph.get("schema_version") == 3:
            if expected_revision is None:
                raise ExpectedWorkflowDraftRevisionError(
                    "V3 草稿保存需要版本号。请刷新画布后再次保存。"
                )
            return self._replace_v3_draft(
                version_id,
                expected_revision=expected_revision,
                authoring_graph=graph,
                compiled_definition=compiled_definition,
                compiled_plan=compiled_plan,
                validation=validation,
            )
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_versions
                SET authoring_graph_json = ?, compiled_definition_json = ?,
                    compiled_plan_json = ?, validation_json = ?, updated_at = ?
                WHERE version_id = ? AND state = 'draft'
                """,
                (
                    _dump(graph),
                    _dump_optional(compiled_definition),
                    _dump_optional(compiled_plan),
                    _dump_optional(validation),
                    _now(),
                    version_id,
                ),
            )
        return self.get_version(version_id)

    def update_v3_draft_from_server_command(
        self,
        version_id: str,
        *,
        expected_revision: int | None,
        authoring_graph: dict[str, Any],
    ) -> WorkflowVersion:
        """Persist a graph changed by an authenticated server authoring command.

        Normal PUT requests intentionally cannot add technical identities.  Node,
        port and edge commands call this narrow method after generating those
        identities on the server.
        """
        if expected_revision is None:
            raise ExpectedWorkflowDraftRevisionError(
                "V3 画布操作需要版本号。请刷新画布后重试。"
            )
        graph = _json_object(authoring_graph, "authoring_graph")
        if graph.get("schema_version") != 3:
            raise WorkflowVersionError("server authoring commands require a V3 draft")
        return self._replace_v3_draft(
            version_id,
            expected_revision=expected_revision,
            authoring_graph=graph,
            compiled_definition=None,
            compiled_plan=None,
            validation=None,
            require_v3_command_graph=True,
        )

    def _replace_v3_draft(
        self,
        version_id: str,
        *,
        expected_revision: int,
        authoring_graph: dict[str, Any],
        compiled_definition: dict[str, Any] | None,
        compiled_plan: dict[str, Any] | None,
        validation: dict[str, Any] | None,
        require_v3_command_graph: bool = False,
    ) -> WorkflowVersion:
        """Atomically compare a V3 revision and persist exactly one successor."""
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ExpectedWorkflowDraftRevisionError(
                "V3 草稿版本号无效。请刷新画布后再次操作。"
            )
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM workflow_versions WHERE version_id = ?", (version_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(version_id)
                current = _version_from_row(row)
                if current.state != "draft":
                    raise PublishedWorkflowVersionError(
                        f"published workflow version is immutable: {version_id}"
                    )
                if current.authoring_graph.get("schema_version") != 3:
                    raise WorkflowVersionError("server authoring commands require a V3 draft")
                if str(authoring_graph.get("workflow_id") or "") != current.workflow_id:
                    raise WorkflowVersionError("canvas graph workflow_id does not match draft")
                if current.draft_revision != expected_revision:
                    raise StaleWorkflowDraftError(
                        "画布已被其他窗口更新。请刷新后确认最新内容，再重新操作。"
                    )
                if require_v3_command_graph and authoring_graph.get("schema_version") != 3:
                    raise WorkflowVersionError("server authoring commands require a V3 draft")
                successor = _with_v3_draft_revision(authoring_graph, expected_revision + 1)
                db.execute(
                    """
                    UPDATE workflow_versions
                    SET authoring_graph_json = ?, compiled_definition_json = ?,
                        compiled_plan_json = ?, validation_json = ?, updated_at = ?
                    WHERE version_id = ? AND state = 'draft'
                    """,
                    (
                        _dump(successor),
                        _dump_optional(compiled_definition),
                        _dump_optional(compiled_plan),
                        _dump_optional(validation),
                        _now(),
                        version_id,
                    ),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_version(version_id)

    def publish_version(
        self,
        version_id: str,
        *,
        expected_revision: int | None = None,
        authoring_graph: dict[str, Any],
        compiled_definition: dict[str, Any],
        compiled_plan: dict[str, Any],
        validation: dict[str, Any],
    ) -> WorkflowVersion:
        graph = _json_object(authoring_graph, "authoring_graph")
        definition = _json_object(compiled_definition, "compiled_definition")
        plan = _json_object(compiled_plan, "compiled_plan")
        validation_payload = _json_object(validation, "validation")
        if validation_payload.get("valid") is not True:
            raise WorkflowVersionError("cannot publish an invalid workflow version")
        now = _now()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM workflow_versions WHERE version_id = ?", (version_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(version_id)
                current = _version_from_row(row)
                if current.state != "draft":
                    raise PublishedWorkflowVersionError(
                        f"published workflow version is immutable: {version_id}"
                    )
                _assert_draft_graph_contract(current.authoring_graph, graph)
                stored_graph = graph
                if current.authoring_graph.get("schema_version") == 3:
                    if (
                        isinstance(expected_revision, bool)
                        or not isinstance(expected_revision, int)
                        or expected_revision < 1
                    ):
                        raise ExpectedWorkflowDraftRevisionError(
                            "V3 发布需要版本号。请刷新画布后再次操作。"
                        )
                    if current.draft_revision != expected_revision:
                        raise StaleWorkflowDraftError(
                            "画布已被其他窗口更新。请刷新后确认最新内容，再重新操作。"
                        )
                    if str(graph.get("workflow_id") or "") != current.workflow_id:
                        raise WorkflowVersionError(
                            "canvas graph workflow_id does not match draft"
                        )
                    stored_graph = _with_v3_draft_revision(graph, expected_revision)
                updated = db.execute(
                    """
                    UPDATE workflow_versions
                    SET state = 'published', authoring_graph_json = ?,
                        compiled_definition_json = ?, compiled_plan_json = ?,
                        validation_json = ?, updated_at = ?, published_at = ?
                    WHERE version_id = ? AND state = 'draft'
                    """,
                    (
                        _dump(stored_graph),
                        _dump(definition),
                        _dump(plan),
                        _dump(validation_payload),
                        now,
                        now,
                        version_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise PublishedWorkflowVersionError(
                        f"published workflow version is immutable: {version_id}"
                    )
                db.execute(
                    """
                    UPDATE workflow_headers
                    SET published_version_id = ?, current_draft_version_id = NULL,
                        status = 'active', archived_at = NULL, updated_at = ?
                    WHERE workflow_id = ? AND current_draft_version_id = ?
                    """,
                    (version_id, now, current.workflow_id, version_id),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_version(version_id)

    def compatibility_definition(self, workflow_id: str) -> dict[str, Any]:
        header = self.get_workflow(workflow_id)
        if not header.published_version_id:
            raise KeyError(f"workflow has no published version: {workflow_id}")
        version = self.get_version(header.published_version_id)
        if not version.compiled_definition:
            raise KeyError(f"workflow version has no compiled definition: {version.version_id}")
        return json.loads(_dump(version.compiled_definition))

    def _next_version_number(self, workflow_id: str) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS max_version
                FROM workflow_versions WHERE workflow_id = ? AND state = 'published'
                """,
                (workflow_id,),
            ).fetchone()
        return int(row["max_version"] or 0) + 1

    def _migrate_legacy_row(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        workflow_id = _validated_workflow_id(str(row["workflow_id"]))
        definition = json.loads(str(row["definition_json"]))
        version_id = _new_version_id()
        created_at = str(row["created_at"] or _now())
        updated_at = str(row["updated_at"] or created_at)
        graph = _legacy_authoring_graph(definition, workflow_id)
        validation = _legacy_validation()
        db.execute(
            """
            INSERT INTO workflow_headers(
                workflow_id, name, description, status,
                published_version_id, current_draft_version_id,
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?, NULL)
            """,
            (
                workflow_id,
                str(definition.get("name") or row["name"] or workflow_id),
                str(definition.get("description") or ""),
                version_id,
                created_at,
                updated_at,
            ),
        )
        db.execute(
            """
            INSERT INTO workflow_versions(
                version_id, workflow_id, version_number, state,
                authoring_graph_json, compiled_definition_json,
                compiled_plan_json, validation_json, based_on_version_id,
                created_at, updated_at, published_at
            ) VALUES (?, ?, 1, 'published', ?, ?, NULL, ?, NULL, ?, ?, ?)
            """,
            (
                version_id,
                workflow_id,
                _dump(graph),
                _dump(definition),
                _dump(validation),
                created_at,
                updated_at,
                updated_at,
            ),
        )

    @staticmethod
    def _table_exists(db: sqlite3.Connection, name: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def workflow_header_status(db_path: str | Path, workflow_id: str) -> str | None:
    """Return the V2 header status without treating legacy-only IDs as failures."""

    try:
        return WorkflowVersionStore(db_path).get_workflow(workflow_id).status
    except (KeyError, ValueError):
        return None


def _validated_workflow_id(value: str) -> str:
    workflow_id = str(value or "").strip()
    if not _WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        raise ValueError("workflow_id must contain only letters, digits, dot, dash, or underscore")
    return workflow_id


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return json.loads(_dump(value))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _dump_optional(value: Any) -> str | None:
    return None if value is None else _dump(_json_object(value, "payload"))


def _load_optional(value: Any) -> dict[str, Any] | None:
    if value is None or str(value) == "":
        return None
    payload = json.loads(str(value))
    return dict(payload) if isinstance(payload, dict) else None


def _header_from_row(row: sqlite3.Row) -> WorkflowHeader:
    return WorkflowHeader(
        workflow_id=str(row["workflow_id"]),
        name=str(row["name"]),
        description=str(row["description"] or ""),
        status=str(row["status"]),
        published_version_id=(
            str(row["published_version_id"]) if row["published_version_id"] else None
        ),
        current_draft_version_id=(
            str(row["current_draft_version_id"])
            if row["current_draft_version_id"]
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] else None,
    )


def _version_from_row(row: sqlite3.Row) -> WorkflowVersion:
    stored_graph = dict(json.loads(str(row["authoring_graph_json"])))
    draft_revision = _pop_v3_draft_revision(stored_graph)
    compiled_definition = _load_optional(row["compiled_definition_json"])
    compiled_plan = _load_optional(row["compiled_plan_json"])
    if (
        compiled_plan is None
        and stored_graph.get("schema_version") == 1
        and isinstance(stored_graph.get("legacy_definition"), dict)
        and isinstance(compiled_definition, dict)
    ):
        compiled_plan = compile_legacy_workflow(
            compiled_definition,
            workflow_version_id=str(row["version_id"]),
        )
    version = WorkflowVersion(
        version_id=str(row["version_id"]),
        workflow_id=str(row["workflow_id"]),
        version_number=int(row["version_number"]),
        state=str(row["state"]),
        authoring_graph=stored_graph,
        compiled_definition=compiled_definition,
        compiled_plan=compiled_plan,
        validation=_load_optional(row["validation_json"]),
        based_on_version_id=(
            str(row["based_on_version_id"]) if row["based_on_version_id"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        published_at=str(row["published_at"]) if row["published_at"] else None,
    )
    object.__setattr__(version, "_draft_revision", draft_revision)
    return version


def _pop_v3_draft_revision(graph: dict[str, Any]) -> int | None:
    """Keep the server-only V3 CAS counter out of the public authoring graph."""
    raw_revision = graph.pop(_V3_DRAFT_REVISION_KEY, None)
    if graph.get("schema_version") != 3:
        return None
    try:
        revision = int(raw_revision)
    except (TypeError, ValueError):
        return 1
    return revision if revision >= 1 else 1


def _with_v3_draft_revision(graph: dict[str, Any], revision: int) -> dict[str, Any]:
    stored = _json_object(graph, "authoring_graph")
    stored[_V3_DRAFT_REVISION_KEY] = revision
    return stored


def _empty_graph(header: WorkflowHeader) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "workflow_id": header.workflow_id,
        "name": header.name,
        "description": header.description,
        "nodes": [],
        "edges": [],
        "settings": {"stop_on_error": True, "max_parallelism": 1},
    }


def _legacy_authoring_graph(
    definition: dict[str, Any], workflow_id: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "name": str(definition.get("name") or workflow_id),
        "description": str(definition.get("description") or ""),
        "read_only": True,
        "legacy_definition": definition,
    }


def _legacy_validation() -> dict[str, Any]:
    return {
        "valid": True,
        "errors": [],
        "warnings": [
            {
                "code": "legacy_graph_read_only",
                "message": "Legacy workflow migrated without inventing typed graph dependencies.",
            }
        ],
    }


def _editable_graph_from_base(
    base: WorkflowVersion, header: WorkflowHeader
) -> dict[str, Any]:
    schema_version = base.authoring_graph.get("schema_version")
    if schema_version in {2, 3}:
        return json.loads(_dump(base.authoring_graph))
    if schema_version == 1:
        raise WorkflowVersionError(
            "legacy V1 history is read-only; explicit copy-to-v3 is required"
        )
    raise WorkflowVersionError(
        f"unsupported workflow schema_version: {schema_version!r}"
    )


def _assert_draft_graph_contract(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> None:
    if existing.get("schema_version") != candidate.get("schema_version"):
        raise WorkflowVersionError("schema_version_immutable")
    if existing.get("schema_version") != 3:
        return
    from app.services.workflow_authoring_factory import (
        CanvasAuthoringError,
        assert_v3_technical_ids_preserved,
    )

    try:
        assert_v3_technical_ids_preserved(existing, candidate)
    except CanvasAuthoringError as exc:
        raise WorkflowVersionError(str(exc)) from exc


def legacy_definition_to_v2_graph(
    definition: dict[str, Any], header: WorkflowHeader
) -> dict[str, Any]:
    inputs = [dict(item) for item in definition.get("inputs") or [] if isinstance(item, dict)]
    steps = [dict(item) for item in definition.get("steps") or [] if isinstance(item, dict)]
    outputs = [dict(item) for item in definition.get("outputs") or [] if isinstance(item, dict)]
    supported_builtin = {
        "semantic_retrieve", "memory_retrieve", "local_scope_discover",
        "evidence_validate", "report_render", "artifact_export",
    }
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    input_node_by_contract: dict[str, str] = {}
    step_node_by_id: dict[str, str] = {}

    for index, item in enumerate(inputs):
        contract_id = str(item.get("id") or f"input_{index + 1}")
        node_id = f"input_{contract_id}"
        input_node_by_contract[contract_id] = node_id
        nodes.append({
            "id": node_id,
            "kind": "input",
            "label": str(item.get("label") or contract_id),
            "position": {"x": 80, "y": 100 + index * 140},
            "config": {
                "contract_id": contract_id,
                "label": str(item.get("label") or contract_id),
                "type": str(item.get("type") or "text"),
                "required": bool(item.get("required")),
                "resolver": str(item.get("resolver") or "manual"),
                "role": str(item.get("role") or item.get("description") or ""),
                **({"default_value": item["default_value"]} if "default_value" in item else {}),
                **({"schema": item["schema"]} if isinstance(item.get("schema"), dict) else {}),
            },
        })

    for index, step in enumerate(steps):
        step_id = str(step.get("id") or f"step_{index + 1}")
        node_id = f"step_{step_id}"
        step_node_by_id[step_id] = node_id
        step_type = str(step.get("type") or "agent_task")
        kind = "agent" if step_type == "agent_task" or step_type not in supported_builtin else step_type
        step_outputs = [
            output for output in outputs
            if str(output.get("from") or output.get("source") or "") == step_id
        ]
        input_ports = [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or "any"),
                "required": bool(item.get("required")),
            }
            for item in inputs
            if str(item.get("id") or "")
        ]
        output_ports = [
            {
                "id": str(item.get("id") or f"output_{output_index + 1}"),
                "type": str(item.get("type") or "any"),
            }
            for output_index, item in enumerate(step_outputs)
        ]
        config: dict[str, Any] = {
            "step_id": step_id,
            "input_ports": input_ports,
            "output_ports": output_ports,
            "failure_policy": str(step.get("failure_policy") or "stop"),
            "timeout_sec": int(step.get("timeout_sec") or 900),
            "idle_timeout_sec": int(step.get("idle_timeout_sec") or 120),
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
        }
        if kind == "agent":
            config.update({
                "goal": str(
                    step.get("goal")
                    or f"完成迁移自旧工作流节点 {step_id}（{step_type}）的目标，并生成声明的交付件。"
                ),
                "provider": str(step.get("provider") or "builtin-llm"),
                "mcp_profiles": _string_list(step.get("mcp_profiles") or step.get("mcp_profile")),
                "skill_ids": _string_list(step.get("skills") or step.get("skill_ids")),
                "skill_instructions": [
                    dict(item) for item in step.get("skill_instructions") or []
                    if isinstance(item, dict)
                ],
                "required_artifacts": sorted({
                    str(item.get("artifact") or "") for item in step_outputs
                    if str(item.get("artifact") or "")
                }),
                **({"legacy_step_type": step_type} if step_type != "agent_task" else {}),
            })
        else:
            for key, value in step.items():
                if key not in {"id", "type", "input_ports", "output_ports"}:
                    config.setdefault(str(key), value)
        nodes.append({
            "id": node_id,
            "kind": kind,
            "label": str(step.get("label") or step.get("name") or step_id),
            "position": {"x": 380 + index * 300, "y": 260},
            "config": config,
        })
        for input_index, item in enumerate(inputs):
            contract_id = str(item.get("id") or "")
            if not contract_id:
                continue
            edges.append({
                "id": f"edge_input_{input_index + 1}_{index + 1}",
                "kind": "data",
                "source": {"node_id": input_node_by_contract[contract_id], "port_id": "value"},
                "target": {"node_id": node_id, "port_id": contract_id},
            })
        if index:
            previous_id = str(steps[index - 1].get("id") or f"step_{index}")
            edges.append({
                "id": f"edge_step_{index}_{index + 1}",
                "kind": "dependency",
                "source": {"node_id": step_node_by_id[previous_id], "port_id": "done"},
                "target": {"node_id": node_id, "port_id": "start"},
            })

    for index, output in enumerate(outputs):
        output_id = str(output.get("id") or f"output_{index + 1}")
        source_step_id = str(output.get("from") or output.get("source") or "")
        if source_step_id not in step_node_by_id and steps:
            source_step_id = str(steps[-1].get("id") or f"step_{len(steps)}")
        if source_step_id not in step_node_by_id:
            continue
        node_id = f"output_{output_id}"
        nodes.append({
            "id": node_id,
            "kind": "output",
            "label": str(output.get("label") or output_id),
            "position": {"x": 420 + len(steps) * 300, "y": 100 + index * 140},
            "config": {
                "output_id": output_id,
                "label": str(output.get("label") or output_id),
                "type": str(output.get("type") or "text"),
                "artifact": str(output.get("artifact") or ""),
                "required": bool(output.get("required")),
                "source_node_id": step_node_by_id[source_step_id],
                "source_port_id": output_id,
                **({"schema": output["schema"]} if isinstance(output.get("schema"), dict) else {}),
            },
        })
        edges.append({
            "id": f"edge_output_{index + 1}",
            "kind": "data",
            "source": {"node_id": step_node_by_id[source_step_id], "port_id": output_id},
            "target": {"node_id": node_id, "port_id": "value"},
        })

    return {
        "schema_version": 2,
        "workflow_id": header.workflow_id,
        "name": header.name,
        "description": header.description,
        "nodes": nodes,
        "edges": edges,
        "settings": {"stop_on_error": True, "max_parallelism": 1},
        "migration": {"source": "legacy_definition"},
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return sorted({str(item) for item in value or [] if str(item).strip()})


_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS workbench_schema_meta (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_headers (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
    published_version_id TEXT,
    current_draft_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_versions (
    version_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflow_headers(workflow_id),
    version_number INTEGER NOT NULL CHECK(version_number > 0),
    state TEXT NOT NULL CHECK(state IN ('draft', 'published', 'archived')),
    authoring_graph_json TEXT NOT NULL,
    compiled_definition_json TEXT,
    compiled_plan_json TEXT,
    validation_json TEXT,
    based_on_version_id TEXT REFERENCES workflow_versions(version_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(workflow_id, version_number)
);
CREATE INDEX IF NOT EXISTS idx_workflow_headers_status
    ON workflow_headers(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow
    ON workflow_versions(workflow_id, version_number DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_versions_current_draft
    ON workflow_versions(workflow_id) WHERE state = 'draft';
"""

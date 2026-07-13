"""Local test semantic library for black-box case generation."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SemanticCaseValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"sem_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class SemanticCase:
    semantic_id: str
    case_id: str
    feature: str
    module: str
    scenario: str
    preconditions: list[str]
    actions: list[str]
    expected: list[str]
    test_level: str
    interface: str
    terms: list[str]
    assertion_style: str
    tags: list[str]
    source_ref: str
    status: str
    created_at: str
    updated_at: str


class TestSemanticLibraryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS semantic_cases (
                    semantic_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL UNIQUE,
                    feature TEXT DEFAULT '',
                    module TEXT DEFAULT '',
                    scenario TEXT DEFAULT '',
                    preconditions_json TEXT DEFAULT '[]',
                    actions_json TEXT DEFAULT '[]',
                    expected_json TEXT DEFAULT '[]',
                    test_level TEXT DEFAULT '',
                    interface TEXT DEFAULT '',
                    terms_json TEXT DEFAULT '[]',
                    assertion_style TEXT DEFAULT '',
                    tags_json TEXT DEFAULT '[]',
                    source_ref TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_cases_module
                    ON semantic_cases(module, status, test_level);
                CREATE VIRTUAL TABLE IF NOT EXISTS semantic_case_fts USING fts5(
                    semantic_id UNINDEXED,
                    case_id,
                    feature,
                    module,
                    scenario,
                    terms,
                    tags,
                    assertion_style,
                    tokenize = 'unicode61 tokenchars ''_-/.'''
                );
                """
            )

    def upsert_case(self, payload: dict[str, Any]) -> str:
        self.initialize()
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise SemanticCaseValidationError("case_id is required")
        now = _now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT semantic_id, created_at FROM semantic_cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            semantic_id = str(existing["semantic_id"]) if existing else _new_id()
            created_at = str(existing["created_at"]) if existing else now
            fields = _normalize_case_payload(payload)
            db.execute(
                """
                INSERT OR REPLACE INTO semantic_cases (
                    semantic_id, case_id, feature, module, scenario,
                    preconditions_json, actions_json, expected_json, test_level,
                    interface, terms_json, assertion_style, tags_json, source_ref,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semantic_id,
                    case_id,
                    fields["feature"],
                    fields["module"],
                    fields["scenario"],
                    json.dumps(fields["preconditions"], ensure_ascii=False),
                    json.dumps(fields["actions"], ensure_ascii=False),
                    json.dumps(fields["expected"], ensure_ascii=False),
                    fields["test_level"],
                    fields["interface"],
                    json.dumps(fields["terms"], ensure_ascii=False),
                    fields["assertion_style"],
                    json.dumps(fields["tags"], ensure_ascii=False),
                    fields["source_ref"],
                    fields["status"],
                    created_at,
                    now,
                ),
            )
            db.execute("DELETE FROM semantic_case_fts WHERE semantic_id = ?", (semantic_id,))
            db.execute(
                """
                INSERT INTO semantic_case_fts
                    (semantic_id, case_id, feature, module, scenario, terms, tags, assertion_style)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semantic_id,
                    case_id,
                    fields["feature"],
                    fields["module"],
                    fields["scenario"],
                    " ".join(fields["terms"]),
                    " ".join(fields["tags"]),
                    fields["assertion_style"],
                ),
            )
        return semantic_id

    def import_cases(self, payload: Any) -> dict[str, Any]:
        cases, defaults, source_ref = _normalize_import_payload(payload)
        imported: list[dict[str, str]] = []
        rejected: list[dict[str, Any]] = []
        for index, item in enumerate(cases):
            if not isinstance(item, dict):
                rejected.append({
                    "index": index,
                    "case_id": "",
                    "reason": "semantic case must be an object",
                })
                continue
            case_payload = _merge_case_defaults(item, defaults)
            if source_ref and not str(case_payload.get("source_ref") or "").strip():
                case_payload["source_ref"] = source_ref
            try:
                semantic_id = self.upsert_case(case_payload)
            except SemanticCaseValidationError as exc:
                rejected.append({
                    "index": index,
                    "case_id": str(item.get("case_id") or ""),
                    "reason": str(exc),
                })
                continue
            imported.append({
                "index": index,
                "semantic_id": semantic_id,
                "case_id": str(case_payload.get("case_id") or ""),
            })
        return {
            "imported_count": len(imported),
            "rejected_count": len(rejected),
            "imported": imported,
            "rejected": rejected,
        }

    def import_case_file(
        self,
        data: bytes,
        *,
        filename: str,
        defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_ref = Path(filename or "semantic_cases").name
        defaults_payload = dict(defaults or {})
        text = data.decode("utf-8-sig")
        suffix = Path(source_ref).suffix.lower()
        if suffix == ".json":
            payload = json.loads(text)
            if isinstance(payload, dict):
                payload = dict(payload)
                payload.setdefault("source_ref", source_ref)
                if defaults_payload:
                    merged_defaults = dict(defaults_payload)
                    merged_defaults.update(payload.get("defaults") or {})
                    payload["defaults"] = merged_defaults
            else:
                payload = {
                    "source_ref": source_ref,
                    "defaults": defaults_payload,
                    "cases": payload,
                }
        elif suffix in {".jsonl", ".ndjson"}:
            payload = {
                "source_ref": source_ref,
                "defaults": defaults_payload,
                "cases": [
                    json.loads(line)
                    for line in text.splitlines()
                    if line.strip()
                ],
            }
        elif suffix == ".csv":
            payload = {
                "source_ref": source_ref,
                "defaults": defaults_payload,
                "cases": _cases_from_csv(text),
            }
        else:
            payload = {
                "source_ref": source_ref,
                "defaults": defaults_payload,
                "cases": _cases_from_text_lines(
                    text,
                    module=str(defaults_payload.get("module") or "module"),
                ),
            }
        result = self.import_cases(payload)
        result["source_ref"] = source_ref
        return result

    def retrieve(
        self,
        *,
        query: str,
        module: str = "",
        test_level: str = "",
        limit: int = 10,
        include_deprecated: bool = False,
    ) -> list[SemanticCase]:
        self.initialize()
        params: list[Any] = [_fts_query(query)]
        where = "semantic_case_fts MATCH ?"
        if module:
            where += " AND c.module = ?"
            params.append(module)
        if test_level:
            where += " AND c.test_level = ?"
            params.append(test_level)
        if not include_deprecated:
            where += " AND c.status = 'active'"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT c.*
                FROM semantic_case_fts f
                JOIN semantic_cases c ON c.semantic_id = f.semantic_id
                WHERE {where}
                ORDER BY bm25(semantic_case_fts), c.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_case(row) for row in rows]

    def list_cases(
        self,
        *,
        q: str = "",
        feature: str = "",
        module: str = "",
        test_level: str = "",
        interface: str = "",
        tag: str = "",
        status: str = "",
        source: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        join = ""
        if q.strip():
            join = "JOIN semantic_case_fts f ON f.semantic_id = c.semantic_id"
            clauses.append("semantic_case_fts MATCH ?")
            params.append(_fts_query(q))
        for column, value in (
            ("feature", feature),
            ("module", module),
            ("test_level", test_level),
            ("interface", interface),
            ("status", status),
            ("source_ref", source),
        ):
            if value:
                clauses.append(f"c.{column} = ?")
                params.append(value)
        if tag:
            clauses.append("EXISTS (SELECT 1 FROM json_each(c.tags_json) WHERE value = ?)")
            params.append(tag)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        ordering = "bm25(semantic_case_fts), c.updated_at DESC" if q.strip() else "c.updated_at DESC"
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(200, int(page_size)))
        with self._connect() as db:
            total = int(db.execute(
                f"SELECT COUNT(*) FROM semantic_cases c {join} {where}", params
            ).fetchone()[0])
            rows = db.execute(
                f"""
                SELECT c.* FROM semantic_cases c {join} {where}
                ORDER BY {ordering}
                LIMIT ? OFFSET ?
                """,
                [*params, safe_page_size, (safe_page - 1) * safe_page_size],
            ).fetchall()
        items = [_row_to_case(row) for row in rows]
        matched_fields = _matched_case_fields(items, q)
        return {
            "items": items,
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
            "matched_fields": matched_fields,
        }

    def get_case(self, semantic_id: str) -> SemanticCase:
        self.initialize()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM semantic_cases WHERE semantic_id = ?", (semantic_id,)
            ).fetchone()
        if row is None:
            raise KeyError(semantic_id)
        return _row_to_case(row)

    def update_case(self, semantic_id: str, changes: dict[str, Any]) -> SemanticCase:
        current = self.get_case(semantic_id)
        allowed = {
            "case_id", "feature", "module", "scenario", "preconditions", "actions",
            "expected", "test_level", "interface", "terms", "assertion_style", "tags",
            "source_ref", "status",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise SemanticCaseValidationError(f"unknown semantic case fields: {', '.join(unknown)}")
        payload = asdict(current)
        payload.update(changes)
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise SemanticCaseValidationError("case_id is required")
        fields = _normalize_case_payload(payload)
        now = _now()
        with self._connect() as db:
            conflict = db.execute(
                "SELECT semantic_id FROM semantic_cases WHERE case_id = ? AND semantic_id != ?",
                (case_id, semantic_id),
            ).fetchone()
            if conflict:
                raise SemanticCaseValidationError(f"case_id already exists: {case_id}")
            db.execute(
                """
                UPDATE semantic_cases SET
                    case_id = ?, feature = ?, module = ?, scenario = ?,
                    preconditions_json = ?, actions_json = ?, expected_json = ?,
                    test_level = ?, interface = ?, terms_json = ?, assertion_style = ?,
                    tags_json = ?, source_ref = ?, status = ?, updated_at = ?
                WHERE semantic_id = ?
                """,
                (
                    case_id, fields["feature"], fields["module"], fields["scenario"],
                    json.dumps(fields["preconditions"], ensure_ascii=False),
                    json.dumps(fields["actions"], ensure_ascii=False),
                    json.dumps(fields["expected"], ensure_ascii=False),
                    fields["test_level"], fields["interface"],
                    json.dumps(fields["terms"], ensure_ascii=False), fields["assertion_style"],
                    json.dumps(fields["tags"], ensure_ascii=False), fields["source_ref"],
                    fields["status"], now, semantic_id,
                ),
            )
            _replace_fts_row(db, semantic_id=semantic_id, case_id=case_id, fields=fields)
        return self.get_case(semantic_id)

    def deprecate_case(self, semantic_id: str) -> SemanticCase:
        return self.update_case(semantic_id, {"status": "deprecated"})

    def restore_case(self, semantic_id: str) -> SemanticCase:
        return self.update_case(semantic_id, {"status": "active"})

    def facets(self) -> dict[str, list[dict[str, Any]]]:
        self.initialize()
        with self._connect() as db:
            result = {
                key: _facet_rows(db, column)
                for key, column in (
                    ("features", "feature"),
                    ("modules", "module"),
                    ("test_levels", "test_level"),
                    ("interfaces", "interface"),
                    ("statuses", "status"),
                    ("sources", "source_ref"),
                )
            }
            result["tags"] = [
                {"value": str(row["value"]), "count": int(row["count"])}
                for row in db.execute(
                    """
                    SELECT value, COUNT(*) AS count
                    FROM semantic_cases, json_each(semantic_cases.tags_json)
                    WHERE value != '' GROUP BY value ORDER BY count DESC, value
                    """
                ).fetchall()
            ]
        return result

    def preview_case_file(
        self,
        data: bytes,
        *,
        filename: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        options = dict(options or {})
        defaults = options.get("defaults") or {}
        mapping = options.get("mapping") or {}
        if not isinstance(defaults, dict) or not isinstance(mapping, dict):
            raise SemanticCaseValidationError("defaults and mapping must be objects")
        source_ref = Path(filename or "semantic_cases").name
        suffix = Path(source_ref).suffix.lower()
        text = data.decode("utf-8-sig")
        raw_rows, source_fields = _preview_rows_from_file(
            text, suffix=suffix, text_separator=str(options.get("text_separator") or "")
        )
        known_fields = {
            "case_id", "feature", "module", "scenario", "preconditions", "actions",
            "expected", "test_level", "interface", "terms", "assertion_style", "tags",
            "source_ref", "status",
        }
        unknown_fields = sorted(
            field for field in source_fields
            if str(mapping.get(field) or field) not in known_fields
        )
        existing_by_id = self._existing_case_ids()
        existing_scenarios = self._existing_scenarios()
        seen_ids: set[str] = set()
        seen_scenarios: set[str] = set()
        rows: list[dict[str, Any]] = []
        duplicate_ids: set[str] = set()
        possible_duplicates: set[str] = set()
        for index, raw in enumerate(raw_rows):
            mapped = _map_preview_row(raw, mapping=mapping, known_fields=known_fields)
            payload = _merge_case_defaults(mapped, defaults)
            payload["source_ref"] = str(payload.get("source_ref") or source_ref)
            case_id = str(payload.get("case_id") or "").strip()
            scenario = str(payload.get("scenario") or "").strip()
            expected = _string_list(payload.get("expected"))
            errors: list[str] = []
            if not case_id:
                errors.append("缺少 case_id")
            if not scenario:
                errors.append("缺少 scenario")
            if not expected:
                errors.append("缺少 expected")
            if case_id and (case_id in existing_by_id or case_id in seen_ids):
                duplicate_ids.add(case_id)
            normalized_scenario = _normalize_scenario(scenario)
            if normalized_scenario and (
                normalized_scenario in existing_scenarios or normalized_scenario in seen_scenarios
            ):
                possible_duplicates.add(scenario)
            if case_id:
                seen_ids.add(case_id)
            if normalized_scenario:
                seen_scenarios.add(normalized_scenario)
            rows.append({
                "index": index,
                "case": payload,
                "errors": errors,
                "warnings": (["case_id 已存在"] if case_id in existing_by_id else [])
                    + (["场景可能重复"] if scenario in possible_duplicates else []),
            })
        valid_count = sum(not row["errors"] for row in rows)
        return {
            "source_ref": source_ref,
            "total_count": len(rows),
            "valid_count": valid_count,
            "invalid_count": len(rows) - valid_count,
            "missing_case_id": sum("缺少 case_id" in row["errors"] for row in rows),
            "missing_scenario": sum("缺少 scenario" in row["errors"] for row in rows),
            "missing_expected": sum("缺少 expected" in row["errors"] for row in rows),
            "duplicate_case_ids": sorted(duplicate_ids),
            "possible_duplicate_scenarios": sorted(possible_duplicates),
            "unknown_fields": unknown_fields,
            "mapping": mapping,
            "rows": rows,
        }

    def commit_preview(self, preview: dict[str, Any], *, conflict_strategy: str) -> dict[str, Any]:
        if conflict_strategy not in {"skip", "overwrite", "create_new"}:
            raise SemanticCaseValidationError("conflict_strategy must be skip, overwrite, or create_new")
        imported: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped_count = 0
        for row in preview.get("rows") or []:
            if not isinstance(row, dict):
                continue
            payload = dict(row.get("case") or {})
            errors = list(row.get("errors") or [])
            if errors:
                failed.append({"index": row.get("index"), "case": payload, "reasons": errors})
                continue
            case_id = str(payload.get("case_id") or "")
            exists = self._case_id_exists(case_id)
            if exists and conflict_strategy == "skip":
                skipped_count += 1
                continue
            if exists and conflict_strategy == "create_new":
                payload["case_id"] = self._next_case_id(case_id)
            semantic_id = self.upsert_case(payload)
            imported.append({"index": row.get("index"), "semantic_id": semantic_id, "case_id": payload["case_id"]})
        return {
            "imported_count": len(imported),
            "skipped_count": skipped_count,
            "failed_count": len(failed),
            "imported": imported,
            "failed": failed,
        }

    def _case_id_exists(self, case_id: str) -> bool:
        self.initialize()
        with self._connect() as db:
            return db.execute("SELECT 1 FROM semantic_cases WHERE case_id = ?", (case_id,)).fetchone() is not None

    def _existing_case_ids(self) -> set[str]:
        with self._connect() as db:
            return {str(row[0]) for row in db.execute("SELECT case_id FROM semantic_cases")}

    def _existing_scenarios(self) -> set[str]:
        with self._connect() as db:
            return {_normalize_scenario(str(row[0])) for row in db.execute("SELECT scenario FROM semantic_cases") if str(row[0]).strip()}

    def _next_case_id(self, base: str) -> str:
        index = 2
        while self._case_id_exists(f"{base}__{index}"):
            index += 1
        return f"{base}__{index}"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


def _normalize_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature": str(payload.get("feature") or ""),
        "module": str(payload.get("module") or ""),
        "scenario": str(payload.get("scenario") or ""),
        "preconditions": _string_list(payload.get("preconditions")),
        "actions": _string_list(payload.get("actions")),
        "expected": _string_list(payload.get("expected")),
        "test_level": str(payload.get("test_level") or "black_box"),
        "interface": str(payload.get("interface") or ""),
        "terms": _string_list(payload.get("terms")),
        "assertion_style": str(payload.get("assertion_style") or ""),
        "tags": _string_list(payload.get("tags")),
        "source_ref": str(payload.get("source_ref") or ""),
        "status": str(payload.get("status") or "active"),
    }


def _normalize_import_payload(payload: Any) -> tuple[list[Any], dict[str, Any], str]:
    if isinstance(payload, list):
        return list(payload), {}, ""
    if not isinstance(payload, dict):
        raise SemanticCaseValidationError("semantic case import payload must be an object or list")
    raw_cases = payload.get("cases")
    if raw_cases is None:
        raw_cases = payload.get("items")
    if raw_cases is None and isinstance(payload.get("case_id"), str):
        raw_cases = [payload]
    if not isinstance(raw_cases, list):
        raise SemanticCaseValidationError("semantic case import cases must be a list")
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise SemanticCaseValidationError("semantic case import defaults must be an object")
    return list(raw_cases), dict(defaults), str(payload.get("source_ref") or "")


def _merge_case_defaults(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(item)
    for list_key in ("tags", "terms", "preconditions", "actions", "expected"):
        if list_key in defaults and list_key in item:
            merged[list_key] = _dedupe_strings([
                *_string_list(defaults.get(list_key)),
                *_string_list(item.get(list_key)),
            ])
    return merged


def _cases_from_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    cases: list[dict[str, Any]] = []
    for row in reader:
        case: dict[str, Any] = {}
        for key, value in row.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            normalized_value = str(value or "").strip()
            if normalized_key in {"preconditions", "actions", "expected", "terms", "tags"}:
                case[normalized_key] = _split_cell_list(normalized_value)
            else:
                case[normalized_key] = normalized_value
        cases.append(case)
    return cases


def _cases_from_text_lines(text: str, *, module: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    safe_module = _case_id_segment(module or "module")
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        scenario, expected = _split_scenario_expected(line)
        scenario_text = scenario or line
        cases.append({
            "case_id": f"{safe_module}_{_case_id_segment(scenario_text)}_{index}",
            "scenario": scenario_text,
            "actions": [scenario_text],
            "expected": [expected or "Expected observable behavior matches the existing feature case."],
            "tags": ["imported_semantic_case"],
        })
    return cases


def _split_cell_list(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;|]", value) if part.strip()]


def _split_scenario_expected(line: str) -> tuple[str, str]:
    parts = re.split(r"\s*(?:->|=>|:)\s*", line, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return line.strip(), ""


def _case_id_segment(value: str) -> str:
    segment = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    segment = re.sub(r"_+", "_", segment).strip("_")
    return segment[:48] or "case"


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _row_to_case(row: sqlite3.Row) -> SemanticCase:
    data = dict(row)
    return SemanticCase(
        semantic_id=data["semantic_id"],
        case_id=data["case_id"],
        feature=data["feature"],
        module=data["module"],
        scenario=data["scenario"],
        preconditions=_json_list(data["preconditions_json"]),
        actions=_json_list(data["actions_json"]),
        expected=_json_list(data["expected_json"]),
        test_level=data["test_level"],
        interface=data["interface"],
        terms=_json_list(data["terms_json"]),
        assertion_style=data["assertion_style"],
        tags=_json_list(data["tags_json"]),
        source_ref=data["source_ref"],
        status=data["status"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return []


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return _string_list(parsed)


def _fts_query(query: str) -> str:
    terms = [part.replace('"', '""') for part in str(query or "").split() if part.strip()]
    return " ".join(f'"{term}"' for term in terms) or '""'


def _replace_fts_row(
    db: sqlite3.Connection,
    *,
    semantic_id: str,
    case_id: str,
    fields: dict[str, Any],
) -> None:
    db.execute("DELETE FROM semantic_case_fts WHERE semantic_id = ?", (semantic_id,))
    db.execute(
        """
        INSERT INTO semantic_case_fts
            (semantic_id, case_id, feature, module, scenario, terms, tags, assertion_style)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            semantic_id, case_id, fields["feature"], fields["module"], fields["scenario"],
            " ".join(fields["terms"]), " ".join(fields["tags"]), fields["assertion_style"],
        ),
    )


def _facet_rows(db: sqlite3.Connection, column: str) -> list[dict[str, Any]]:
    return [
        {"value": str(row["value"]), "count": int(row["count"])}
        for row in db.execute(
            f"SELECT {column} AS value, COUNT(*) AS count FROM semantic_cases "
            f"WHERE {column} != '' GROUP BY {column} ORDER BY count DESC, value"
        ).fetchall()
    ]


def _matched_case_fields(items: list[SemanticCase], query: str) -> list[str]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    matched: set[str] = set()
    for item in items:
        values = {
            "case_id": item.case_id,
            "feature": item.feature,
            "module": item.module,
            "scenario": item.scenario,
            "terms": " ".join(item.terms),
            "tags": " ".join(item.tags),
            "assertion_style": item.assertion_style,
        }
        for key, value in values.items():
            lowered = value.casefold()
            if any(term in lowered for term in terms):
                matched.add(key)
    return sorted(matched)


def _preview_rows_from_file(text: str, *, suffix: str, text_separator: str) -> tuple[list[dict[str, Any]], set[str]]:
    if suffix == ".json":
        parsed = json.loads(text)
        rows = parsed.get("cases", parsed.get("items", [])) if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            raise SemanticCaseValidationError("JSON import must contain a list of cases")
    elif suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif suffix == ".csv":
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
    elif suffix in {".txt", ".md", ".markdown"}:
        delimiters = {"pipe": "|", "tab": "\t", "arrow": "->"}
        delimiter = delimiters.get(text_separator)
        if delimiter is None:
            raise SemanticCaseValidationError("text_separator must be explicitly set to pipe, tab, or arrow")
        rows = []
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [part.strip() for part in line.split(delimiter)]
            rows.append({
                "case_id": parts[0] if len(parts) > 0 else "",
                "scenario": parts[1] if len(parts) > 1 else "",
                "expected": parts[2] if len(parts) > 2 else "",
            })
    else:
        raise SemanticCaseValidationError("unsupported semantic import format")
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    fields = {str(key) for row in normalized_rows for key in row}
    return normalized_rows, fields


def _map_preview_row(
    raw: dict[str, Any], *, mapping: dict[str, Any], known_fields: set[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    list_fields = {"preconditions", "actions", "expected", "terms", "tags"}
    for source, value in raw.items():
        target = str(mapping.get(source) or source)
        if target not in known_fields:
            continue
        result[target] = _split_cell_list(str(value or "")) if target in list_fields else str(value or "").strip()
    return result


def _normalize_scenario(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())

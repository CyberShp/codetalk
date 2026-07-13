"""Verified, one-time backup for additive Workbench V2 migrations."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


_BACKUP_LOCK = threading.RLock()
_BACKUP_GLOB = "workflows.pre-workbench-v2.*.bak"


def ensure_workbench_migration_backup(db_path: str | Path) -> Path | None:
    """Back up an existing Workbench database before its first V2 migration.

    SQLite's backup API includes committed WAL contents and produces a
    self-contained database. An existing verified pre-V2 backup is never
    overwritten, so a partially completed migration cannot replace it.
    """

    source_path = Path(db_path)
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None

    with _BACKUP_LOCK:
        existing = sorted(source_path.parent.glob(_BACKUP_GLOB))
        if existing:
            _verify_backup(existing[0])
            return existing[0]

        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
            if not _has_persisted_workbench_data(source):
                return None
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = source_path.with_name(
                f"{source_path.stem}.pre-workbench-v2.{stamp}.bak"
            )
            try:
                with sqlite3.connect(backup_path) as destination:
                    source.backup(destination)
                _verify_backup(backup_path)
            except Exception:
                backup_path.unlink(missing_ok=True)
                raise
            return backup_path


def _has_persisted_workbench_data(db: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "workflow_definitions" in tables:
        return True
    for table in ("workflow_headers", "workflow_versions", "workbench_tasks"):
        if table in tables and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return True
    return False


def _verify_backup(path: Path) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        result = db.execute("PRAGMA quick_check").fetchone()
    if result is None or str(result[0]).lower() != "ok":
        raise sqlite3.DatabaseError(f"Workbench migration backup failed verification: {path}")

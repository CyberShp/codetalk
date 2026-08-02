from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.knowledge_store import KnowledgeStore, SCHEMA_VERSION


def test_initialize_tracks_schema_version_and_backs_up_before_upgrade(tmp_path):
    db_path = tmp_path / "knowledge.sqlite3"
    store = KnowledgeStore(db_path)

    assert store.initialize() == SCHEMA_VERSION
    assert store.schema_version() == SCHEMA_VERSION

    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE knowledge_schema SET version = 0")
        db.commit()

    assert store.initialize() == SCHEMA_VERSION
    assert (tmp_path / "knowledge.sqlite3.pre-v0.bak").exists()


def test_sources_dedupe_exact_hash_keep_locators_and_link_incidents_to_versioned_patterns(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    first = store.register_source(
        source_kind="paste",
        source_identity="paste:iscsi-cmdsn",
        content=b"CmdSN stops advancing",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        locators=[{"kind": "line", "start": 1, "end": 1, "excerpt": "CmdSN stops advancing"}],
    )
    duplicate = store.register_source(
        source_kind="paste",
        source_identity="paste:iscsi-cmdsn",
        content=b"CmdSN stops advancing",
        scope="project",
        workspace_identity="codehub.example/storage/array",
    )
    revised = store.register_source(
        source_kind="paste",
        source_identity="paste:iscsi-cmdsn",
        content=b"CmdSN stops advancing and availability shrinks",
        scope="project",
        workspace_identity="codehub.example/storage/array",
    )

    assert duplicate["duplicate"] is True
    assert duplicate["source_snapshot_id"] == first["source_snapshot_id"]
    assert revised["duplicate"] is False
    assert revised["source_document_id"] == first["source_document_id"]
    assert store.list_source_locators(first["source_snapshot_id"])[0]["kind"] == "line"

    incident = store.create_incident(
        title="iSCSI CmdSN recovery stalls",
        summary="Resource count recovers but advertised availability stays at zero.",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        source_snapshot_ids=[first["source_snapshot_id"], revised["source_snapshot_id"]],
        terms=["iSCSI", "CmdSN", "resource"],
    )
    pattern = store.create_pattern(
        name="resource recovery needs protocol sequence progress",
        content="Verify availability advertisement resumes after resource pressure recovers.",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        applicability=["resource exhaustion", "iSCSI"],
        exclusions=["explicit recovery reset"],
    )
    second_version = store.add_pattern_version(
        pattern["pattern_id"],
        content="Verify CmdSN and advertised availability resume after resource pressure recovers.",
    )
    store.link_incident_pattern(incident["incident_id"], pattern["pattern_id"], second_version["pattern_version_id"])

    assert store.get_pattern(pattern["pattern_id"])["active_version_id"] == second_version["pattern_version_id"]
    assert store.list_pattern_incidents(pattern["pattern_id"])[0]["incident_id"] == incident["incident_id"]
    assert store.search_experience("CmdSN availability", scope="project", workspace_identity="codehub.example/storage/array")[0]["record_type"] == "pattern"


def test_file_hash_deduplicates_across_source_identities_and_versions_can_be_restored(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    content = Path(__file__).with_name("fixtures").joinpath("knowledge", "paste_incident.txt").read_bytes()
    original = store.register_source(
        source_kind="file", source_identity="file:/imports/a.txt", content=content, scope="personal_global"
    )
    duplicate = store.register_source(
        source_kind="file", source_identity="file:/imports/copied.txt", content=content, scope="personal_global"
    )
    pattern = store.create_pattern(
        name="resource sequence recovery", content="initial version", scope="personal_global"
    )
    newer = store.add_pattern_version(pattern["pattern_id"], content="updated version")
    restored = store.restore_pattern_version(pattern["pattern_id"], pattern["active_version_id"])

    assert duplicate["duplicate"] is True
    assert duplicate["source_snapshot_id"] == original["source_snapshot_id"]
    assert restored["active_version_id"] == pattern["active_version_id"]
    assert newer["pattern_version_id"] != restored["active_version_id"]


def test_import_stages_are_retryable_and_feedback_is_separate_from_truth(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    job = store.create_import_job(source_count=2, scope="personal_global")
    store.start_import_stage(job["job_id"], "parse")
    failed = store.fail_import_stage(job["job_id"], "parse", "malformed workbook")
    retried = store.retry_import_stage(job["job_id"], "parse")
    completed = store.complete_import_stage(job["job_id"], "parse", processed_count=2)
    feedback = store.record_feedback(
        subject_type="pattern",
        subject_id="pat_example",
        outcome="irrelevant",
        workspace_identity="codehub.example/storage/array",
        note="Same term, different lifecycle.",
    )

    assert failed["status"] == "failed"
    assert retried["attempt"] == 2
    assert completed["status"] == "completed"
    assert feedback["outcome"] == "irrelevant"
    assert store.get_import_job(job["job_id"])["status"] == "running"


def test_import_job_keeps_ordered_source_snapshot_associations(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    job = store.create_import_job(source_count=1, scope="personal_global")
    source = store.register_source(
        source_kind="paste",
        source_identity="paste:incident",
        content=b"CmdSN recovery",
        scope="personal_global",
        locators=[{"kind": "line", "start": 1, "end": 1}],
    )

    store.attach_import_source(
        job["job_id"],
        source["source_snapshot_id"],
        filename="incident.txt",
        parser="text",
        parse_status="parsed",
    )

    sources = store.list_import_sources(job["job_id"], include_content=True)
    assert sources[0]["source_snapshot_id"] == source["source_snapshot_id"]
    assert sources[0]["filename"] == "incident.txt"
    assert sources[0]["content"] == b"CmdSN recovery"
    assert sources[0]["locators"][0]["kind"] == "line"


def test_links_enforce_foreign_keys_and_pattern_states_and_merge_proposals_are_persistent(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    source = store.register_source(
        source_kind="paste", source_identity="paste:one", content=b"one", scope="personal_global"
    )
    incident = store.create_incident(
        title="incident", summary="summary", scope="personal_global", source_snapshot_ids=[source["source_snapshot_id"]]
    )
    first = store.create_pattern(name="pattern", content="first", scope="personal_global")
    second = store.create_pattern(name="pattern two", content="second", scope="personal_global")

    with pytest.raises(sqlite3.IntegrityError):
        store.create_incident(title="broken", summary="broken", scope="personal_global", source_snapshot_ids=["snap_missing"])
    with pytest.raises(sqlite3.IntegrityError):
        store.link_incident_pattern("inc_missing", first["pattern_id"], first["active_version_id"])
    with pytest.raises(sqlite3.IntegrityError):
        store.link_incident_pattern(incident["incident_id"], first["pattern_id"], "patver_missing")
    with pytest.raises(sqlite3.IntegrityError):
        store.link_incident_pattern(incident["incident_id"], first["pattern_id"], second["active_version_id"])

    changed = store.update_pattern_states(first["pattern_id"], review_state="confirmed", lifecycle_state="deprecated")
    proposal = store.create_merge_proposal(
        subject_type="pattern", source_id=first["pattern_id"], candidate_id=second["pattern_id"], similarity=0.91
    )

    assert changed["review_state"] == "confirmed"
    assert changed["lifecycle_state"] == "deprecated"
    assert proposal["status"] == "proposed"
    assert store.list_merge_proposals(subject_type="pattern")[0]["proposal_id"] == proposal["proposal_id"]

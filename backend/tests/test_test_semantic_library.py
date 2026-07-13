def test_test_semantic_library_imports_and_retrieves_black_box_cases(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    store.initialize()
    tls_id = store.upsert_case({
        "case_id": "TC_NVMF_TLS_001",
        "feature": "NVMe TCP TLS",
        "module": "nvmf_tcp/transport/tls",
        "scenario": "TLS handshake fails when certificate is invalid",
        "preconditions": ["TLS enabled", "invalid client certificate"],
        "actions": ["create NVMe TCP connection"],
        "expected": ["connection rejected", "authentication failure is observable"],
        "test_level": "black_box",
        "interface": "RPC/CLI",
        "terms": ["handshake", "certificate", "authentication failure"],
        "assertion_style": "status + log + connection state",
        "tags": ["negative", "security", "transport", "tls"],
        "source_ref": "cases/nvmf_tls.xlsx",
        "status": "active",
    })
    store.upsert_case({
        "case_id": "TC_OLD_001",
        "feature": "Legacy TCP",
        "module": "legacy/tcp",
        "scenario": "old inactive case",
        "test_level": "black_box",
        "terms": ["handshake"],
        "status": "deprecated",
    })

    results = store.retrieve(
        query="certificate handshake",
        module="nvmf_tcp/transport/tls",
        test_level="black_box",
    )

    assert [item.case_id for item in results] == ["TC_NVMF_TLS_001"]
    assert results[0].semantic_id == tls_id
    assert results[0].terms == ["handshake", "certificate", "authentication failure"]
    assert results[0].assertion_style == "status + log + connection state"


def test_test_semantic_library_rejects_missing_case_id(tmp_path):
    from app.services.test_semantic_library import (
        SemanticCaseValidationError,
        TestSemanticLibraryStore,
    )

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    store.initialize()

    try:
        store.upsert_case({"feature": "NVMe TCP TLS"})
    except SemanticCaseValidationError as exc:
        assert "case_id" in str(exc)
    else:
        raise AssertionError("missing case_id should be rejected")


def test_test_semantic_library_bulk_imports_cases_with_defaults(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    result = store.import_cases({
        "source_ref": "feature_cases/nvmf_tls.json",
        "defaults": {
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
            "tags": ["regression"],
        },
        "cases": [
            {
                "case_id": "TC_TLS_CERT_REJECT",
                "scenario": "certificate rejected during TLS handshake",
                "terms": ["certificate", "handshake"],
            },
            {
                "case_id": "",
                "scenario": "bad row",
            },
            {
                "case_id": "TC_TLS_CLEANUP",
                "scenario": "connection resources are released after auth failure",
                "terms": ["connection release"],
                "tags": ["cleanup"],
            },
        ],
    })

    assert result["imported_count"] == 2
    assert result["rejected_count"] == 1
    assert result["rejected"][0]["index"] == 1
    assert result["rejected"][0]["reason"] == "case_id is required"
    assert [item["case_id"] for item in result["imported"]] == [
        "TC_TLS_CERT_REJECT",
        "TC_TLS_CLEANUP",
    ]

    results = store.retrieve(
        query="certificate handshake",
        module="nvmf_tcp/transport/tls",
        test_level="black_box",
    )
    assert [item.case_id for item in results] == ["TC_TLS_CERT_REJECT"]
    assert results[0].source_ref == "feature_cases/nvmf_tls.json"
    assert results[0].tags == ["regression"]


def test_test_semantic_library_bulk_import_accepts_top_level_list(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    result = store.import_cases([
        {
            "case_id": "TC_DIRECT_LIST",
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp/transport/tls",
            "scenario": "direct list import",
            "terms": ["direct", "list"],
        }
    ])

    assert result["imported_count"] == 1
    assert result["rejected_count"] == 0


def test_test_semantic_library_imports_csv_case_file(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    result = store.import_case_file(
        b"case_id,scenario,actions,expected,terms\n"
        b"TC_TLS_FILE,TLS file import,connect;fail,released;logged,tls;cleanup\n",
        filename="nvmf_tls_cases.csv",
        defaults={
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
            "tags": ["imported_file"],
        },
    )

    assert result["source_ref"] == "nvmf_tls_cases.csv"
    assert result["imported_count"] == 1
    assert result["imported"][0]["case_id"] == "TC_TLS_FILE"
    results = store.retrieve(
        query="cleanup",
        module="nvmf_tcp/transport/tls",
        test_level="black_box",
    )
    assert results[0].case_id == "TC_TLS_FILE"
    assert results[0].actions == ["connect", "fail"]
    assert results[0].expected == ["released", "logged"]
    assert results[0].terms == ["tls", "cleanup"]


def test_test_semantic_library_imports_text_case_file_with_defaults(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    result = store.import_case_file(
        "TLS disabled by config -> non-TLS mode is reported\n"
        "invalid certificate -> connection is rejected".encode("utf-8"),
        filename="tls_cases.txt",
        defaults={
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp",
            "test_level": "black_box",
        },
    )

    assert result["imported_count"] == 2
    assert [item["case_id"] for item in result["imported"]] == [
        "nvmf_tcp_tls_disabled_by_config_1",
        "nvmf_tcp_invalid_certificate_2",
    ]


def test_semantic_asset_management_reindexes_and_preserves_lifecycle(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    semantic_id = store.upsert_case({
        "case_id": "TC_ISCSI_LOGIN_001",
        "feature": "iSCSI",
        "module": "iscsi/login",
        "scenario": "Login succeeds with valid CHAP credentials",
        "preconditions": ["target requires CHAP"],
        "actions": ["connect with valid credentials"],
        "expected": ["session becomes active"],
        "test_level": "black_box",
        "interface": "iSCSI wire protocol",
        "tags": ["authentication"],
        "source_ref": "test/iscsi_tgt",
    })

    page = store.list_cases(q="CHAP", module="iscsi/login", page=1, page_size=20)
    assert page["total"] == 1
    assert page["items"][0].semantic_id == semantic_id
    assert "scenario" in page["matched_fields"]

    updated = store.update_case(semantic_id, {
        "scenario": "Login rejects an expired mutual CHAP secret",
        "expected": ["authentication failure is observable"],
        "tags": ["authentication", "negative"],
    })
    assert updated.case_id == "TC_ISCSI_LOGIN_001"
    assert store.retrieve(query="expired mutual", limit=10)[0].semantic_id == semantic_id
    assert store.retrieve(query="succeeds valid", limit=10) == []

    assert store.deprecate_case(semantic_id).status == "deprecated"
    assert store.list_cases(status="active", page=1, page_size=20)["total"] == 0
    assert store.get_case(semantic_id).scenario == updated.scenario
    assert store.restore_case(semantic_id).status == "active"

    facets = store.facets()
    assert facets["features"] == [{"value": "iSCSI", "count": 1}]
    assert {item["value"] for item in facets["tags"]} == {"authentication", "negative"}


def test_semantic_import_preview_is_non_mutating_and_requires_explicit_conflict_strategy(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    original_id = store.upsert_case({
        "case_id": "TC_TLS_001",
        "feature": "NVMe TCP TLS",
        "module": "nvmf/tcp/tls",
        "scenario": "TLS handshake succeeds",
        "expected": ["controller becomes live"],
    })
    preview = store.preview_case_file(
        (
            "id,title,result,extra\n"
            "TC_TLS_001,TLS handshake rejects invalid cert,connection is rejected,ignored\n"
            ",missing identifier,visible failure,ignored\n"
        ).encode(),
        filename="tls.csv",
        options={
            "mapping": {"id": "case_id", "title": "scenario", "result": "expected"},
            "defaults": {"feature": "NVMe TCP TLS", "module": "nvmf/tcp/tls"},
        },
    )

    assert preview["total_count"] == 2
    assert preview["valid_count"] == 1
    assert preview["invalid_count"] == 1
    assert preview["duplicate_case_ids"] == ["TC_TLS_001"]
    assert preview["unknown_fields"] == ["extra"]
    assert preview["rows"][1]["errors"] == ["缺少 case_id"]
    assert store.get_case(original_id).scenario == "TLS handshake succeeds"

    skipped = store.commit_preview(preview, conflict_strategy="skip")
    assert skipped["imported_count"] == 0
    assert skipped["skipped_count"] == 1
    assert skipped["failed_count"] == 1
    assert store.get_case(original_id).scenario == "TLS handshake succeeds"

    overwritten = store.commit_preview(preview, conflict_strategy="overwrite")
    assert overwritten["imported_count"] == 1
    assert store.get_case(original_id).scenario == "TLS handshake rejects invalid cert"

    created = store.commit_preview(preview, conflict_strategy="create_new")
    assert created["imported_count"] == 1
    assert created["imported"][0]["case_id"] == "TC_TLS_001__2"


def test_semantic_text_preview_requires_separator_and_never_invents_expected(tmp_path):
    from app.services.test_semantic_library import (
        SemanticCaseValidationError,
        TestSemanticLibraryStore,
    )

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    try:
        store.preview_case_file(
            b"TC_1 | connect without credentials | login is rejected",
            filename="cases.md",
            options={"defaults": {"module": "iscsi/login"}},
        )
    except SemanticCaseValidationError as exc:
        assert "separator" in str(exc)
    else:
        raise AssertionError("text import without an explicit separator must fail")

    preview = store.preview_case_file(
        b"TC_1 | connect without credentials | login is rejected\nTC_2 | incomplete row",
        filename="cases.md",
        options={"text_separator": "pipe", "defaults": {"module": "iscsi/login"}},
    )
    assert preview["valid_count"] == 1
    assert preview["rows"][1]["errors"] == ["缺少 expected"]
    assert store.list_cases(page=1, page_size=20)["total"] == 0

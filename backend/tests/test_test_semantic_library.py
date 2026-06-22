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

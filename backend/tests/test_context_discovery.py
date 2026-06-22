import pytest


@pytest.mark.asyncio
async def test_fast_context_unavailable_is_non_blocking(tmp_path):
    from app.services.context_discovery import (
        ContextDiscoveryRequest,
        run_context_source_discovery,
    )

    results = await run_context_source_discovery(
        ContextDiscoveryRequest(
            request_id="req-1",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["fast-context"],
    )

    assert len(results) == 1
    assert results[0].provider == "fast-context"
    assert results[0].status == "unavailable"
    assert results[0].candidate_files == []
    assert "not configured" in results[0].warnings[0]


@pytest.mark.asyncio
async def test_fast_context_candidates_are_locally_validated(tmp_path):
    from app.services.context_discovery import (
        ContextDiscoveryRequest,
        run_context_source_discovery,
    )

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    (src / "tls.c").write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")

    async def fake_search(_request):
        return {
            "candidate_files": [
                {
                    "path": "nof/nvmf_tcp/transport/tls/tls.c",
                    "reason": "fast-context semantic path match",
                    "confidence": "high",
                    "evidence_excerpt": "nvmf_tcp_tls_handshake",
                }
            ],
            "commands": ["mcp__fast-context__fast_context_search"],
            "raw_summary": "found tls source",
        }

    results = await run_context_source_discovery(
        ContextDiscoveryRequest(
            request_id="req-1",
            repo_path=str(tmp_path),
            analysis_object_text="nvme-tcp-tls",
        ),
        providers=["fast-context"],
        fast_context_search=fake_search,
    )

    assert results[0].status == "ok"
    assert results[0].candidate_files[0].validated is True
    assert results[0].candidate_files[0].path == "nof/nvmf_tcp/transport/tls/tls.c"


def test_fast_context_merge_preserves_provider_source(tmp_path):
    from app.services.context_discovery import ContextCandidateFile, ContextDiscoveryResult
    from app.services.external_agent_discovery import merge_source_candidates

    src = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls"
    src.mkdir(parents=True)
    (src / "tls.c").write_text("int tls;\n", encoding="utf-8")

    merged, warnings = merge_source_candidates(
        tmp_path,
        [],
        [
            ContextDiscoveryResult(
                provider="fast-context",
                status="ok",
                candidate_files=[
                    ContextCandidateFile(
                        path="nof/nvmf_tcp/transport/tls/tls.c",
                        reason="semantic code search match",
                        confidence="high",
                        validated=True,
                    )
                ],
            )
        ],
    )

    assert warnings == []
    assert merged[0].source == "fast_context"
    assert merged[0].confidence == "high"


@pytest.mark.asyncio
async def test_workspace_resolver_merges_fast_context_candidates(tmp_path, monkeypatch):
    from app.schemas.workspace_analysis import AnalysisObject, LLMLimits
    from app.config import settings
    from app.services.context_discovery import ContextCandidateFile, ContextDiscoveryResult
    from app.services.workspace_scope_resolver import WorkspaceScopeResolver, _GraphIndex

    monkeypatch.setattr(settings, "fast_context_backend_bridge_enabled", True)
    src = tmp_path / "src" / "hidden_tls.c"
    src.parent.mkdir(parents=True)
    src.write_text("int hidden_tls(void) { return 0; }\n", encoding="utf-8")

    async def no_agent(_request, **_kwargs):
        return []

    async def fake_context(_request, **_kwargs):
        return [
            ContextDiscoveryResult(
                provider="fast-context",
                status="ok",
                candidate_files=[
                    ContextCandidateFile(
                        path="src/hidden_tls.c",
                        reason="semantic source match",
                        confidence="high",
                        validated=True,
                    )
                ],
            )
        ]

    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_external_agent_discovery",
        no_agent,
    )
    monkeypatch.setattr(
        "app.services.workspace_scope_resolver.run_context_source_discovery",
        fake_context,
    )

    resolved = await WorkspaceScopeResolver()._resolve_object(
        obj=AnalysisObject(id="obj-hidden", text="opaque target", kind="module"),
        ws_id="ws",
        repo_path=str(tmp_path),
        index=_GraphIndex(None),
        limits=LLMLimits(max_files_per_object=8),
        gitnexus_available=False,
    )

    assert resolved.candidate_files[0].source == "fast_context"
    assert resolved.candidate_files[0].path.replace("\\", "/").endswith("src/hidden_tls.c")

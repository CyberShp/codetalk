"""Optional context discovery providers that feed CodeTalk validation.

This module is deliberately small: providers such as fast-context can locate
candidate source files, but CodeTalk still validates every path locally before
the candidate enters scope resolution or evidence memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable, Literal

from app.config import settings
from app.services.external_agent_discovery import (
    AgentCandidateFile,
    AgentDiscoveryResult,
    AgentStatus,
    validate_agent_candidate_file,
)

ContextProvider = Literal["fast-context"]
FastContextSearch = Callable[["ContextDiscoveryRequest"], Any]


@dataclass
class ContextDiscoveryRequest:
    request_id: str
    repo_path: str
    analysis_object_text: str
    path_hints: list[str] = field(default_factory=list)
    scope_hints: list[dict] = field(default_factory=list)
    existing_candidates: list[dict] = field(default_factory=list)
    goal: str = "source_scope"


@dataclass
class ContextCandidateFile(AgentCandidateFile):
    pass


@dataclass
class ContextDiscoveryResult(AgentDiscoveryResult):
    pass


async def run_context_source_discovery(
    request: ContextDiscoveryRequest,
    *,
    providers: list[str] | None = None,
    fast_context_search: FastContextSearch | None = None,
) -> list[ContextDiscoveryResult]:
    if not getattr(settings, "context_discovery_enabled", True):
        return []
    selected = providers or ["fast-context"]
    results: list[ContextDiscoveryResult] = []
    for provider in selected:
        if provider == "fast-context":
            results.append(
                await _run_fast_context_provider(
                    request,
                    fast_context_search=fast_context_search,
                )
            )
            continue
        results.append(ContextDiscoveryResult(
            provider=provider,
            status="unavailable",
            warnings=[f"unknown context discovery provider: {provider}"],
        ))
    return results


async def _run_fast_context_provider(
    request: ContextDiscoveryRequest,
    *,
    fast_context_search: FastContextSearch | None,
) -> ContextDiscoveryResult:
    if not getattr(settings, "fast_context_enabled", True):
        return ContextDiscoveryResult(
            provider="fast-context",
            status="unavailable",
            warnings=["fast-context provider is disabled"],
        )
    if fast_context_search is None:
        return ContextDiscoveryResult(
            provider="fast-context",
            status="unavailable",
            warnings=["fast-context MCP bridge is not configured for the backend runtime"],
        )
    try:
        raw = fast_context_search(request)
        payload = await raw if isawaitable(raw) else raw
    except TimeoutError:
        return ContextDiscoveryResult(
            provider="fast-context",
            status="timeout",
            warnings=["fast-context search timed out"],
        )
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        return ContextDiscoveryResult(
            provider="fast-context",
            status="error",
            raw_summary=message,
            warnings=[message],
        )
    return _parse_fast_context_payload(request.repo_path, payload)


def _parse_fast_context_payload(
    repo_path: str | Path,
    payload: Any,
) -> ContextDiscoveryResult:
    if not isinstance(payload, dict):
        return ContextDiscoveryResult(
            provider="fast-context",
            status="invalid_output",
            warnings=["fast-context result must be an object"],
            raw_summary=str(payload)[:4000],
        )
    candidate_files: list[ContextCandidateFile] = []
    for item in payload.get("candidate_files") or payload.get("files") or []:
        if isinstance(item, str):
            raw_path = item
            reason = "fast-context source candidate"
            confidence = "medium"
            excerpt = ""
        elif isinstance(item, dict):
            raw_path = str(item.get("path") or item.get("file_path") or "").strip()
            reason = str(item.get("reason") or "fast-context source candidate")
            confidence = _normalize_confidence(item.get("confidence"))
            excerpt = str(item.get("evidence_excerpt") or item.get("excerpt") or "")
        else:
            continue
        if not raw_path:
            continue
        validation = validate_agent_candidate_file(repo_path, raw_path)
        candidate_files.append(ContextCandidateFile(
            path=validation.path or raw_path,
            reason=reason,
            confidence=confidence,
            evidence_excerpt=excerpt,
            validated=validation.validated,
            validation_error=validation.validation_error,
        ))
    status: AgentStatus = "ok" if candidate_files else "invalid_output"
    warnings = [
        f"rejected {item.path} ({item.validation_error})"
        for item in candidate_files
        if not item.validated and item.validation_error
    ]
    if not candidate_files:
        warnings.append("fast-context returned no candidate source files")
    return ContextDiscoveryResult(
        provider="fast-context",
        status=status,
        candidate_files=candidate_files,
        commands=[
            str(item)
            for item in payload.get("commands", [])
            if isinstance(item, str)
        ],
        raw_summary=str(payload.get("raw_summary") or payload.get("summary") or ""),
        warnings=warnings,
    )


def _normalize_confidence(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    return text if text in {"high", "medium", "low"} else "medium"

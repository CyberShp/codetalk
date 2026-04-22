"""Repository-level chat streaming endpoint."""

import logging
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.chat_session import ChatSession
from app.services.chat_payload import ChatMessage, build_deepwiki_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["repo-chat"])

# Regex to extract C-style identifiers from user messages (potential function names).
# Don't use \b — Python treats Chinese characters as \w, breaking word boundaries.
_IDENT_RE = re.compile(r"(?<![a-zA-Z0-9_])([a-zA-Z_][a-zA-Z0-9_]{2,})(?![a-zA-Z0-9_])")


async def _gitnexus_search_context(query: str, repo_name: str) -> str:
    """Search GitNexus for symbols related to the user's question.

    Iron Law: pure HTTP call to GitNexus /api/search. No analysis.
    Returns formatted string for injection into chat context, or "" on failure.

    B3 guard: skips silently when repo is not indexed by GitNexus (/api/repos).
    """
    if not repo_name:
        return ""

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=10
        ) as client:
            # B3: skip if repo not indexed by GitNexus (/api/repos is Phase 5 endpoint)
            repos_resp = await client.get("/api/repos", params={"repo": repo_name})
            if repos_resp.status_code != 200:
                return ""
            raw = repos_resp.json()
            # GitNexus returns a JSON array, not {"repos": [...]}.
            repos = raw if isinstance(raw, list) else raw.get("repos", [])
            names = {r if isinstance(r, str) else (r.get("name") or r.get("repo") or "") for r in repos}
            if repo_name not in names:
                return ""

            params: dict[str, str] = {"repo": repo_name}
            resp = await client.post(
                "/api/search",
                params=params,
                json={"query": query, "mode": "hybrid", "limit": 5, "enrich": True},
            )
            if resp.status_code != 200:
                return ""
            results = resp.json().get("results", [])
            if not results:
                return ""

            lines = ["[知识图谱相关符号]:"]
            for r in results[:5]:
                name = r.get("name", "")
                label = r.get("label", "")
                path = r.get("filePath", "")
                cluster = r.get("cluster", "")
                processes = r.get("processes", [])
                line = f"- {name} ({label}) @ {path}"
                if cluster:
                    line += f" [社区: {cluster}]"
                if processes:
                    line += f" [流程: {', '.join(str(p) for p in processes[:3])}]"
                lines.append(line)
            return "\n".join(lines)
    except Exception:
        return ""  # Non-fatal — chat works without graph context


async def _joern_code_context(query: str, repo_local_path: str) -> str:
    """Look up function source code via Joern CPG for identifiers in the query.

    Iron Law: pure HTTP call to Joern /query-sync. No analysis.
    Extracts C-style identifiers from query, searches Joern for matching methods,
    reads source code from repo files. Returns formatted code snippets or "".
    """
    # Extract potential function names from query
    identifiers = _IDENT_RE.findall(query)
    # Filter out common non-function words
    stop = {"this", "that", "what", "which", "from", "with", "the", "and", "for", "not",
            "how", "does", "has", "have", "its", "are", "was", "were", "been", "will",
            "can", "may", "should", "would", "could", "about", "into", "over", "after"}
    candidates = [w for w in identifiers if w.lower() not in stop and ("_" in w or len(w) > 4)]
    if not candidates:
        return ""

    snippets: list[str] = []
    try:
        # Query Joern for methods matching candidate names
        name_pat = "|".join(re.escape(c) for c in candidates[:5])
        cpgql = (
            f'cpg.method.name(".*({name_pat}).*").l.take(5).map(m => '
            'Map("name" -> m.name, "file" -> m.filename, '
            '"line" -> m.lineNumber.getOrElse(-1).toString, '
            '"lineEnd" -> m.lineNumberEnd.getOrElse(-1).toString)).toJson'
        )
        async with httpx.AsyncClient(
            base_url=settings.joern_base_url, timeout=10
        ) as client:
            resp = await client.post("/query-sync", json={"query": cpgql})
            if resp.status_code != 200:
                return ""
            data = resp.json()
            stdout = data.get("stdout", "")
            # Strip ANSI codes
            clean = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
            # Parse Scala REPL output
            import json as _json
            methods = []
            for line in clean.strip().split("\n"):
                eq = line.find("= ")
                if eq == -1:
                    continue
                val = line[eq + 2:].strip()
                if val.startswith('"'):
                    try:
                        inner = _json.loads(val)
                        if isinstance(inner, str):
                            methods = _json.loads(inner)
                    except Exception:
                        pass

        # Read source code from files for each method
        for m in methods[:3]:
            fname = m.get("file", "")
            start = int(m.get("line", -1))
            end = int(m.get("lineEnd", -1))
            if start < 0 or not fname:
                continue
            # Build full path
            fpath = os.path.join(repo_local_path, fname)
            if not os.path.isfile(fpath):
                continue
            # Read lines [start-2, end+2] for context
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                lo = max(0, start - 2)
                hi = min(len(all_lines), (end if end > start else start + 30) + 2)
                code_lines = all_lines[lo:hi]
                code = "".join(code_lines).rstrip()
                if len(code) > 1500:
                    code = code[:1500] + "\n// ... (truncated)"
                snippets.append(f"// {m['name']}() @ {fname}:{start}\n{code}")
            except Exception:
                continue

        if not snippets:
            return ""
        return "[源码片段]:\n```c\n" + "\n\n".join(snippets) + "\n```"
    except Exception:
        return ""  # Non-fatal


class RepoChatRequest(BaseModel):
    repo_id: uuid.UUID
    messages: list[ChatMessage]
    file_path: str | None = None
    deep_research: bool = False
    included_files: list[str] | None = None
    excluded_dirs: list[str] | None = None


class ChatSessionCreate(BaseModel):
    title: str | None = None
    messages: list[dict] = []


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None


@router.post("/{repo_id}/chat/stream")
async def repo_chat_stream(
    repo_id: uuid.UUID, body: RepoChatRequest, db: AsyncSession = Depends(get_db)
):
    """Stream chat response for repo, with full deepwiki params."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    # Inject tool context — non-blocking, each fails silently.
    # 1) GitNexus: graph relationships (symbols, clusters, connections)
    # 2) Joern CPG: actual source code of functions mentioned in the query
    # GitNexus indexes repos by the UUID directory name (e.g. "e08faf4b-..."),
    # NOT by the human-readable repo.name (e.g. "iscsi").
    messages = list(body.messages)
    if messages and messages[-1].role == "user":
        user_text = messages[-1].content
        extra_ctx_parts: list[str] = []

        gitnexus_ctx = await _gitnexus_search_context(user_text, str(repo.id))
        if gitnexus_ctx:
            extra_ctx_parts.append(gitnexus_ctx)

        joern_ctx = await _joern_code_context(user_text, repo.local_path)
        if joern_ctx:
            extra_ctx_parts.append(joern_ctx)

        if extra_ctx_parts:
            last = messages[-1]
            messages[-1] = ChatMessage(
                role=last.role,
                content=user_text + "\n\n" + "\n\n".join(extra_ctx_parts),
            )

    payload, trust_env = build_deepwiki_payload(
        repo,
        messages,
        llm_config,
        file_path=body.file_path,
        included_files=body.included_files,
        excluded_dirs=body.excluded_dirs,
        deep_research=body.deep_research,
    )

    await db.close()

    async def generate():
        try:
            async with httpx.AsyncClient(
                base_url=settings.deepwiki_base_url,
                timeout=httpx.Timeout(300, connect=10),
                trust_env=trust_env,
            ) as client:
                async with client.stream(
                    "POST",
                    "/chat/completions/stream",
                    json=payload,
                    timeout=300,
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_text():
                        yield chunk
        except httpx.ConnectError:
            yield "\n\n> ⚠️ 无法连接 deepwiki 服务。"
        except Exception as exc:
            yield f"\n\n> ⚠️ 请求失败: {exc}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Chat Session CRUD
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/chat/sessions")
async def list_chat_sessions(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Return all chat sessions for a repo, newest first."""
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.repo_id == repo_id)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "repo_id": str(s.repo_id),
            "title": s.title,
            "messages": [],
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in sessions
    ]


@router.get("/{repo_id}/chat/sessions/{session_id}")
async def get_chat_session(
    repo_id: uuid.UUID, session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Return a single chat session."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Session not found")
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.post("/{repo_id}/chat/sessions", status_code=201)
async def create_chat_session(
    repo_id: uuid.UUID, body: ChatSessionCreate, db: AsyncSession = Depends(get_db)
):
    """Create a new chat session. Auto-titles from first user message if title omitted."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    title = body.title
    if not title:
        first_user = next(
            (m for m in body.messages if m.get("role") == "user"), None
        )
        if first_user:
            raw = first_user.get("content", "")
            title = raw[:50] + ("…" if len(raw) > 50 else "")

    session = ChatSession(
        repo_id=repo_id,
        title=title,
        messages=body.messages,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.put("/{repo_id}/chat/sessions/{session_id}")
async def update_chat_session(
    repo_id: uuid.UUID,
    session_id: uuid.UUID,
    body: ChatSessionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update title and/or messages of a chat session."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Session not found")

    if body.title is not None:
        session.title = body.title
    if body.messages is not None:
        session.messages = body.messages
        flag_modified(session, "messages")

    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(session)
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.delete("/{repo_id}/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    repo_id: uuid.UUID, session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Delete a chat session."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Session not found")
    await db.delete(session)
    await db.commit()


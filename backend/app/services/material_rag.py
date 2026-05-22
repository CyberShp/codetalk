"""Material RAG: chunk, embed, and retrieve workspace materials.

Provides semantic search over workspace materials by:
1. Splitting material text into overlapping chunks
2. Embedding each chunk via the configured embedding model
3. Retrieving top-K relevant chunks for a given query via cosine similarity

Gracefully degrades to full-text loading when no embedding model is configured.
"""

import asyncio
import logging
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE_TOKENS = 500
_CHUNK_OVERLAP_TOKENS = 50
_TOP_K = 5
_MAX_MATERIAL_BYTES = 100_000


def _estimate_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def _read_material_file(file_path: str) -> str:
    p = Path(file_path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        if size <= _MAX_MATERIAL_BYTES:
            return p.read_text(encoding="utf-8", errors="replace")
        raw = p.read_bytes()[:_MAX_MATERIAL_BYTES]
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE_TOKENS,
    overlap: int = _CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Split text into overlapping chunks, splitting on markdown headers and paragraphs."""
    if not text.strip():
        return []

    sections: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.startswith("##") and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    chunks: list[str] = []
    for section in sections:
        tokens_est = _estimate_tokens(section)
        if tokens_est <= chunk_size:
            if section.strip():
                chunks.append(section.strip())
            continue

        paragraphs = section.split("\n\n")
        buf: list[str] = []
        buf_tokens = 0

        for para in paragraphs:
            para_tokens = _estimate_tokens(para)
            if buf_tokens + para_tokens > chunk_size and buf:
                chunks.append("\n\n".join(buf).strip())
                keep_tokens = 0
                keep: list[str] = []
                for p in reversed(buf):
                    t = _estimate_tokens(p)
                    if keep_tokens + t > overlap:
                        break
                    keep.insert(0, p)
                    keep_tokens += t
                buf = keep
                buf_tokens = keep_tokens

            buf.append(para)
            buf_tokens += para_tokens

        if buf:
            text_out = "\n\n".join(buf).strip()
            if text_out:
                chunks.append(text_out)

    return chunks


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _get_embedding_client():
    """Create an embedding client from the active embedding model config.

    Returns None if no embedding model is configured.
    """
    try:
        from app.llm.embedding_client import EmbeddingClient
        from app.llm.factory import _load_general_settings, _resolve_proxy

        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT value FROM settings WHERE key = 'active_embedding_model_id'"
            ) as cur:
                row = await cur.fetchone()

            if not row or not row["value"]:
                return None

            config_id = row["value"]
            async with db.execute(
                "SELECT * FROM llm_configs WHERE id = ?", (config_id,)
            ) as cur:
                cfg_row = await cur.fetchone()

            if not cfg_row:
                return None

            cfg = dict(cfg_row)
            general = await _load_general_settings(db)

        proxy_url, ssl_cert, force_direct = _resolve_proxy(general)
        return EmbeddingClient(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            proxy_url=proxy_url,
            ssl_cert_path=ssl_cert,
            force_direct=force_direct,
        )
    except Exception as exc:
        logger.warning("Failed to create embedding client: %s", exc)
        return None


async def embed_material(material_id: str, workspace_id: str) -> int:
    """Chunk and embed a single material. Returns number of chunks created."""
    client = await _get_embedding_client()
    if client is None:
        logger.info("No embedding model configured, skipping material embedding")
        return 0

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT file_path, filename FROM workspace_materials WHERE id = ?",
            (material_id,),
        ) as cur:
            mat = await cur.fetchone()

    if not mat:
        return 0

    text = await asyncio.to_thread(_read_material_file, mat["file_path"])
    if not text.strip():
        return 0

    chunks = chunk_text(text)
    if not chunks:
        return 0

    try:
        embeddings = await client.embed_batch(chunks)
    except Exception as exc:
        logger.error("Embedding failed for material %s: %s", material_id, exc)
        return 0

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "DELETE FROM material_chunks WHERE material_id = ?", (material_id,)
        )

        for i, (chunk_content, embedding) in enumerate(zip(chunks, embeddings)):
            await db.execute(
                "INSERT INTO material_chunks "
                "(id, material_id, workspace_id, chunk_index, content, embedding, token_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    material_id,
                    workspace_id,
                    i,
                    chunk_content,
                    _pack_embedding(embedding),
                    _estimate_tokens(chunk_content),
                    now,
                ),
            )

        await db.commit()

    logger.info(
        "Embedded material %s (%s): %d chunks",
        material_id, mat["filename"], len(chunks),
    )
    return len(chunks)


async def embed_workspace_materials(ws_id: str) -> int:
    """Embed all active materials in a workspace. Returns total chunks created."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM workspace_materials "
            "WHERE workspace_id = ? AND is_active = TRUE",
            (ws_id,),
        ) as cur:
            rows = await cur.fetchall()

    total = 0
    for row in rows:
        total += await embed_material(row["id"], ws_id)
    return total


async def delete_material_chunks(material_id: str) -> None:
    """Remove all chunks for a material (called on material delete)."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "DELETE FROM material_chunks WHERE material_id = ?", (material_id,)
        )
        await db.commit()


async def retrieve_chunks(
    ws_id: str,
    query: str,
    top_k: int = _TOP_K,
) -> list[dict]:
    """Retrieve top-K relevant material chunks for a query.

    Returns list of {content, filename, score} dicts, sorted by relevance.
    Returns empty list if no embedding model is configured or no chunks exist.
    """
    client = await _get_embedding_client()
    if client is None:
        return []

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT mc.content, mc.embedding, wm.filename "
            "FROM material_chunks mc "
            "JOIN workspace_materials wm ON mc.material_id = wm.id "
            "WHERE mc.workspace_id = ? AND wm.is_active = TRUE",
            (ws_id,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return []

    try:
        query_embedding = (await client.embed_batch([query]))[0]
    except Exception as exc:
        logger.warning("Query embedding failed: %s", exc)
        return []

    scored: list[tuple[float, str, str]] = []
    for row in rows:
        chunk_vec = _unpack_embedding(row["embedding"])
        score = _cosine_similarity(query_embedding, chunk_vec)
        scored.append((score, row["content"], row["filename"]))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {"content": content, "filename": filename, "score": round(score, 4)}
        for score, content, filename in scored[:top_k]
    ]

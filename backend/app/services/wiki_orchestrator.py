"""Wiki generation orchestrator — multi-page wiki via deepwiki API.

IRON LAW: No analysis logic. Only HTTP calls to deepwiki + format conversion.
All LLM calls go through deepwiki's /chat/completions/stream.
All RAG retrieval is done by deepwiki's FAISS retriever.
CodeTalks owns the prompt templates (wiki_prompts.py).
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.services.wiki_prompts import build_page_prompt, build_structure_prompt

logger = logging.getLogger(__name__)


@dataclass
class WikiPage:
    id: str
    title: str
    content: str = ""
    file_paths: list[str] = field(default_factory=list)
    importance: str = "medium"
    related_pages: list[str] = field(default_factory=list)


@dataclass
class WikiSection:
    id: str
    title: str
    pages: list[str] = field(default_factory=list)
    subsections: list[str] = field(default_factory=list)


@dataclass
class WikiStructure:
    title: str
    description: str
    pages: list[WikiPage] = field(default_factory=list)
    sections: list[WikiSection] = field(default_factory=list)
    root_sections: list[str] = field(default_factory=list)


@dataclass
class WikiResult:
    structure: WikiStructure
    generated_pages: dict[str, WikiPage]  # page_id -> WikiPage with content
    stale: bool = False


class WikiOrchestrator:
    """Orchestrates multi-page wiki generation via deepwiki API.

    Flow:
    1. Check deepwiki cache → GET /api/wiki_cache
    2. Fetch repo structure → GET /local_repo/structure
    3. Determine wiki structure → POST /chat/completions/stream (1 call)
    4. Generate pages → POST /chat/completions/stream (N calls, serial)
    5. Save to cache → POST /api/wiki_cache
    """

    def __init__(self, base_url: str = "http://deepwiki:8001"):
        self.base_url = base_url

    async def get_cached_wiki(
        self,
        owner: str,
        repo: str,
        repo_type: str = "local",
        language: str = "zh",
    ) -> dict | None:
        """Check deepwiki cache. Returns raw cache data or None."""
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=10)
        ) as client:
            resp = await client.get(
                "/api/wiki_cache",
                params={
                    "owner": owner,
                    "repo": repo,
                    "repo_type": repo_type,
                    "language": language,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and data.get("wiki_structure") and data.get("generated_pages"):
                    return data
            return None

    async def generate_wiki(
        self,
        repo_local_path: str,
        owner: str,
        repo: str,
        language: str = "zh",
        provider: str = "openai",
        model: str = "gpt-4o",
        comprehensive: bool = True,
        proxy_mode: str = "system",
        on_progress: object = None,
    ) -> WikiResult:
        """Full wiki generation: structure determination + page generation + cache save.

        Args:
            on_progress: Optional async callable(current, total, page_title) for progress updates.
        """
        trust_env = proxy_mode != "direct"

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(300, connect=10),
            trust_env=trust_env,
        ) as client:
            # Step 1: Fetch repo structure
            file_tree, readme = await self._fetch_repo_structure(
                client, repo_local_path
            )

            # Step 2: Determine wiki structure
            structure = await self._determine_structure(
                client,
                file_tree,
                readme,
                repo_local_path,
                language,
                provider,
                model,
                comprehensive,
            )

            # Step 3: Generate pages (serial, MAX_CONCURRENT=1)
            generated_pages: dict[str, WikiPage] = {}
            total = len(structure.pages)
            for i, page in enumerate(structure.pages):
                logger.info(
                    "Generating wiki page %d/%d: %s", i + 1, total, page.title
                )
                if on_progress:
                    await on_progress(i, total, page.title)

                try:
                    content = await self._generate_page(
                        client,
                        page,
                        repo_local_path,
                        language,
                        provider,
                        model,
                    )
                    page.content = content
                    generated_pages[page.id] = page
                except Exception as exc:
                    logger.error(
                        "Failed to generate page %s: %s", page.id, exc
                    )
                    page.content = f"> Wiki page generation failed: {exc}"
                    generated_pages[page.id] = page

            if on_progress:
                await on_progress(total, total, "done")

            # Step 4: Save to deepwiki cache
            await self._save_cache(
                client, owner, repo, "local", language, comprehensive,
                structure, generated_pages, provider, model,
            )

            return WikiResult(
                structure=structure, generated_pages=generated_pages
            )

    async def delete_cache(
        self,
        owner: str,
        repo: str,
        repo_type: str = "local",
        language: str = "zh",
    ) -> bool:
        """Delete deepwiki cache for a repo."""
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=10)
        ) as client:
            resp = await client.request(
                "DELETE",
                "/api/wiki_cache",
                params={
                    "owner": owner,
                    "repo": repo,
                    "repo_type": repo_type,
                    "language": language,
                },
            )
            return resp.status_code == 200

    # ── internal methods ──

    async def _fetch_repo_structure(
        self, client: httpx.AsyncClient, repo_local_path: str
    ) -> tuple[str, str]:
        """GET /local_repo/structure → (file_tree, readme)"""
        resp = await client.get(
            "/local_repo/structure", params={"path": repo_local_path}
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("file_tree", ""), body.get("readme", "")

    async def _determine_structure(
        self,
        client: httpx.AsyncClient,
        file_tree: str,
        readme: str,
        repo_local_path: str,
        language: str,
        provider: str,
        model: str,
        comprehensive: bool,
    ) -> WikiStructure:
        """Single LLM call to determine wiki page structure."""
        prompt = build_structure_prompt(
            file_tree=file_tree,
            readme=readme,
            language=language,
            comprehensive=comprehensive,
        )

        payload = {
            "repo_url": repo_local_path,
            "messages": [{"role": "user", "content": prompt}],
            "language": language,
            "provider": provider,
            "model": model,
        }

        raw = await self._stream_collect(client, payload)
        return self._parse_structure_xml(raw)

    async def _generate_page(
        self,
        client: httpx.AsyncClient,
        page: WikiPage,
        repo_local_path: str,
        language: str,
        provider: str,
        model: str,
    ) -> str:
        """Single LLM call to generate one wiki page."""
        prompt = build_page_prompt(
            page_title=page.title,
            file_paths=page.file_paths,
            language=language,
        )

        payload = {
            "repo_url": repo_local_path,
            "messages": [{"role": "user", "content": prompt}],
            "language": language,
            "provider": provider,
            "model": model,
        }
        if page.file_paths:
            payload["included_files"] = ",".join(page.file_paths)

        return await self._stream_collect(client, payload)

    async def _stream_collect(
        self, client: httpx.AsyncClient, payload: dict
    ) -> str:
        """POST /chat/completions/stream and collect full response."""
        content = ""
        async with client.stream(
            "POST", "/chat/completions/stream", json=payload, timeout=300
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                content += chunk
        return content

    async def _save_cache(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        repo_type: str,
        language: str,
        comprehensive: bool,
        structure: WikiStructure,
        pages: dict[str, WikiPage],
        provider: str,
        model: str,
    ) -> None:
        """POST /api/wiki_cache to persist generated wiki."""
        wiki_structure = {
            "id": f"wiki-{owner}-{repo}",
            "title": structure.title,
            "description": structure.description,
            "pages": [
                {
                    "id": p.id,
                    "title": p.title,
                    "content": p.content,
                    "filePaths": p.file_paths,
                    "importance": p.importance,
                    "relatedPages": p.related_pages,
                }
                for p in structure.pages
            ],
            "sections": [
                {
                    "id": s.id,
                    "title": s.title,
                    "pages": s.pages,
                    "subsections": s.subsections,
                }
                for s in structure.sections
            ],
            "rootSections": structure.root_sections,
        }

        generated_pages = {
            pid: {
                "id": p.id,
                "title": p.title,
                "content": p.content,
                "filePaths": p.file_paths,
                "importance": p.importance,
                "relatedPages": p.related_pages,
            }
            for pid, p in pages.items()
        }

        body = {
            "repo": {
                "owner": owner,
                "repo": repo,
                "type": repo_type,
            },
            "language": language,
            "comprehensive": comprehensive,
            "wiki_structure": wiki_structure,
            "generated_pages": generated_pages,
            "provider": provider,
            "model": model,
        }

        try:
            resp = await client.post("/api/wiki_cache", json=body)
            if resp.status_code == 200:
                logger.info("Wiki cache saved for %s/%s", owner, repo)
            else:
                logger.warning(
                    "Failed to save wiki cache: HTTP %s", resp.status_code
                )
        except Exception as exc:
            logger.warning("Failed to save wiki cache: %s", exc)

    @staticmethod
    def _parse_structure_xml(raw: str) -> WikiStructure:
        """Parse XML wiki structure from LLM response.

        Tries xml.etree.ElementTree first, falls back to regex.
        """
        # Extract XML block
        match = re.search(
            r"<wiki_structure>[\s\S]*?</wiki_structure>", raw
        )
        if not match:
            raise ValueError(
                "LLM response does not contain <wiki_structure> XML block"
            )

        xml_str = match.group(0)

        try:
            return WikiOrchestrator._parse_xml_etree(xml_str)
        except ET.ParseError as exc:
            logger.warning("XML parse failed (%s), trying regex fallback", exc)
            return WikiOrchestrator._parse_xml_regex(xml_str)

    @staticmethod
    def _parse_xml_etree(xml_str: str) -> WikiStructure:
        """Parse with ElementTree."""
        root = ET.fromstring(xml_str)

        title_el = root.find("title")
        desc_el = root.find("description")

        pages = []
        pages_el = root.find("pages")
        if pages_el is not None:
            for page_el in pages_el.findall("page"):
                page_id = page_el.get("id", "")
                p_title = page_el.findtext("title", "")
                importance = page_el.findtext("importance", "medium")
                file_paths = [
                    fp.text or ""
                    for fp in page_el.findall("relevant_files/file_path")
                    if fp.text
                ]
                related = [
                    r.text or ""
                    for r in page_el.findall("related_pages/related")
                    if r.text
                ]
                pages.append(
                    WikiPage(
                        id=page_id,
                        title=p_title,
                        file_paths=file_paths,
                        importance=importance,
                        related_pages=related,
                    )
                )

        sections = []
        root_sections = []
        sections_el = root.find("sections")
        if sections_el is not None:
            for sec_el in sections_el.findall("section"):
                sec_id = sec_el.get("id", "")
                sec_title = sec_el.findtext("title", "")
                sec_pages = [
                    pr.text or ""
                    for pr in sec_el.findall("pages/page_ref")
                    if pr.text
                ]
                subsections = [
                    sr.text or ""
                    for sr in sec_el.findall("subsections/section_ref")
                    if sr.text
                ]
                sections.append(
                    WikiSection(
                        id=sec_id,
                        title=sec_title,
                        pages=sec_pages,
                        subsections=subsections,
                    )
                )
                # Top-level sections (not referenced as subsections) are roots
                root_sections.append(sec_id)

            # Filter: only keep sections not referenced as subsections of others
            all_subsections = set()
            for s in sections:
                all_subsections.update(s.subsections)
            root_sections = [
                sid for sid in root_sections if sid not in all_subsections
            ]

        return WikiStructure(
            title=title_el.text if title_el is not None else "Wiki",
            description=desc_el.text if desc_el is not None else "",
            pages=pages,
            sections=sections,
            root_sections=root_sections,
        )

    @staticmethod
    def _parse_xml_regex(xml_str: str) -> WikiStructure:
        """Regex fallback for malformed XML."""
        title_match = re.search(r"<title>(.*?)</title>", xml_str)
        desc_match = re.search(r"<description>(.*?)</description>", xml_str)

        pages = []
        for m in re.finditer(
            r'<page\s+id="([^"]*)">(.*?)</page>', xml_str, re.DOTALL
        ):
            page_id = m.group(1)
            block = m.group(2)
            p_title = re.search(r"<title>(.*?)</title>", block)
            importance = re.search(r"<importance>(.*?)</importance>", block)
            file_paths = re.findall(r"<file_path>(.*?)</file_path>", block)
            related = re.findall(r"<related>(.*?)</related>", block)
            pages.append(
                WikiPage(
                    id=page_id,
                    title=p_title.group(1) if p_title else page_id,
                    file_paths=file_paths,
                    importance=(
                        importance.group(1) if importance else "medium"
                    ),
                    related_pages=related,
                )
            )

        return WikiStructure(
            title=title_match.group(1) if title_match else "Wiki",
            description=desc_match.group(1) if desc_match else "",
            pages=pages,
            sections=[],
            root_sections=[],
        )

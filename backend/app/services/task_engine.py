"""Task execution engine — orchestrates tool adapters for analysis tasks."""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest, UnifiedResult
from app.database import async_session
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.task import AnalysisTask, ToolRun
from app.services import source_manager

logger = logging.getLogger(__name__)

_running_tasks: dict[UUID, asyncio.Task] = {}


def register_task(task_id: UUID, handle: asyncio.Task) -> None:
    _running_tasks[task_id] = handle


async def cancel_task(task_id: UUID) -> bool:
    """Cancel a running task. Returns True if handle was found and cancelled."""
    handle = _running_tasks.pop(task_id, None)
    if handle and not handle.done():
        handle.cancel()
        return True
    return False


async def run_task(task_id: UUID) -> None:
    async with async_session() as db:
        task = await db.get(AnalysisTask, task_id)
        if not task:
            logger.error("Task %s not found", task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            repo = await db.get(Repository, task.repository_id)
            if not repo:
                raise RuntimeError(f"Repository {task.repository_id} not found")

            local_path = await source_manager.resolve_source(repo)
            if repo.local_path != local_path:
                repo.local_path = local_path
                await db.commit()

            options = await _build_options(task, db)
            if task.target_spec.get("mr_url"):
                options["mr_url"] = task.target_spec["mr_url"]
            options["repo_name"] = repo.name
            request = AnalysisRequest(
                repo_local_path=local_path,
                target_files=task.target_spec.get("files"),
                task_type=task.task_type,
                options=options,
            )

            adapters = []
            for tool_name in task.tools:
                try:
                    adapters.append(create_adapter(tool_name))
                except KeyError:
                    logger.warning("Unknown adapter: %s, skipping", tool_name)

            if not adapters:
                raise RuntimeError("No valid tool adapters for this task")

            tool_runs = await _create_tool_runs(db, task_id, adapters)

            results: list[UnifiedResult] = []
            completed = 0
            for adapter, run in zip(adapters, tool_runs):
                run.status = "running"
                run.started_at = datetime.now(timezone.utc)
                await db.commit()

                try:
                    await adapter.prepare(request)
                    result = await adapter.analyze(request)
                    run.status = "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.result = {
                        **result.data,
                        "diagrams": result.diagrams,
                        "metadata": result.metadata,
                    }
                    results.append(result)
                except Exception as exc:
                    logger.error("Adapter %s failed: %s", adapter.name(), exc)
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.error = str(exc)

                completed += 1
                task.progress = int(completed / len(adapters) * 100)
                await db.commit()

            task.completed_at = datetime.now(timezone.utc)
            task.progress = 100

            if not results:
                failed_errors = [r.error for r in tool_runs if r.error]
                task.status = "failed"
                task.error = "; ".join(failed_errors) or "All tool runs failed"
            else:
                task.status = "completed"
                if task.ai_enabled:
                    summary = await _build_summary(results, options)
                    task.ai_summary = summary

            await db.commit()

        except asyncio.CancelledError:
            logger.info("Task %s cancelled", task_id)
            task.status = "cancelled"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as exc:
            logger.exception("Task %s failed", task_id)
            task.status = "failed"
            task.error = str(exc)
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()
        finally:
            _running_tasks.pop(task_id, None)


async def _create_tool_runs(
    db: AsyncSession, task_id: UUID, adapters: list
) -> list[ToolRun]:
    runs = []
    for adapter in adapters:
        run = ToolRun(task_id=task_id, tool_name=adapter.name())
        db.add(run)
        runs.append(run)
    await db.commit()
    for run in runs:
        await db.refresh(run)
    return runs


async def _build_options(task: AnalysisTask, db: AsyncSession) -> dict:
    options = dict(task.target_spec.get("options", {}))
    options.setdefault("language", "zh")
    if task.ai_enabled:
        result = await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
        llm_config = result.scalar_one_or_none()
        if not llm_config:
            result = await db.execute(
                select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
            )
            llm_config = result.scalar_one_or_none()
            if llm_config:
                logger.warning("No default LLM config; falling back to %s", llm_config.model_name)
        if llm_config:
            provider = llm_config.provider
            if provider == "custom":
                provider = "openai"
            options.setdefault("provider", provider)
            options.setdefault("model", llm_config.model_name)
            if llm_config.base_url:
                options.setdefault("llm_base_url", llm_config.base_url)
            if llm_config.api_key_encrypted:
                from app.utils.crypto import decrypt_key
                options.setdefault("llm_api_key", decrypt_key(llm_config.api_key_encrypted))
            options.setdefault("proxy_mode", llm_config.proxy_mode)
            logger.info(
                "LLM config: provider=%s model=%s base_url=%s proxy=%s has_key=%s",
                provider, llm_config.model_name, llm_config.base_url or "(default)",
                llm_config.proxy_mode, bool(llm_config.api_key_encrypted),
            )
    return options


async def _build_summary(results: list[UnifiedResult], options: dict) -> str:
    """Build AI summary via configured LLM endpoint, fallback to plaintext."""
    plaintext = _plaintext_summary(results)

    base_url = options.get("llm_base_url")
    api_key = options.get("llm_api_key")
    model = options.get("model")
    if not (base_url and api_key and model):
        logger.info("No LLM endpoint configured, using plaintext summary")
        return plaintext

    proxy_mode = options.get("proxy_mode", "system")
    trust_env = proxy_mode != "direct"

    language = options.get("language", "")
    if language == "zh":
        prompt = (
            "请用中文简要总结以下代码分析结果。"
            "重点说明关键架构模式、重要组件和值得注意的发现。\n\n" + plaintext
        )
    else:
        prompt = (
            "Summarize the following code analysis results concisely. "
            "Highlight key architectural patterns, important components, "
            "and notable findings.\n\n" + plaintext
        )

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(60, connect=10),
            trust_env=trust_env,
        ) as client:
            resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("AI summary generated via %s/%s", base_url, model)
            return content
    except Exception as exc:
        logger.warning("LLM summary failed (%s), falling back to plaintext", exc)
        return plaintext


def _plaintext_summary(results: list[UnifiedResult]) -> str:
    parts = []
    for r in results:
        if r.tool_name == "zoekt":
            query = r.data.get("query", "")
            if r.metadata.get("skipped") == "no_query":
                parts.append(
                    f"zoekt: 代码指纹索引已建立（仓库 {r.metadata.get('display_name', '')}），"
                    "本次任务未下达检索指令"
                )
            else:
                hits = r.data.get("search_results", [])
                total_matches = sum(len(f.get("matches", [])) for f in hits)
                snippet_lines = [
                    f"  {f['file']}:{m['line_number']}: {m['line_content'].strip()}"
                    for f in hits[:5]
                    for m in f.get("matches", [])[:3]
                ]
                snippets_text = "\n".join(snippet_lines)
                # Guard against extremely long snippets blowing up the prompt
                if len(snippets_text) > 2000:
                    snippets_text = snippets_text[:2000] + "\n  ...(truncated)"
                parts.append(
                    f"zoekt: searched '{query}', found {len(hits)} files "
                    f"({total_matches} total matches).\nTop code hits:\n{snippets_text}"
                )
        else:
            doc = r.data.get("documentation", "")
            n_diagrams = len(r.diagrams)
            preview = doc[:200] + "..." if len(doc) > 200 else doc
            parts.append(
                f"{r.tool_name}: generated documentation ({len(doc)} chars, "
                f"{n_diagrams} diagrams). Preview: {preview}"
            )
    return " | ".join(parts)

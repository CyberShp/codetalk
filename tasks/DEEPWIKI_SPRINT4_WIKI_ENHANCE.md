# Sprint 4: Wiki 增强 — 动态模型选择 + 增量更新

> **前置依赖**: Sprint 1 (上下文联动) ✅, Sprint 1.5 (InsightAsk Deep Research) ✅, Sprint 2 (Deep Research UI) ✅, Sprint 3 (WebSocket) ✅
> **执行者**: Sonnet (编码) → GPT52 (审查)
> **预估改动**: 0 新文件, 4 修改文件

---

## 4.1 动态模型选择

### 架构决策

| # | 决策 | 理由 |
|---|------|------|
| AD-1 | 后端代理 deepwiki `/models/config` | 前端不直接调 deepwiki；保持统一入口 |
| AD-2 | 端点挂在 `/api/settings/deepwiki/models` | 与现有 LLM 配置端点同族 |
| AD-3 | 仅展示，不做本地持久化 | 模型列表来自 deepwiki 运行时，无需入库 |

### Step 1: 后端 — deepwiki 模型代理端点

**修改文件**: `backend/app/api/settings.py`

在文件末尾新增端点：

```python
@router.get("/deepwiki/models")
async def get_deepwiki_models():
    """Proxy deepwiki /models/config — returns available model providers."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.deepwiki_base_url,
            timeout=httpx.Timeout(15, connect=5),
        ) as client:
            resp = await client.get("/models/config")
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(502, "Cannot connect to deepwiki service")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"deepwiki error: HTTP {exc.response.status_code}")
```

**实现要点**:
1. 需要 `from app.config import settings` — 该 import 已存在于 `settings.py` 吗？不在。需要新增
2. deepwiki 的 `/models/config` 返回格式直接透传，不做转换
3. 超时设短（15s），连接错误返回 502

### Step 2: 前端 — API 客户端方法

**修改文件**: `frontend/src/lib/api.ts`

在 `settings` 对象内（`deleteLLM` 之后，约 line 199）新增：

```typescript
deepwikiModels: () =>
  request<Record<string, unknown>>(`/api/settings/deepwiki/models`),
```

### Step 3: 前端 — 设置页展示 deepwiki 可用模型

**修改文件**: `frontend/src/app/(app)/settings/page.tsx`

在现有 LLM 配置列表区域**下方**新增一个 "DeepWiki 可用模型" 信息面板：

1. 组件挂载时调 `api.settings.deepwikiModels()`，存入 state
2. 展示 deepwiki 返回的 provider → model 列表（只读信息卡片）
3. 如果调用失败（502 等），显示 "无法连接 deepwiki 服务" 提示
4. 不需要交互操作（无编辑/删除/创建），纯展示
5. 设计：GlassPanel 包裹，标题 "DeepWiki 可用模型"，每个 provider 一行，展开显示其 model 列表

---

## 4.3 增量 Wiki 更新（单页重新生成）

### 架构决策

| # | 决策 | 理由 |
|---|------|------|
| AD-4 | 复用 `WikiOrchestrator._generate_page()` | 已有单页生成逻辑，无需重写 |
| AD-5 | 新增 `regenerate_page()` 公开方法 | `_generate_page` 是 private；封装为公开方法处理 cache 更新 |
| AD-6 | 通过 deepwiki `POST /api/wiki_cache` 整体回写 | deepwiki 没有 patch-single-page API，只能先 GET 整个 cache → 替换单页 → POST 回写 |
| AD-7 | 同时支持 task-scoped 和 repo-centric 路径 | 两套 wiki 端点共享 orchestrator |

### Step 4: 后端 — WikiOrchestrator 增加 `regenerate_page` 方法

**修改文件**: `backend/app/services/wiki_orchestrator.py`

新增公开方法：

```python
async def regenerate_page(
    self,
    owner: str,
    repo: str,
    repo_local_path: str,
    page_id: str,
    page_title: str,
    file_paths: list[str],
    language: str = "zh",
    provider: str = "openai",
    model: str = "gpt-4o",
    proxy_mode: str = "system",
) -> str:
    """Regenerate a single wiki page and update cache.

    Flow:
    1. GET existing cache from deepwiki
    2. Regenerate the target page via /chat/completions/stream
    3. Replace the page in cache
    4. POST updated cache back to deepwiki
    5. Return new page content
    """
    trust_env = proxy_mode != "direct"
    tool_repo_path = to_tool_repo_path(
        repo_local_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )

    async with httpx.AsyncClient(
        base_url=self.base_url,
        timeout=httpx.Timeout(300, connect=10),
        trust_env=trust_env,
    ) as client:
        # 1. Generate new content for the single page
        page = WikiPage(
            id=page_id,
            title=page_title,
            file_paths=file_paths,
        )
        new_content = await self._generate_page(
            client, page, tool_repo_path, language, provider, model
        )

        # 2. GET existing cache
        existing_cache = await self.get_cached_wiki(
            owner=owner, repo=repo, language=language
        )
        if not existing_cache:
            raise ValueError("No existing wiki cache to update")

        # 3. Update the page in generated_pages
        gen_pages = existing_cache.get("generated_pages", {})
        if page_id not in gen_pages:
            raise ValueError(f"Page {page_id} not found in wiki cache")

        gen_pages[page_id]["content"] = new_content

        # 4. Also update in wiki_structure.pages
        ws_pages = existing_cache.get("wiki_structure", {}).get("pages", [])
        for p in ws_pages:
            if p.get("id") == page_id:
                p["content"] = new_content
                break

        # 5. POST updated cache back to deepwiki
        body = {
            "repo": {"owner": owner, "repo": repo, "type": "local"},
            "language": language,
            "comprehensive": True,
            "wiki_structure": existing_cache["wiki_structure"],
            "generated_pages": gen_pages,
            "provider": provider,
            "model": model,
        }
        try:
            resp = await client.post("/api/wiki_cache", json=body)
            if resp.status_code != 200:
                logger.warning("Failed to save updated wiki cache: HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Failed to save updated wiki cache: %s", exc)

        return new_content
```

### Step 5: 后端 — repo_wiki.py 增加单页重新生成端点

**修改文件**: `backend/app/api/repo_wiki.py`

新增端点（在 `export_repo_wiki` 之前）：

```python
class WikiRegeneratePageRequest(BaseModel):
    page_id: str
    page_title: str
    file_paths: list[str] = []


@router.post("/{repo_id}/wiki/regenerate-page")
async def regenerate_wiki_page(
    repo_id: uuid.UUID,
    body: WikiRegeneratePageRequest,
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a single wiki page without rebuilding the entire wiki."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    owner, repo_name = _cache_owner_repo(repo)
    llm_opts = await _get_llm_options(db)
    provider = llm_opts.get("provider", "openai")
    model = llm_opts.get("model", "gpt-4o")
    proxy_mode = llm_opts.get("proxy_mode", "system")

    try:
        new_content = await _orchestrator.regenerate_page(
            owner=owner,
            repo=repo_name,
            repo_local_path=repo.local_path,
            page_id=body.page_id,
            page_title=body.page_title,
            file_paths=body.file_paths,
            language="zh",
            provider=provider,
            model=model,
            proxy_mode=proxy_mode,
        )
        return {"status": "ok", "content": new_content}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.exception("Failed to regenerate wiki page %s", body.page_id)
        raise HTTPException(502, f"Page regeneration failed: {exc}")
```

**需要新增 import**: `from pydantic import BaseModel`（如未引入）

### Step 6: 前端 — API 客户端方法

**修改文件**: `frontend/src/lib/api.ts`

在 `api.repos.wiki` 对象内（`deleteCache` 之后，约 line 113）新增：

```typescript
regeneratePage: (repoId: string, pageId: string, pageTitle: string, filePaths: string[]) =>
  request<{ status: string; content: string }>(`/api/repos/${repoId}/wiki/regenerate-page`, {
    method: "POST",
    body: JSON.stringify({ page_id: pageId, page_title: pageTitle, file_paths: filePaths }),
  }),
```

### Step 7: 前端 — WikiViewer 增加单页 "重新生成" 按钮

**修改文件**: `frontend/src/components/ui/WikiViewer.tsx`

**注意**: WikiViewer 当前是 task-scoped（接受 `taskId` prop）。增量更新走 repo-centric API。需要做以下适配：

1. Props 增加可选 `repoId?: string`
2. 在当前页标题栏旁增加一个 "重新生成此页" 按钮（`RefreshCw` icon，已 imported）
3. 点击后调 `api.repos.wiki.regeneratePage(repoId, pageId, pageTitle, filePaths)`
4. 加载中显示 spinner，完成后局部替换当前页 content（不刷新整个 wiki）
5. 如果 `repoId` 未传入，该按钮不渲染（task-scoped 场景暂不支持，因为 task-scoped 端点没有 regenerate-page）
6. 错误处理：失败时 toast 或内联 error message

**状态新增**:
```typescript
const [regenerating, setRegenerating] = useState(false);
```

---

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/api/settings.py` | 新增 `GET /api/settings/deepwiki/models` 代理端点 |
| 修改 | `backend/app/services/wiki_orchestrator.py` | 新增 `regenerate_page()` 公开方法 |
| 修改 | `backend/app/api/repo_wiki.py` | 新增 `POST /{repo_id}/wiki/regenerate-page` 端点 |
| 修改 | `frontend/src/lib/api.ts` | 新增 `deepwikiModels()` + `regeneratePage()` 方法 |
| 修改 | `frontend/src/app/(app)/settings/page.tsx` | 新增 deepwiki 可用模型展示面板 |
| 修改 | `frontend/src/components/ui/WikiViewer.tsx` | 新增单页 "重新生成" 按钮 |

---

## 验收标准

- [ ] `GET /api/settings/deepwiki/models` 返回 deepwiki 的模型配置
- [ ] deepwiki 不可达时返回 502 而非 500/挂起
- [ ] 设置页展示 deepwiki 可用模型列表（只读）
- [ ] `POST /api/repos/{repo_id}/wiki/regenerate-page` 可重新生成单页并更新 cache
- [ ] WikiViewer 中有 "重新生成此页" 按钮，点击后单页内容更新
- [ ] 重新生成期间有 loading 状态，完成后内容就地替换
- [ ] page_id 不存在时返回 400 错误
- [ ] 原有 wiki 端点（get/generate/status/delete/export）均不受影响
- [ ] 无 lint 错误
- [ ] settings.py 新增的 import（`from app.config import settings`）不与已有 import 冲突

---

## 注意事项

1. `settings.py` 当前**没有** `from app.config import settings` 这个 import，需要新增。注意命名不与 router 变量冲突
2. `regenerate_page` 的 cache 回写是 GET→modify→POST 全量回写模式，因为 deepwiki 没有 patch API
3. WikiViewer 的 `repoId` prop 是可选的。现有 task-scoped 调用方不需要改动
4. deepwiki `/models/config` 的返回格式不固定（取决于 deepwiki 版本），前端应做好容错
5. 单页重新生成不更新 `WikiCacheMeta.generated_at`（因为不是全量重建）

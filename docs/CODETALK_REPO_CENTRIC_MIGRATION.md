# CodeTalk 架构改造方案：任务中心 → 仓库中心

## 改造目标

将 CodeTalk 从"创建任务 → 查看任务详情"的工作流，改造为"进入仓库 → 打开 Wiki / Graph / Chat"的工作流。任务退化为仓库下的历史记录，不再作为用户主入口。Wiki 和 Graph 各自在独立新窗口中全屏运行，最大化内容面积。

---

## 第一部分：后端 API 改造

### 1.1 新增仓库维度的 Wiki 端点

当前所有 wiki 端点都以 `task_id` 为入口（`/api/tasks/{task_id}/wiki`），需要新增以 `repo_id` 为入口的平行端点。

新建文件 `backend/app/api/repo_wiki.py`：

```python
"""Repository-level wiki endpoints.

Same logic as task-scoped wiki.py, but keyed by repo_id directly.
No task lookup needed.
"""

router = APIRouter(prefix="/api/repos", tags=["repo-wiki"])

@router.get("/{repo_id}/wiki")
async def get_repo_wiki(repo_id: uuid.UUID, db=Depends(get_db)):
    """Get wiki for a repository directly."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    owner, repo_name = _cache_owner_repo(repo)
    cached = await _orchestrator.get_cached_wiki(owner=owner, repo=repo_name, language="zh")

    if not cached:
        return {"status": "not_generated", "wiki": None, "stale": False}

    # Staleness check
    result = await db.execute(
        select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo.id)
    )
    meta = result.scalar_one_or_none()
    stale = _check_staleness(meta, repo) if meta else True

    return {"status": "ready", "wiki": cached, "stale": stale}


@router.post("/{repo_id}/wiki/generate")
async def generate_repo_wiki(repo_id: uuid.UUID, body: WikiGenerateRequest, db=Depends(get_db)):
    """Trigger wiki generation for repo directly."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")
    # ... same _run() logic as current wiki.py, but without task lookup


@router.get("/{repo_id}/wiki/status")
async def repo_wiki_status(repo_id: uuid.UUID):
    """Wiki generation progress, keyed by repo_id."""
    status = _generation_status.get(str(repo_id))
    if not status:
        return {"running": False, "current": 0, "total": 0, "page_title": "", "error": None}
    return status


@router.delete("/{repo_id}/wiki/cache")
async def delete_repo_wiki_cache(repo_id: uuid.UUID, db=Depends(get_db)):
    """Delete wiki cache for repo."""
    # ...


@router.post("/{repo_id}/wiki/export")
async def export_repo_wiki(repo_id: uuid.UUID, body: WikiExportRequest, db=Depends(get_db)):
    """Export wiki for repo."""
    # ...
```

注意：旧的 `tasks/{task_id}/wiki` 端点**保留不删**，确保兼容。新端点和旧端点共享 `_orchestrator` 和 `_generation_status`，因为 wiki 缓存本身就是 repo 级别的。

### 1.2 新增仓库维度的 Chat 端点

新建文件 `backend/app/api/repo_chat.py`：

```python
"""Repository-level chat streaming endpoint."""

router = APIRouter(prefix="/api/repos", tags=["repo-chat"])

class RepoChatRequest(BaseModel):
    repo_id: uuid.UUID
    messages: list[ChatMessage]
    file_path: str | None = None
    deep_research: bool = False
    included_files: list[str] | None = None

@router.post("/{repo_id}/chat/stream")
async def repo_chat_stream(repo_id: uuid.UUID, body: RepoChatRequest, db=Depends(get_db)):
    """Stream chat response for repo, with full deepwiki params."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    repo_path = repo.local_path

    # Build full payload for deepwiki
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()

    payload: dict = {
        "repo_url": repo_path,
        "type": "local",
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "language": "zh",
    }

    # File context
    if body.file_path:
        payload["filePath"] = body.file_path
    if body.included_files:
        payload["included_files"] = "\n".join(body.included_files)

    # Deep research tag injection
    if body.deep_research and payload["messages"]:
        last = payload["messages"][-1]
        if last["role"] == "user":
            last["content"] = f"[DEEP RESEARCH] {last['content']}"

    # LLM config
    if llm_config:
        provider = llm_config.provider
        if provider == "custom":
            provider = "openai"
        payload["provider"] = provider
        payload["model"] = llm_config.model_name

    proxy_mode = llm_config.proxy_mode if llm_config else "system"
    trust_env = proxy_mode != "direct"

    await db.close()

    async def generate():
        try:
            async with httpx.AsyncClient(
                base_url="http://deepwiki:8001",
                timeout=httpx.Timeout(300, connect=10),
                trust_env=trust_env,
            ) as client:
                async with client.stream(
                    "POST", "/chat/completions/stream",
                    json=payload, timeout=300,
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_text():
                        yield chunk
        except httpx.ConnectError:
            yield "\n\n> ⚠️ 无法连接 deepwiki 服务。"
        except Exception as exc:
            yield f"\n\n> ⚠️ 请求失败: {exc}"

    return StreamingResponse(generate(), media_type="text/plain",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

这个端点解决了之前 chat.py 的所有问题：传了 `type: "local"`、支持 `filePath`、支持 `included_files`、支持 `deep_research`。

### 1.3 新增仓库维度的 Graph 端点

新建文件 `backend/app/api/repo_graph.py`：

```python
"""Repository-level graph data endpoint."""

router = APIRouter(prefix="/api/repos", tags=["repo-graph"])

@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: uuid.UUID, db=Depends(get_db)):
    """Get latest graph data for repo.

    Looks up the most recent completed task for this repo and returns
    its gitnexus tool_run result.
    """
    # Find latest completed task with gitnexus data
    result = await db.execute(
        select(AnalysisTask)
        .where(AnalysisTask.repository_id == repo_id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.completed_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        return {"status": "not_analyzed", "graph": None}

    # Load tool runs
    await db.refresh(task, ["tool_runs"])
    gitnexus_run = next(
        (r for r in task.tool_runs if r.tool_name == "gitnexus" and r.status == "completed"),
        None
    )
    if not gitnexus_run or not gitnexus_run.result:
        return {"status": "not_analyzed", "graph": None}

    return {
        "status": "ready",
        "graph": gitnexus_run.result.get("graph"),
        "metadata": gitnexus_run.result.get("metadata"),
        "analyzed_at": task.completed_at.isoformat() if task.completed_at else None,
    }
```

### 1.4 新增仓库详情端点

在 `backend/app/api/repos.py` 中新增：

```python
@router.get("/{repo_id}")
async def get_repo_detail(repo_id: uuid.UUID, db=Depends(get_db)):
    """Get repo with summary status for wiki and graph."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    # Wiki status
    wiki_meta = await db.execute(
        select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo.id)
    )
    wiki_meta = wiki_meta.scalar_one_or_none()

    # Graph status (from latest task)
    latest_task = await db.execute(
        select(AnalysisTask)
        .where(AnalysisTask.repository_id == repo.id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.completed_at.desc())
        .limit(1)
    )
    latest_task = latest_task.scalar_one_or_none()
    graph_ready = False
    graph_stats = None
    if latest_task:
        await db.refresh(latest_task, ["tool_runs"])
        gn_run = next(
            (r for r in latest_task.tool_runs if r.tool_name == "gitnexus" and r.result),
            None
        )
        if gn_run:
            graph_ready = True
            meta = gn_run.result.get("metadata", {})
            graph_stats = {
                "node_count": meta.get("node_count", 0),
                "edge_count": meta.get("edge_count", 0),
                "process_count": meta.get("process_count", 0),
                "community_count": meta.get("community_count", 0),
            }

    return {
        "repo": {
            "id": str(repo.id),
            "name": repo.name,
            "source_type": repo.source_type,
            "source_uri": repo.source_uri,
            "local_path": repo.local_path,
            "branch": repo.branch,
            "last_indexed_at": repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
        },
        "wiki": {
            "status": "ready" if wiki_meta else "not_generated",
            "generated_at": wiki_meta.generated_at.isoformat() if wiki_meta else None,
            "page_count": None,  # filled from deepwiki cache if needed
            "stale": _check_staleness(wiki_meta, repo) if wiki_meta else False,
        },
        "graph": {
            "status": "ready" if graph_ready else "not_analyzed",
            "analyzed_at": latest_task.completed_at.isoformat() if latest_task and latest_task.completed_at else None,
            "stats": graph_stats,
        },
    }
```

### 1.5 仓库维度的分析历史（带分页）

在 `backend/app/api/repos.py` 中新增：

```python
@router.get("/{repo_id}/analyses")
async def list_repo_analyses(
    repo_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db=Depends(get_db),
):
    """List analysis tasks for a repo, paginated, newest first."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    # Count
    count_q = select(func.count()).select_from(AnalysisTask).where(
        AnalysisTask.repository_id == repo_id
    )
    total = (await db.execute(count_q)).scalar()

    # Page
    q = (
        select(AnalysisTask)
        .where(AnalysisTask.repository_id == repo_id)
        .order_by(AnalysisTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(q)
    tasks = result.scalars().all()

    return {
        "items": [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "status": t.status,
                "error": t.error,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "created_at": t.created_at.isoformat(),
            }
            for t in tasks
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }
```

### 1.6 路由注册

在 `backend/app/api/router.py` 中新增：

```python
from app.api.repo_wiki import router as repo_wiki_router
from app.api.repo_chat import router as repo_chat_router
from app.api.repo_graph import router as repo_graph_router

api_router.include_router(repo_wiki_router)
api_router.include_router(repo_chat_router)
api_router.include_router(repo_graph_router)
```

### 1.7 新增仓库维度的分析触发

当前分析任务通过 `POST /api/tasks` 创建，需要 `repository_id`。这个端点保持不变，前端改为从仓库页发起调用即可。

---

## 第二部分：前端页面结构改造

### 2.1 新增页面路由

```
现有（保留兼容）:
  /dashboard          仪表盘
  /tasks              任务列表
  /tasks/[id]         任务详情（保留，不删）
  /assets             资产/仓库管理
  /settings           设置
  /tools              工具

新增:
  /repos/[id]         仓库主页（仓库中心）
  /repos/[id]/wiki    Wiki 全屏应用（新窗口）
  /repos/[id]/chat    Chat 结果页（新窗口，从 wiki 跳转）
  /repos/[id]/graph   Graph 全屏应用（新窗口）
```

### 2.2 Layout 策略

三种 layout 模式：

**主 layout**（`/dashboard`、`/tasks`、`/assets`、`/settings`、`/repos/[id]`）：
保持现有的左侧 Sidebar + 顶部 TopBar，内容区 `ml-64 mt-14 p-6`。

**全屏 layout**（`/repos/[id]/wiki`、`/repos/[id]/graph`、`/repos/[id]/chat`）：
无 Sidebar、无 TopBar。仅有一个 42px 高的窄 topbar（左侧 CODETALKS logo 可点击回到 dashboard，斜杠后是仓库名）。内容占满整个视口。

实现方式：Next.js 的 route group 或条件 layout：

```
app/
├── (main)/                    # 带 sidebar 的 layout
│   ├── layout.tsx             # Sidebar + TopBar
│   ├── dashboard/page.tsx
│   ├── tasks/page.tsx
│   ├── tasks/[id]/page.tsx
│   ├── assets/page.tsx
│   ├── settings/page.tsx
│   └── repos/[id]/page.tsx    # 仓库主页
│
├── (fullscreen)/              # 无 sidebar 的 layout
│   ├── layout.tsx             # 只有窄 topbar
│   ├── repos/[id]/wiki/page.tsx
│   ├── repos/[id]/graph/page.tsx
│   └── repos/[id]/chat/page.tsx
│
└── layout.tsx                 # 根 layout（body class等）
```

### 2.3 全屏 layout

新建 `app/(fullscreen)/layout.tsx`：

```tsx
export default function FullscreenLayout({ children }: { children: React.ReactNode }) {
  return (
    <body className="min-h-full bg-surface text-on-surface font-ui">
      {children}
    </body>
  );
}
```

每个全屏页面自带 topbar：

```tsx
function FullscreenTopbar({ repoName, backHref, backLabel }: {
  repoName: string;
  backHref: string;
  backLabel: string;
}) {
  return (
    <header className="h-[42px] flex items-center px-4 gap-3 border-b border-outline-variant bg-surface-container/60">
      <Link href={backHref} className="flex items-center gap-2 text-xs text-primary hover:bg-primary/5 px-2 py-1 rounded">
        <span>←</span>
        <span>{backLabel}</span>
      </Link>
      <span className="text-on-surface-variant/30">/</span>
      <span className="text-xs text-on-surface-variant">{repoName}</span>
    </header>
  );
}
```

---

## 第三部分：仓库主页（`/repos/[id]`）

这是改造后的核心入口页面，在主 layout 中渲染（有 Sidebar）。

### 3.1 页面结构

```
┌─────────────────────────────────────────┐
│ api-server                  [New analysis]│
│ /data/repos/api-server · synced 2h ago    │
├────────────┬────────────────────────────┤
│ Wiki Docs  │  Code Graph               │
│ card       │  card                      │
│ (click →   │  (click →                  │
│  new window)│  new window)               │
├────────────┴────────────────────────────┤
│ Recent analyses                          │
│ ┌──────────────────────────────────────┐│
│ │ Full repo  ● completed     2h ago    ││
│ │ MR #142    ● failed        3d ago    ││
│ │ Full repo  ● completed     7d ago    ││
│ └──────────────────────────────────────┘│
│              [1] [2] [3]                │
└─────────────────────────────────────────┘
```

### 3.2 入口卡片逻辑

Wiki 卡片状态：
- `not_generated`：显示"尚未生成"，卡片可点击但进入后提示生成
- `ready`：显示"N pages generated"，绿色标记
- `stale`：显示"content may be outdated"，黄色标记
- `generating`：显示进度条和当前页

Graph 卡片状态：
- `not_analyzed`：显示"需先执行分析"，灰色
- `ready`：显示"N nodes · M edges"，绿色

点击卡片行为：`window.open(\`/repos/${repoId}/wiki\`, '_blank')` 或 `window.open(\`/repos/${repoId}/graph\`, '_blank')`

### 3.3 New Analysis 按钮

复用现有 `NewAnalysisModal` 的逻辑，但预填 `repository_id`：

```tsx
<NewAnalysisModal
  repositoryId={repoId}
  onClose={() => setShowModal(false)}
/>
```

---

## 第四部分：Wiki 全屏应用（`/repos/[id]/wiki`）

### 4.1 页面布局

```
┌──────────────────────────────────────────────┐
│ ← Repo hub / api-server          [Regenerate]│  42px topbar
├────────┬─────────────────────────────────────┤
│ TOC    │ Wiki content                        │
│        │                                     │
│ Overview│ # Architecture                      │
│  Arch  │                                     │
│  Start │ The API server follows a layered... │
│        │                                     │
│ Core   │ Entry point: [src/server/api.ts]    │
│  Auth  │                                     │
│  Users │ Core analysis runs in a forked...   │
│  Data  │                                     │
│        │                                     │
│ Infra  │                                     │
│  API   │                                     │
│  DB    │                                     │
├────────┴─────────────────────────────────────┤
│ Context: Architecture (2 files) │ Ask...│ DR │  44px chat bar
└──────────────────────────────────────────────┘
```

- 左侧 TOC：210px，wiki 页面导航
- 中间内容：剩余全部宽度
- 底部 Chat bar：固定 44px，显示当前上下文 + 输入框 + Deep Research 开关

### 4.2 Chat bar 行为

用户在 Chat bar 中输入问题并提交后，不在当前页面展示回答，而是 `window.open` 打开 Chat 结果页：

```tsx
const handleChatSubmit = (question: string) => {
  const params = new URLSearchParams({
    q: question,
    page: currentPageId,
    files: currentPage.filePaths.join(","),
    dr: deepResearch ? "1" : "0",
  });
  window.open(`/repos/${repoId}/chat?${params}`, "_blank");
};
```

### 4.3 数据获取

```tsx
// 调用仓库维度的 wiki 端点
const wikiData = await api.repos.wiki.get(repoId);  // GET /api/repos/{id}/wiki
```

---

## 第五部分：Chat 结果页（`/repos/[id]/chat`）

### 5.1 页面布局

```
┌───────────────────────────────────────────────────────────┐
│ ← Wiki / Architecture                     Deep research: off│  42px topbar
├─────────────────────────────┬─────────────────────────────┤
│ Question                    │ Referenced files             │
│ ┌─────────────────────────┐ │ ● src/middleware/auth.ts     │
│ │ How does auth verify JWT │ │ ● src/auth/jwt-utils.ts     │
│ └─────────────────────────┘ │ ● src/auth/refresh.ts       │
│                             │                              │
│ ## JWT verification flow    │ ─────────────────────────── │
│                             │ src/middleware/auth.ts        │
│ The auth middleware is in   │ lines 12-45                  │
│ [src/middleware/auth.ts]    │ ─────────────────────────── │
│ which extracts the Bearer...│ 12│ export async function... │
│                             │ 13│   const authHeader = ... │
│ The core verification calls │ 14│   if (!authHeader?...    │
│ [verifyToken()] which...    │ 15│     return res.status...  │
│                             │ ...                          │
│                             │                              │
├─────────────────────────────┤                              │
│ Follow up: [input         ] │                              │
└─────────────────────────────┴─────────────────────────────┘
```

### 5.2 核心逻辑

1. 页面加载时，从 URL query params 读取 `q`（问题）、`files`（关联文件）、`dr`（deep research）
2. 立即调用 `POST /api/repos/{id}/chat/stream`，带上 `file_path` 和 `included_files`
3. 左侧流式展示 AI 回答
4. 回答中的代码引用（`[filename:line-line]` 格式）解析为可点击元素
5. 点击引用 → 调用 GitNexus `/api/file?repo=xxx&path=xxx&startLine=xx&endLine=xx` → 右侧展示代码
6. 右侧面板默认收起，首次点击引用后滑出
7. 底部 follow-up 输入栏，追问后回答追加在左侧（不开新页面）
8. topbar 左上角 "← Wiki" 返回 wiki 页面

### 5.3 代码引用解析

DeepWiki wiki_prompts.py 要求的格式是 `[filename.ext:start_line-end_line]()`。前端需要用正则提取：

```typescript
const CODE_REF_PATTERN = /\[([^\]]+?):(\d+)-(\d+)\]\(\)/g;
const FILE_REF_PATTERN = /\[([^\]]+?\.\w+)\]\(\)/g;

function parseCodeRefs(markdown: string): CodeRef[] {
  const refs: CodeRef[] = [];
  let match;
  while ((match = CODE_REF_PATTERN.exec(markdown)) !== null) {
    refs.push({
      filePath: match[1],
      startLine: parseInt(match[2]),
      endLine: parseInt(match[3]),
      raw: match[0],
    });
  }
  return refs;
}
```

在渲染 markdown 时，将这些引用替换为可点击的 `<button class="code-ref">` 元素。

---

## 第六部分：Graph 全屏应用（`/repos/[id]/graph`）

### 6.1 页面布局

```
┌──────────────────────────────────────────────────┐
│ ← Repo hub / api-server                          │  42px topbar
├──────────────────────────────────┬────────────────┤
│ [Search symbols...]             │ validateUser() │
│                                 │                │
│                                 │ Type: Function │
│     ┌─────┐                     │ File: src/...  │
│     │     │────────┐            │ Community: Auth│
│     └─────┘        │            │ Callers: 3     │
│        │       ┌───▼───┐        │ Callees: 2     │
│        │       │       │        │ Process:       │
│     ┌──▼──┐    └───────┘        │  Login flow    │
│     │     │                     │  (step 2/5)    │
│     └─────┘                     │                │
│                                 │ [Ask AI]       │
│                                 │ [Impact]       │
│ 142 nodes · 287 edges · 6 comm  │ [View source]  │
└──────────────────────────────────┴────────────────┘
```

### 6.2 数据获取

```tsx
// 调用仓库维度的 graph 端点
const graphData = await api.repos.graph.get(repoId);  // GET /api/repos/{id}/graph
```

### 6.3 Detail panel 操作按钮

- "Ask AI" → 打开 chat 结果页，问题自动填充为 "explain [symbol] function"，filePath 带上
- "Impact" → 调用 GitNexus `/api/search` 或 Cypher 查询展示影响面（后续阶段实现）
- "View source" → 调用 GitNexus `/api/file` 在面板内展示代码

---

## 第七部分：前端 API 客户端扩展

在 `frontend/src/lib/api.ts` 中新增仓库维度的 API：

```typescript
repos: {
  // 现有
  sync: (repoId) => ...,
  delete: (repoId) => ...,

  // 新增
  get: (repoId: string) =>
    request<RepoDetail>(`/api/repos/${repoId}`),

  analyses: (repoId: string, page = 1, pageSize = 10) =>
    request<PaginatedAnalyses>(`/api/repos/${repoId}/analyses?page=${page}&page_size=${pageSize}`),

  wiki: {
    get: (repoId: string) =>
      request<WikiResponse>(`/api/repos/${repoId}/wiki`),
    generate: (repoId: string, comprehensive = true, forceRefresh = false) =>
      request<WikiGenerateResponse>(`/api/repos/${repoId}/wiki/generate`, {
        method: "POST",
        body: JSON.stringify({ comprehensive, force_refresh: forceRefresh }),
      }),
    status: (repoId: string) =>
      request<WikiStatus>(`/api/repos/${repoId}/wiki/status`),
    deleteCache: (repoId: string) =>
      request<{ status: string }>(`/api/repos/${repoId}/wiki/cache`, {
        method: "DELETE",
      }),
  },

  graph: {
    get: (repoId: string) =>
      request<RepoGraphResponse>(`/api/repos/${repoId}/graph`),
  },

  chat: {
    stream: (
      repoId: string,
      messages: { role: string; content: string }[],
      options?: {
        filePath?: string;
        deepResearch?: boolean;
        includedFiles?: string[];
      },
      signal?: AbortSignal,
    ) =>
      fetch(`${BASE}/api/repos/${repoId}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_id: repoId,
          messages,
          file_path: options?.filePath,
          deep_research: options?.deepResearch ?? false,
          included_files: options?.includedFiles,
        }),
        signal,
      }),
  },
},
```

---

## 第八部分：Sidebar 导航调整

### 8.1 当前导航项

```
仪表盘  /dashboard
任务    /tasks
工具    /tools
资产    /assets
设置    /settings
```

### 8.2 改造后

```
仪表盘  /dashboard
仓库    /assets          ← 改标签为"仓库"，路由可保持 /assets 不变
设置    /settings
```

- "任务"从主导航移除。任务列表在仓库页面内作为"分析历史"出现。需要全局任务视图时可在仪表盘中提供链接。
- "工具"从主导航移除。工具健康状态改为在设置页面中展示。
- "资产"改名为"仓库"，这是用户的核心入口。

### 8.3 仓库列表页改造

当前 `/assets` 页面是项目 → 仓库的两级结构。改造后简化为仓库列表（如果需要保留项目分组，用 group header 即可），每个仓库行可点击进入 `/repos/[id]`。

---

## 第九部分：侧边栏入口改造

### 9.1 Sidebar 中添加仓库快捷入口

在资产页面选过的仓库，可在 Sidebar 底部显示"最近使用"列表：

```
仪表盘
仓库

──── 最近 ────
api-server
frontend-app
auth-service

设置
```

点击仓库名直接进入 `/repos/[id]`。

---

## 第十部分：迁移策略

### 10.1 保留旧端点

所有 `/api/tasks/{task_id}/wiki` 等旧端点保留，确保已有页面不崩。`/tasks/[id]` 页面继续可用，但不再是主入口。

### 10.2 分步实施

| 步骤 | 内容 | 预期工时 |
|---|---|---|
| 1 | 后端：新增 repo_wiki、repo_chat、repo_graph、repo detail 端点 | 2 天 |
| 2 | 前端：新增 `(fullscreen)` layout + FullscreenTopbar 组件 | 0.5 天 |
| 3 | 前端：新增 `/repos/[id]` 仓库主页（入口卡片 + 分析历史） | 1.5 天 |
| 4 | 前端：新增 `/repos/[id]/wiki` Wiki 全屏应用 | 2 天 |
| 5 | 前端：新增 `/repos/[id]/graph` Graph 全屏应用 | 1.5 天 |
| 6 | 前端：新增 `/repos/[id]/chat` Chat 结果页（含代码引用面板） | 2.5 天 |
| 7 | 前端：Sidebar 导航调整 + 仓库列表页改造 | 1 天 |
| 8 | 集成测试 + 旧页面兼容验证 | 1 天 |

总计约 12 天。

### 10.3 数据库迁移

无需数据库 schema 变更。新端点全部复用现有表（Repository、AnalysisTask、ToolRun、WikiCacheMeta、LLMConfig）。仅改变查询方式（从 task_id 查 repo 改为直接用 repo_id）。

---

## 附录：新旧 API 对照表

| 功能 | 旧端点（保留） | 新端点 |
|---|---|---|
| 获取 wiki | `GET /api/tasks/{tid}/wiki` | `GET /api/repos/{rid}/wiki` |
| 生成 wiki | `POST /api/tasks/{tid}/wiki/generate` | `POST /api/repos/{rid}/wiki/generate` |
| wiki 进度 | `GET /api/tasks/{tid}/wiki/status` | `GET /api/repos/{rid}/wiki/status` |
| 删除 wiki 缓存 | `DELETE /api/tasks/{tid}/wiki/cache` | `DELETE /api/repos/{rid}/wiki/cache` |
| 导出 wiki | `POST /api/tasks/{tid}/wiki/export` | `POST /api/repos/{rid}/wiki/export` |
| Chat 流式 | `POST /api/chat/stream` (task_id in body) | `POST /api/repos/{rid}/chat/stream` |
| 获取图谱 | 无（嵌在 task detail 中） | `GET /api/repos/{rid}/graph` |
| 仓库详情 | 无 | `GET /api/repos/{rid}` |
| 分析历史 | `GET /api/tasks?repository_id=xxx` | `GET /api/repos/{rid}/analyses` |

## 附录：页面导航流

```
Dashboard ──→ 仓库列表 ──→ 仓库主页 (/repos/[id])
                              │
                              ├──→ Wiki 卡片 ──→ Wiki 全屏 (new window)
                              │                    │
                              │                    └──→ Chat bar 提交 ──→ Chat 结果页 (new window)
                              │                                           │
                              │                                           └──→ ← Wiki 返回 wiki
                              │
                              ├──→ Graph 卡片 ──→ Graph 全屏 (new window)
                              │                    │
                              │                    └──→ Ask AI 按钮 ──→ Chat 结果页 (new window)
                              │
                              ├──→ New Analysis ──→ 分析完成后刷新卡片状态
                              │
                              └──→ 分析历史列表（分页）
```

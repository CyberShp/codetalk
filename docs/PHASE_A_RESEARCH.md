# Phase A 调研报告 — deepwiki Wiki 编排机制与 CodeTalks 接入方案

> 调研执行: @opus | 日期: 2026-04-15
> 上游: PRODUCT_ARCH_v2.md Phase A

## 1. deepwiki Wiki 生成机制（逆向分析结果）

### 1.1 生成流程（五步）

```
1. Cache Check
   GET /api/wiki_cache?owner=X&repo=Y&repo_type=Z&language=L
   → 命中: 直接返回 wiki_structure + generated_pages
   → 未命中: 进入步骤 2

2. Fetch Repo Structure
   GET /local_repo/structure?path=/data/repos/xxx
   → 返回 { file_tree, readme }

3. Determine Wiki Structure (单次 LLM 调用)
   POST /chat/completions/stream
   → 输入: file_tree + readme + 结构化prompt
   → 输出: XML 格式的 wiki_structure（页面列表、层级、关联文件）
   → 前端解析 XML 为 WikiStructure 对象

4. Generate Pages (逐页串行 LLM 调用, MAX_CONCURRENT=1)
   对 wiki_structure.pages 中每个 page:
     POST /chat/completions/stream
     → 输入: page.title + page.filePaths + 详细写作prompt
     → 输出: Markdown + Mermaid 图表
   
5. Save to Cache
   POST /api/wiki_cache
   → { repo, language, wiki_structure, generated_pages, provider, model }
```

### 1.2 Wiki 类型

| 类型 | 页面数 | 特点 |
|------|--------|------|
| Comprehensive | 8-12 页 | 带 sections 层级结构、更详细 |
| Concise | 4-6 页 | 扁平列表、精简 |

注意: `comprehensive` 标志仅影响结构确定 prompt 中的页面数要求，**不影响后端缓存 key**。

### 1.3 缓存 Key 格式

**后端实际实现** (`/app/api/api.py:get_wiki_cache_path`):
```
deepwiki_cache_{repo_type}_{owner}_{repo}_{language}.json
```

**参数**:
- `repo_type`: `github` | `gitlab` | `bitbucket` | `local`
- `owner`: 仓库所有者（本地仓库可用任意字符串，如 `local`）
- `repo`: 仓库名（本地仓库可用 UUID）
- `language`: `zh` | `en` | `ja` | `es` | `kr` | `vi` 等

**CodeTalks 映射方案**:
- `repo_type=local`, `owner=local`, `repo=<task的repo UUID>`, `language=zh`
- 缓存路径示例: `deepwiki_cache_local_local_0ebf3f13-xxxx_zh.json`

### 1.4 Structure Determination Prompt（核心）

结构确定请求发送给 `/chat/completions/stream`，prompt 包含:

```
Analyze this {platform} repository {owner}/{repo}
and create a wiki structure for it.

1. The complete file tree of the project:
<file_tree>
{fileTree}
</file_tree>

2. The README file of the project:
<readme>
{readme}
</readme>

I want to create a wiki for this repository.
Determine the most logical structure for a wiki
based on the repository's content.

Create {8-12 | 4-6} pages that would make a
{comprehensive | concise} wiki...
```

**期望返回 XML 格式**:
```xml
<wiki_structure>
  <title>Overall Title</title>
  <description>Brief description</description>
  <sections>
    <section id="section-1">
      <title>Section Title</title>
      <pages>
        <page_ref>page-1</page_ref>
      </pages>
      <subsections>
        <section_ref>section-2</section_ref>
      </subsections>
    </section>
  </sections>
  <pages>
    <page id="page-1">
      <title>Page Title</title>
      <description>Brief description</description>
      <importance>high|medium|low</importance>
      <relevant_files>
        <file_path>src/main.py</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
      <parent_section>section-1</parent_section>
    </page>
  </pages>
</wiki_structure>
```

### 1.5 Per-Page Generation Prompt（核心）

每个页面生成也通过 `/chat/completions/stream`，prompt 要求:

```
You are an expert technical writer and software architect.
Your task is to generate a comprehensive and accurate technical wiki page
in Markdown format about a specific feature or module.

You will be given:
1. The "[WIKI_PAGE_TOPIC]" for the page
2. A list of "[RELEVANT_SOURCE_FILES]" — use AT LEAST 5 source files

CRITICAL STARTING INSTRUCTION:
Start with a <details> block listing ALL relevant source files.

Structure:
- # {page.title} (H1)
- Introduction with links to related wiki pages
- Detailed sections with H2/H3 headings
- Mermaid diagrams (graph TD, never graph LR)
- Source code citations: [filename.ext:start_line-end_line]()

Generate content in {language} language.
```

### 1.6 数据模型

**WikiStructureModel** (前端 → 后端):
```json
{
  "id": "string",
  "title": "string",
  "description": "string",
  "pages": [WikiPage],
  "sections": [WikiSection] | null,
  "rootSections": ["string"] | null
}
```

**WikiPage**:
```json
{
  "id": "page-1",
  "title": "Architecture Overview",
  "content": "# Architecture...\n```mermaid\ngraph TD\n...\n```",
  "filePaths": ["src/main.py", "src/config.py"],
  "importance": "high",
  "relatedPages": ["page-2", "page-3"]
}
```

**WikiSection** (用于 Comprehensive 模式):
```json
{
  "id": "section-1",
  "title": "Core Architecture",
  "pages": ["page-1", "page-2"],
  "subsections": ["section-2"] | null
}
```

## 2. CodeTalks 接入方案

### 2.1 推荐方案: C（混合渐进）

参照 PRODUCT_ARCH_v2.md 的推荐:

**Phase A-1 (快速上线)**: 后端编排 + 直接存/读 wiki_cache
**Phase A-2 (完善)**: 前端 TOC 侧栏 + 页间导航 + 导出

### 2.2 后端改动: WikiOrchestrator 服务

新增 `backend/app/services/wiki_orchestrator.py`:

```python
class WikiOrchestrator:
    """编排 deepwiki 多页 Wiki 生成。
    
    IRON LAW: 不做任何分析——只调用 deepwiki API 和转换格式。
    所有 LLM 调用通过 deepwiki 的 /chat/completions/stream 代理。
    """
    
    def __init__(self, deepwiki_base_url: str = "http://deepwiki:8001"):
        self.base_url = deepwiki_base_url
    
    async def get_or_generate_wiki(
        self,
        repo_local_path: str,
        owner: str,        # cache key 用
        repo_name: str,    # cache key 用
        language: str,
        provider: str,
        model: str,
        comprehensive: bool = True,
        force_refresh: bool = False,
    ) -> WikiResult:
        """
        1. 检查缓存 → GET /api/wiki_cache
        2. 未命中 → 获取仓库结构 → GET /local_repo/structure
        3. 确定 Wiki 结构 → POST /chat/completions/stream (单次)
        4. 逐页生成 → POST /chat/completions/stream (N 次串行)
        5. 保存缓存 → POST /api/wiki_cache
        """
```

**关键设计决策**:

1. **串行生成**: deepwiki 内部的 RAG retriever 可能不支持并发请求（已知 embedding 阶段会阻塞全部请求），所以保持 MAX_CONCURRENT=1
2. **进度推送**: 当前为 JSON 轮询 `GET /wiki/status → {current, total, page_title}`；A-5 可升级为 SSE
3. **缓存双层**: deepwiki wiki_cache 存内容，CodeTalks DB 存元数据（branch/last_indexed_at/wiki_type）用于失效判断（详见 2.6 节）
4. **LLM 参数传递**: 复用 task_engine 的 `_build_options()` 获取 provider/model/api_key
5. **Prompt 模板**: 从 `wiki_prompts.py` 加载，CodeTalks 自有版本（详见 2.5 节）

### 2.3 后端改动: 新 API 端点

新增路由 `backend/app/api/wiki.py`:

```
GET  /api/tasks/{task_id}/wiki        — 获取 Wiki（优先缓存）
POST /api/tasks/{task_id}/wiki/generate — 触发 Wiki 生成
GET  /api/tasks/{task_id}/wiki/status   — JSON 轮询生成进度（SSE 后置到 A-5）
POST /api/tasks/{task_id}/wiki/export   — 代理 deepwiki export
DELETE /api/tasks/{task_id}/wiki/cache  — 清除缓存，重新生成
```

### 2.4 前端改动

**新增组件**:
- `WikiViewer.tsx` — 多页 Wiki 主容器（替代文档 tab 的扁平 Markdown）
- `WikiTOCSidebar.tsx` — TOC 侧栏（支持 sections 层级折叠）
- `WikiPageRenderer.tsx` — 单页渲染器（复用 MarkdownRenderer + MermaidRenderer）
- `WikiExportBar.tsx` — 导出按钮（Markdown / JSON）

**改动组件**:
- 任务详情页的"文档" tab — 条件渲染: 有 wiki_structure 时用 WikiViewer，否则用现有的扁平 MarkdownRenderer

### 2.5 Prompt 所有权（决议）

**CodeTalks 持有 prompt 模板，deepwiki 只提供 RAG 检索 + LLM 执行通道。**

理由:
- 我们自己编排 `/chat/completions/stream`（步骤 3 结构确定 + 步骤 4 逐页生成），就必须自己组装 prompt
- deepwiki 前端的 prompt 是编译在 Next.js bundle 里的私有实现，没有版本化承诺，上游更新随时可能变
- CodeTalks 持有 prompt 才能做：中文优化、页面数控制、Mermaid 格式约束、citation 格式等定制

实施:
- `backend/app/services/wiki_prompts.py` — 显式、可版本化的 prompt 模板文件
- 结构确定 prompt: 基于 deepwiki 逆向结果（1.4 节），CodeTalks 自有版本
- 页面生成 prompt: 基于 deepwiki 逆向结果（1.5 节），CodeTalks 自有版本
- prompt 变更纳入 code review，不依赖上游前端私有实现

### 2.6 缓存失效策略（决议）

**deepwiki 文件 key 继续复用，CodeTalks 侧增加元数据比对层判断缓存新鲜度。**

问题: deepwiki 的缓存 key `deepwiki_cache_{repo_type}_{owner}_{repo}_{language}.json` 不含 branch / commit / wiki_type。同一仓库切分支、代码更新、或改变 wiki 类型后，旧缓存会被脏读。

决议:

1. **独立 `wiki_cache_meta` 表** — wiki cache 是 repo 级资源，不挂在 task 上（同仓库多任务复用同一 wiki cache）:
   ```
   id              UUID PK
   repository_id   UUID FK → repositories.id (UNIQUE)
   branch          VARCHAR(255)   — 生成时的分支
   last_indexed_at TIMESTAMP      — 生成时仓库的 last_indexed_at
   wiki_type       VARCHAR(20)    — comprehensive | concise
   language        VARCHAR(10)    — zh | en | ...
   generated_at    TIMESTAMP      — wiki 生成完成时间
   ```

2. **读缓存前比对** (freshness = 数据是否过期): `GET /api/tasks/{id}/wiki` 时:
   ```
   current = (repo.branch, repo.last_indexed_at)
   cached  = (meta.branch, meta.last_indexed_at)
   if current != cached → stale: true
   ```
   **`wiki_type` 和 `language` 不参与 freshness 判断** — 它们是用户意图，不是数据新鲜度指标。
   用户切换 wiki_type 时走显式 regenerate（force_refresh），不走 stale 标记。
   前端展示 stale wiki 时显示"内容可能已过期"提示 + 刷新按钮。

3. **显式失效触发**:
   - `DELETE /api/tasks/{id}/wiki/cache` → 删除 deepwiki 缓存 + 清除本地元数据
   - `source_manager.resolve_source()` 更新 `last_indexed_at` 后，下次读 wiki 自动检测到 stale
   - 用户在前端点"刷新 Wiki"→ force_refresh=true → delete + regenerate
   - wiki_type 变更 (comprehensive ↔ concise) → 用户显式触发 regenerate (force_refresh=true)，因为 deepwiki key 不含此字段，必须先 DELETE 再重新生成

### 2.7 不做的事情

- 不自建缓存文件存储 — 缓存内容（wiki pages）仍存在 deepwiki 的 wikicache 目录，CodeTalks 只存元数据
- 不做并发页面生成 — deepwiki RAG 不支持并发
- 不自建 RAG 检索 — 代码检索由 deepwiki 的 FAISS retriever 完成

## 3. 关键风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| deepwiki RAG embedding 阻塞（已知问题，SPDK 用了 2 小时） | Wiki 生成期间所有 deepwiki 请求被阻塞 | 前端展示预估时间 + 允许取消 + 生成后缓存 |
| Structure determination 返回非法 XML | 解析失败，无法生成页面 | 正则回退解析 + 重试 1 次 |
| 单页生成失败 | 部分页面缺失 | 跳过失败页面，标记为"生成失败"，允许单页重试 |
| wiki_cache 文件被清除（容器重启） | 缓存丢失，需重新生成 | Volume mount wikicache 目录（已在 docker-compose 中配置） |
| 缓存脏读（代码更新/切分支后命中旧 wiki） | 用户看到过时文档 | 2.6 节元数据比对层 — 读前比对 branch + last_indexed_at，不一致标记 stale |
| prompt 模板与 deepwiki 上游分叉 | 生成质量可能偏离预期 | CodeTalks 持有 prompt (2.5 节)，独立演进，不依赖上游 |

## 4. 实施顺序建议

```
A-1: wiki_prompts.py (prompt 模板) + wiki_cache_meta DB 表/字段
     ↓ 基础设施
A-2: WikiOrchestrator 服务 + /api/tasks/{id}/wiki 端点
     ↓ 可用 curl 验证（含缓存比对 + stale 标记）
A-3: 前端 WikiViewer + TOCSidebar + 页间导航
     ↓ 可在浏览器验证
A-4: 导出功能 + 缓存管理（清除/刷新/stale 提示）
     ↓ 完整功能
A-5: 进度推送升级为 SSE（当前 A-2 实现为 JSON 轮询，功能可用）
```

## 5. 已关闭决议

| 问题 | 决议 | 详见 |
|------|------|------|
| 缓存策略 | deepwiki key 复用 + CodeTalks 元数据比对层 | 2.6 节 |
| prompt 所有权 | CodeTalks 持有，deepwiki 只做 RAG+LLM 通道 | 2.5 节 |
| cache key 映射 | `owner=local, repo=<repository.id>, repo_type=local` | 1.3 节 |

## 6. 开放问题（待缅因猫决策）

1. **Wiki 类型选择**: 是否暴露 comprehensive/concise 选项给用户？还是固定用 comprehensive？
2. **Structure prompt 自定义**: 是否需要让用户影响 wiki 结构（如指定重点分析的目录）？deepwiki 的 `included_dirs`/`excluded_dirs` 参数已支持这个

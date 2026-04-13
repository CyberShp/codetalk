# Phase 3A: Zoekt Adapter

**前置依赖：Phase 2A 完成（BaseToolAdapter 已定义）**
**可与其他 Phase 3 任务并行**

## 任务目标

实现 Zoekt 代码搜索工具的 adapter，以及其 Docker 容器配置。

## Zoekt 概述

Zoekt 是 Google 开源的基于 trigram 的代码搜索引擎。提供 JSON HTTP API 进行搜索。

## 步骤

### 1. Docker 配置

Zoekt 使用官方镜像，需要两个组件：
- `zoekt-indexserver` 或 `zoekt-git-index`（索引器）
- `zoekt-webserver`（搜索 API 服务器）

在 `docker-compose.yml` 中确认 zoekt 服务配置：
```yaml
zoekt:
  image: ghcr.io/sourcegraph/zoekt-webserver:latest
  ports:
    - "6070:6070"
  volumes:
    - zoekt_index:/data/index
    - code_volume:/data/repos:ro
  command: ["-index", "/data/index", "-listen", ":6070"]
```

另需一个 indexer 服务或在 prepare 阶段通过 docker exec 调用。

### 2. 实现 Adapter (`backend/app/adapters/zoekt.py`)

```python
class ZoektAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://zoekt:6070"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url)

    def name(self) -> str:
        return "zoekt"

    def capabilities(self) -> List[ToolCapability]:
        return [ToolCapability.CODE_SEARCH]

    async def health_check(self) -> ToolHealth:
        # GET / 检查 web UI 是否响应
        try:
            resp = await self.client.get("/")
            return ToolHealth(is_healthy=resp.status_code == 200, container_status="running")
        except:
            return ToolHealth(is_healthy=False, container_status="error")

    async def prepare(self, request: AnalysisRequest) -> None:
        # 通过 docker exec 调用 zoekt-index 或 zoekt-git-index
        # 索引目标仓库到 /data/index/
        # 命令: zoekt-index -index /data/index /data/repos/{repo_name}
        # 用 docker SDK 或 subprocess 执行
        ...

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 调用 Zoekt 的 JSON search API
        # GET /api/search?q={query}&num=50
        # 如果有 target_files，限制搜索范围：file:{pattern}
        # 返回搜索结果：文件名、行号、代码片段
        #
        # 注意：这里只做 HTTP 调用，不做任何文本匹配/正则搜索
        ...

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        # 索引阶段的日志输出
        ...
```

### 3. Zoekt API 参考

**搜索端点：** `GET /api/search`
- 参数：`q` (查询字符串), `num` (结果数量)
- 查询语法：支持 `file:pattern`, `repo:name`, `case:yes`, `lang:python` 等限定符
- 返回 JSON：`{Result: {Files: [{FileName, Repository, LineMatches: [{LineNumber, Line}]}]}}`

**列表端点：** `GET /api/list`
- 列出已索引的仓库

### 4. 注册 Adapter

在 `backend/app/adapters/__init__.py` 中注册：
```python
from .zoekt import ZoektAdapter
register_adapter(ZoektAdapter())
```

## 验收标准

- [ ] Zoekt 容器正常启动
- [ ] prepare() 成功索引一个测试仓库
- [ ] analyze() 通过 Zoekt API 返回搜索结果
- [ ] 移除 zoekt 容器后，adapter 报连接错误
- [ ] analyze() 中无任何文本搜索/正则匹配逻辑

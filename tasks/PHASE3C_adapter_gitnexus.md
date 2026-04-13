# Phase 3C: GitNexus Adapter

**前置依赖：Phase 2A 完成（BaseToolAdapter 已定义）**
**可与其他 Phase 3 任务并行**

## 任务目标

实现 GitNexus 知识图谱工具的 adapter 和自建 Docker 镜像。

## GitNexus 概述

零服务器、客户端代码智能引擎，基于 tree-sitter AST 分析创建交互式知识图谱。支持多语言。原生是浏览器端运行，需要配置为 bridge/serve 模式暴露 HTTP API。

GitHub: https://github.com/abhigyanpatwari/GitNexus

## 步骤

### 1. 研究 GitNexus CLI 和 Bridge 模式

先克隆 GitNexus 仓库研究其 API：
```bash
git clone https://github.com/abhigyanpatwari/GitNexus.git /tmp/gitnexus-research
```

重点了解：
- CLI 命令 `gitnexus analyze` 的输入输出
- `gitnexus serve` bridge 模式暴露的 HTTP 端点
- MCP 集成的 API 结构
- LadybugDB 存储格式

### 2. 自建 Dockerfile (`docker/gitnexus/Dockerfile`)

```dockerfile
FROM node:20-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gitnexus
RUN git clone https://github.com/abhigyanpatwari/GitNexus.git .
RUN npm install

EXPOSE 7100
# bridge 模式，暴露 HTTP API
CMD ["node", "server.js", "--port", "7100"]
```

注意：具体的启动命令需要研究 GitNexus 的实际 CLI 接口，上面是占位。

### 3. 实现 Adapter (`backend/app/adapters/gitnexus.py`)

```python
class GitNexusAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://gitnexus:7100"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url)

    def name(self) -> str:
        return "gitnexus"

    def capabilities(self) -> List[ToolCapability]:
        return [
            ToolCapability.KNOWLEDGE_GRAPH,
            ToolCapability.AST_ANALYSIS,
            ToolCapability.DEPENDENCY_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        # HTTP GET 检查 bridge server
        ...

    async def prepare(self, request: AnalysisRequest) -> None:
        # 调用 gitnexus analyze 命令索引仓库
        # 构建 tree-sitter AST 知识图谱到 LadybugDB
        ...

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 通过 bridge HTTP API 查询：
        #
        # 可能的端点（需研究确认）：
        # - query: BM25 + 语义混合搜索
        # - context: 360度符号分析（调用者/被调用者/依赖/被依赖）
        # - impact: 爆炸半径/影响范围分析
        # - graph: 知识图谱数据（聚类图、处理流程图）
        #
        # 如果有 target_files，限制分析范围到指定文件
        #
        # 所有图谱构建由 GitNexus 的 tree-sitter 引擎完成
        # adapter 只做 HTTP 查询和结果转换
        ...

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        # 索引阶段的进度日志
        ...
```

### 4. 结果格式转换

GitNexus 返回的知识图谱数据需要转换为 `UnifiedResult`：
- `data`: 节点和边的列表（用于前端渲染图谱）
- `diagrams`: 如果有生成的可视化，转为 SVG/Mermaid

## 关键注意事项

- GitNexus 是相对新的项目，API 可能不稳定，需要在实现前仔细研究其源码
- 如果 bridge 模式不可用或不成熟，备选方案是：
  1. 在容器中运行 GitNexus 的核心分析，将结果写入文件
  2. Adapter 读取结果文件
  3. 但仍然不能自己写分析逻辑
- Tree-sitter 的语言支持取决于 GitNexus 集成了哪些 tree-sitter grammar

## 验收标准

- [ ] GitNexus Docker 镜像能成功构建和启动
- [ ] prepare() 成功索引一个测试仓库
- [ ] analyze() 通过 HTTP API 获取知识图谱数据
- [ ] 移除容器后 adapter 报错
- [ ] analyze() 中无任何 AST 解析或图谱构建逻辑

# Phase 3E: Joern Adapter

**前置依赖：Phase 2A 完成（BaseToolAdapter 已定义）**
**可与其他 Phase 3 任务并行**

## 任务目标

实现 Joern 代码属性图(CPG)分析工具的 adapter 和自建 Docker 镜像。

## Joern 概述

开源代码分析平台，通过生成代码属性图(CPG)支持跨函数/跨文件的污点分析、安全扫描、调用图分析。使用 CPGQL 查询语言，提供 HTTP server 模式。

GitHub: https://github.com/joernio/joern
文档: https://docs.joern.io/

## 步骤

### 1. 自建 Dockerfile (`docker/joern/Dockerfile`)

```dockerfile
FROM eclipse-temurin:21-jre

RUN apt-get update && apt-get install -y curl unzip && rm -rf /var/lib/apt/lists/*

# 下载 Joern 最新版本
RUN curl -L https://github.com/joernio/joern/releases/latest/download/joern-install.sh -o /tmp/joern-install.sh && \
    chmod +x /tmp/joern-install.sh && \
    /tmp/joern-install.sh --install-dir=/opt/joern

ENV PATH="/opt/joern/joern-cli:$PATH"

WORKDIR /data
EXPOSE 8080

# 以 server 模式启动
CMD ["joern", "--server", "--server-host", "0.0.0.0", "--server-port", "8080"]
```

### 2. docker-compose.yml 补充

```yaml
joern:
  build: ./docker/joern
  ports:
    - "8080:8080"
  volumes:
    - code_volume:/data/repos:ro
    - joern_workspace:/data/workspace
  deploy:
    resources:
      limits:
        memory: 4G  # CPG 生成需要较大内存
```

### 3. Joern Server API 参考

Joern server 模式提供以下端点：

**执行查询：** `POST http://joern:8080/query`
```json
{"query": "cpg.method.name.l"}
```
返回：`{"uuid": "xxx"}` — 异步执行

**获取结果：** `GET http://joern:8080/result/{uuid}`
返回查询结果或运行状态

**WebSocket：** `ws://joern:8080/connect`
实时查询执行状态

### 4. 实现 Adapter (`backend/app/adapters/joern.py`)

```python
class JoernAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://joern:8080"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=600)  # CPG 操作很慢

    def name(self) -> str:
        return "joern"

    def capabilities(self) -> List[ToolCapability]:
        return [
            ToolCapability.TAINT_ANALYSIS,
            ToolCapability.SECURITY_SCAN,
            ToolCapability.CALL_GRAPH,
            ToolCapability.AST_ANALYSIS,
        ]

    async def health_check(self) -> ToolHealth:
        # HTTP GET 检查 server 端口
        ...

    async def prepare(self, request: AnalysisRequest) -> None:
        # 1. 发送 CPGQL 导入代码：
        #    POST /query {"query": "importCode(\"/data/repos/{name}\")"}
        # 2. 轮询 GET /result/{uuid} 直到 CPG 构建完成
        # 3. 这是最耗时的步骤，大项目可能需要几分钟
        ...

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 发送预定义的 CPGQL 查询，所有分析由 Joern 引擎执行：
        #
        # 1. 方法列表：
        #    "cpg.method.name.l"
        #
        # 2. 调用图：
        #    "cpg.method.callOut.map(c => (c.name, c.start.get)).l"
        #
        # 3. 安全扫描 - 查找常见漏洞模式：
        #    SQL 注入: 'cpg.call("exec.*").argument.isLiteral.l'
        #    命令注入: 类似 CPGQL 查询
        #
        # 4. 污点分析（如果指定了 source/sink）：
        #    "def source = cpg.method(\"getUserInput\").callOut;
        #     def sink = cpg.method(\"exec\").parameter;
        #     sink.reachableBy(source).l"
        #
        # 5. 如果有 target_files，限制查询范围：
        #    "cpg.method.where(_.file.name(\".*TargetFile.*\")).callOut.l"
        #
        # 每个查询：POST /query → 轮询 GET /result/{uuid}
        #
        # 关键：所有查询都是 CPGQL 表达式，由 Joern 执行
        # adapter 中不写任何代码分析逻辑
        ...

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        # WebSocket ws://joern:8080/connect 获取实时状态
        ...

    # 辅助方法
    async def _execute_query(self, cpgql: str) -> dict:
        """发送 CPGQL 查询并等待结果"""
        # POST /query
        resp = await self.client.post("/query", json={"query": cpgql})
        uuid = resp.json()["uuid"]
        # 轮询结果
        while True:
            result = await self.client.get(f"/result/{uuid}")
            data = result.json()
            if data.get("ready", False):
                return data
            await asyncio.sleep(1)
```

### 5. 预定义 CPGQL 查询集

创建一个查询模板文件 `backend/app/adapters/joern_queries.py`：

```python
# 这些是发送给 Joern 执行的 CPGQL 查询字符串
# 所有分析由 Joern 的 CPG 引擎完成

QUERIES = {
    "methods": "cpg.method.map(m => (m.name, m.filename, m.lineNumber.getOrElse(-1))).l",
    "call_graph": "cpg.method.callOut.map(c => (c.name, c.methodFullName)).l",
    "security_sql_injection": 'cpg.call("(?i)(exec|query|execute).*").argument.order(1).isLiteral.code.l',
    "security_command_injection": 'cpg.call("(?i)(exec|system|popen|Runtime.exec).*").l',
    "file_methods": 'cpg.method.where(_.file.name(".*{filename}.*")).name.l',
    "taint_analysis": """
        def source = cpg.method("{source}").callOut
        def sink = cpg.method("{sink}").parameter
        sink.reachableBy(source).l
    """,
}
```

这些查询字符串会被发送到 Joern server 执行。codetalks 不做任何分析。

## 验收标准

- [ ] Joern Docker 镜像能成功构建（需要 4G+ 内存）
- [ ] Joern server 模式正常启动并响应 HTTP
- [ ] prepare() 成功导入一个测试项目的 CPG
- [ ] analyze() 通过 CPGQL 查询获取方法列表、调用图、安全扫描结果
- [ ] _execute_query() 正确处理异步查询/轮询模式
- [ ] 移除容器后 adapter 报错
- [ ] analyze() 中零行代码分析逻辑，所有分析由 CPGQL 查询完成

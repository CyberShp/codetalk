# Phase 3B: CodeCompass Adapter

**前置依赖：Phase 2A 完成（BaseToolAdapter 已定义）**
**可与其他 Phase 3 任务并行**

## 任务目标

实现 CodeCompass 代码理解工具的 adapter 和自建 Docker 镜像。

## CodeCompass 概述

Ericsson 开源的代码理解工具，支持 C/C++/C#/Python。基于 LLVM/Clang 编译器基础设施，提供函数调用分析、指针分析、架构/组件/接口图。使用 Thrift API。

GitHub: https://github.com/Ericsson/CodeCompass

## 步骤

### 1. 自建 Dockerfile (`docker/codecompass/Dockerfile`)

CodeCompass 需要从源码构建，环境较重：

```dockerfile
FROM ubuntu:22.04

# 安装依赖
RUN apt-get update && apt-get install -y \
    cmake make g++ \
    llvm-15 clang-15 libclang-15-dev \
    libboost-all-dev \
    default-jdk \
    libgraphviz-dev \
    libmagic-dev \
    libgit2-dev \
    ctags \
    libgtest-dev \
    npm \
    postgresql-client libpq-dev \
    thrift-compiler libthrift-dev \
    && rm -rf /var/lib/apt/lists/*

# 构建 CodeCompass
WORKDIR /opt
RUN git clone https://github.com/Ericsson/CodeCompass.git
WORKDIR /opt/CodeCompass
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/opt/codecompass-install \
             -DDATABASE=pgsql \
             -DWITH_AUTH=plain && \
    make -j$(nproc) && \
    make install

ENV PATH="/opt/codecompass-install/bin:$PATH"

EXPOSE 6251
ENTRYPOINT ["CodeCompass_webserver"]
CMD ["-w", "/data/workspaces", "-p", "6251"]
```

注意：这个构建过程很长，建议预构建镜像推到本地 registry。

### 2. docker-compose.yml 补充

```yaml
codecompass:
  build: ./docker/codecompass
  ports:
    - "6251:6251"
  volumes:
    - code_volume:/data/repos:ro
    - codecompass_data:/data/workspaces
  depends_on:
    - postgres
  environment:
    - CC_DATABASE=pgsql:host=postgres;port=5432;user=codetalks;password=${POSTGRES_PASSWORD};database=codecompass
```

### 3. 实现 Adapter (`backend/app/adapters/codecompass.py`)

```python
class CodeCompassAdapter(BaseToolAdapter):
    def name(self) -> str:
        return "codecompass"

    def capabilities(self) -> List[ToolCapability]:
        return [
            ToolCapability.CALL_GRAPH,
            ToolCapability.ARCHITECTURE_DIAGRAM,
            ToolCapability.POINTER_ANALYSIS,
            ToolCapability.DEPENDENCY_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        # HTTP GET 检查 web server 端口
        ...

    async def prepare(self, request: AnalysisRequest) -> None:
        # 运行 CodeCompass_parser 解析项目
        # 命令: CodeCompass_parser
        #   -d "pgsql:host=postgres;..."
        #   -w /data/workspaces
        #   -n {project_name}
        #   -i /data/repos/{repo_name}
        #
        # 这是一个重量级操作，可能需要几分钟
        # 通过 docker exec 执行
        ...

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        # 查询 CodeCompass Thrift/Web API 获取分析结果
        #
        # CodeCompass web server 提供 REST-like 端点：
        # - 获取文件列表
        # - 获取函数调用图 (SVG/JSON)
        # - 获取类层级图
        # - 获取依赖图
        # - 获取指针分析结果
        #
        # 所有分析由 CodeCompass 的 parser 在 prepare 阶段完成
        # 这里只是查询已有结果
        #
        # 注意：对于非 C/C++/C#/Python 的仓库，返回空结果并说明不支持
        ...

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        # 解析阶段的日志（parser 输出）
        ...
```

### 4. 语言支持检查

在 analyze() 开始前检查仓库语言：
- 如果仓库主要是 JavaScript/TypeScript/Go/Rust 等非支持语言
- 返回空结果，附带消息 "CodeCompass only supports C/C++, C#, Python"
- 不要尝试用 CodeCompass 分析不支持的语言

### 5. Thrift API 研究

需要研究 CodeCompass 的 Thrift 服务定义来了解可用的 API 调用。源码中的 `.thrift` 文件定义了服务接口。如果 Thrift 客户端集成困难，可以通过 CodeCompass 的 Web UI 暴露的 REST 端点作为替代。

## 验收标准

- [ ] CodeCompass Docker 镜像能成功构建
- [ ] prepare() 成功解析一个 C/C++ 测试项目
- [ ] analyze() 通过 CodeCompass API 获取调用图和依赖图
- [ ] 非支持语言的仓库返回空结果+说明
- [ ] 移除容器后 adapter 报错
- [ ] analyze() 中无任何代码解析逻辑

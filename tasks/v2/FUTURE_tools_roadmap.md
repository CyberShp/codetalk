# Future: 工具扩展路线图

> 以下工具已规划但不在当前 MVP 范围内。每个工具作为独立垂直切片，遵循相同模式：
> Docker 验证 → Adapter 实现 → API 集成 → 前端可视化 → Review

## 扩展原则

1. **每次只加一个工具**，跑通再开下一个
2. 每个 adapter 必须经过缅因猫「零分析逻辑」review
3. 新 adapter 只需：实现 BaseToolAdapter 接口 + 注册到 TOOL_REGISTRY + docker-compose 加服务
4. 前端 Tools 页面自动从 `GET /api/tools` 读取，新工具自动出现
5. Task Detail 的 Findings tab 按 tool_name 分组渲染，新工具结果自动展示

---

## F1: Zoekt — 代码搜索

**优先级：高（MVP 之后第一个加）**
**复杂度：低**

| 项目 | 说明 |
|------|------|
| Docker | 官方镜像 `ghcr.io/sourcegraph/zoekt-webserver`，无需自建 |
| API | JSON HTTP `GET /api/search?q=...&num=50` |
| 能力 | CODE_SEARCH |
| 端口 | 6070 |
| prepare | 调用 zoekt-index 索引仓库 |
| analyze | HTTP 搜索，返回文件名+行号+代码片段 |
| 前端 | Findings tab 新增搜索结果列表组件 |

**验证步骤：**
```bash
docker run -d --name zoekt-test -p 6070:6070 \
  -v code_volume:/data/repos:ro \
  ghcr.io/sourcegraph/zoekt-webserver:latest
curl http://localhost:6070/api/search?q=main
```

**前端可视化：**
- 搜索结果列表：文件路径 + 行号 + 代码片段（语法高亮）
- 支持按文件分组
- 点击结果跳转到文件视图

---

## F2: Joern — CPG/安全分析/调用图

**优先级：高**
**复杂度：高（CPG 构建耗时，CPGQL 查询设计）**

| 项目 | 说明 |
|------|------|
| Docker | 自建镜像 `eclipse-temurin:21-jre` + joern-install.sh |
| API | HTTP `POST /query` + `GET /result/{uuid}`（异步） |
| 能力 | TAINT_ANALYSIS, SECURITY_SCAN, CALL_GRAPH, AST_ANALYSIS |
| 端口 | 8080 |
| 内存 | 需要 4G+ |
| prepare | `importCode("/data/repos/{name}")` — CPG 构建，耗时几分钟 |
| analyze | 发送预定义 CPGQL 查询，轮询结果 |

**预定义查询集：**
- 方法列表
- 调用图
- SQL 注入检测
- 命令注入检测
- 污点分析（可选 source/sink）

**前端可视化：**
- 安全发现列表（严重度/文件/行号/描述）
- 调用图（react-flow 或 d3-force 节点图）
- 污点传播路径高亮

**注意：** Joern 的 server 模式 API 需要在 Phase 0 验证，v1 时在这里踩过坑。

---

## F3: CodeCompass — 调用图/依赖图/指针分析

**优先级：中（目标用户有 C/C++ 需求时加）**
**复杂度：极高（从 LLVM 源码编译，构建 30min+）**

| 项目 | 说明 |
|------|------|
| Docker | 自建镜像，基于 ubuntu:22.04，需编译 LLVM/Clang |
| API | Thrift 服务 / Web UI REST 端点 |
| 能力 | CALL_GRAPH, ARCHITECTURE_DIAGRAM, POINTER_ANALYSIS, DEPENDENCY_GRAPH |
| 端口 | 6251 |
| 语言限制 | 只支持 C/C++, C#, Python |
| prepare | 运行 CodeCompass_parser（重量级，几分钟到几十分钟） |

**风险：**
- 构建镜像极其耗时，建议预构建推到本地 registry
- Thrift API 集成复杂度高
- 只有 C/C++ 生态才值得投入

**前端可视化：**
- 调用图 / 类层级图（SVG 渲染）
- 依赖图
- 指针分析结果

---

## F4: GitNexus — 知识图谱

**优先级：低（项目成熟度不够）**
**复杂度：高（bridge mode 不确定）**

| 项目 | 说明 |
|------|------|
| Docker | 自建镜像，Node.js 环境 |
| API | bridge HTTP（需研究确认） |
| 能力 | KNOWLEDGE_GRAPH, AST_ANALYSIS, DEPENDENCY_GRAPH |
| 端口 | 7100 |
| 基础 | tree-sitter AST 分析 |

**风险：**
- 项目较新，API 可能不稳定
- bridge mode 是否可用需要先验证
- 如果 bridge 不可用，需要 CLI → 文件 → 读取的间接方案

**前端可视化：**
- 知识图谱节点图（d3-force）
- 符号 360 度分析面板
- 影响范围分析

---

## F5: MR Diff 分析

**优先级：中（在至少 2 个工具跑通后加）**

- 解析 GitHub PR / GitLab MR URL
- 通过 API 获取变更文件列表
- 将文件列表传给各 adapter 做范围分析
- 前端展示 diff 视图 + 分析结果叠加

---

## 工具扩展 Checklist（每个新工具必做）

- [ ] Phase 0 验证：Docker 能跑 + API 真实可用
- [ ] Adapter 实现：只做 HTTP 调用 + 格式转换
- [ ] docker-compose 加服务定义
- [ ] 注册到 TOOL_REGISTRY
- [ ] API /api/tools 自动包含
- [ ] 前端 Tools 页面自动展示
- [ ] Task Detail Findings tab 渲染结果
- [ ] 缅因猫 review：零分析逻辑确认
- [ ] 端到端测试：创建任务 → 运行 → 查看结果
- [ ] 移除容器测试：应报错而非静默

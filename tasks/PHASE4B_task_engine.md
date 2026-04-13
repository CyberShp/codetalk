# Phase 4B: 分析任务引擎集成

**前置依赖：Phase 2A + 2B + 至少一个 Phase 3 Adapter 完成**
**可与其他 Phase 4 任务并行**

## 任务目标

将 Task Engine 与真实 Adapter 集成，实现完整的分析任务创建、运行、监控、结果展示流程。

## 步骤

### 1. 后端 — 任务创建 API

`POST /api/tasks` 请求体：

```json
{
  "repository_id": "uuid",
  "task_type": "full_repo",          // full_repo | file_paths | mr_diff
  "tools": ["zoekt", "joern"],       // 要使用的工具列表
  "ai_enabled": false,
  "target_spec": {
    // full_repo: {}
    // file_paths: {"files": ["src/main.py", "src/utils.py"]}
    // mr_diff: {"mr_url": "https://github.com/org/repo/pull/123"}
  }
}
```

### 2. 后端 — 任务执行

任务创建后加入后台队列（用 asyncio.create_task 或简单的内存队列）：

```python
async def execute_task(task_id: UUID):
    task = get_task(task_id)
    update_status(task_id, "running")

    try:
        # 1. 解析代码来源
        repo = get_repo(task.repository_id)
        local_path = await source_manager.resolve_source(repo)

        request = AnalysisRequest(
            repo_local_path=local_path,
            target_files=task.target_spec.get("files"),
            task_type=task.task_type,
        )

        # 2. MR diff 处理
        if task.task_type == "mr_diff":
            request.target_files = await git_service.get_mr_diff(task.target_spec["mr_url"])

        # 3. 获取 adapter 列表（过滤 AI 关闭时的 deepwiki）
        adapters = []
        for name in task.tools:
            if name == "deepwiki" and not task.ai_enabled:
                continue
            adapters.append(get_adapter(name))

        # 4. 为每个 adapter 创建 tool_run 记录
        tool_runs = create_tool_runs(task_id, [a.name() for a in adapters])

        # 5. 并行 prepare
        prepare_tasks = []
        for adapter, run in zip(adapters, tool_runs):
            prepare_tasks.append(run_with_logging(adapter.prepare, request, run.id))
        await asyncio.gather(*prepare_tasks, return_exceptions=True)

        # 6. 并行 analyze
        results = []
        for adapter, run in zip(adapters, tool_runs):
            try:
                result = await adapter.analyze(request)
                update_tool_run(run.id, status="completed", result=result)
                results.append(result)
            except Exception as e:
                update_tool_run(run.id, status="failed", error=str(e))

        # 7. AI 总结（可选）
        if task.ai_enabled and results:
            summary = await ai_service.summarize_results(results)
            save_summary(task_id, summary)

        # 8. 完成
        update_status(task_id, "completed", progress=100)
    except Exception as e:
        update_status(task_id, "failed", error=str(e))
```

### 3. 后端 — 结果查询 API

`GET /api/tasks/{id}/results` 返回：

```json
{
  "task_id": "uuid",
  "status": "completed",
  "tool_runs": [
    {
      "tool_name": "zoekt",
      "status": "completed",
      "result": {
        "tool_name": "zoekt",
        "capability": "code_search",
        "data": { /* Zoekt 搜索结果 */ },
        "diagrams": [],
        "metadata": {}
      }
    },
    {
      "tool_name": "joern",
      "status": "completed",
      "result": {
        "tool_name": "joern",
        "capability": "taint_analysis",
        "data": { /* Joern CPG 查询结果 */ },
        "diagrams": [{"type": "mermaid", "content": "graph LR; ..."}],
        "metadata": {}
      }
    }
  ],
  "ai_summary": "..." // 如果 AI 开启
}
```

### 4. 前端 — 创建任务

**新建分析任务 Modal/页面：**
1. 选择仓库（下拉列表）
2. 选择任务类型：
   - 全量仓库分析
   - 指定文件路径（多行输入或文件树选择）
   - MR 链接（URL 输入）
3. 选择工具（多选复选框，显示工具能力）
4. AI 开关
5. 提交

### 5. 前端 — Tasks 列表页串联

替换 mock 数据：
- 调用 `GET /api/tasks` 获取任务列表
- 定时轮询或 WebSocket 更新运行中任务的进度
- 过滤 tabs 切换
- 点击任务行跳转到详情页

### 6. 前端 — Task 详情页串联

调用 `GET /api/tasks/{id}/results` 获取结果：

**Analysis Flow tab：**
- 展示每个工具的执行状态（准备中→分析中→完成/失败）
- 可视化工具间的数据流

**Findings tab：**
- 按工具分组展示分析结果
- Zoekt：搜索结果列表（文件名+行号+代码片段）
- Joern：安全发现列表 + 调用图
- GitNexus：知识图谱节点和关系
- CodeCompass：调用图/依赖图（SVG 渲染）

**Documentation tab：**
- deepwiki 生成的文档（Markdown 渲染）
- Mermaid 图表渲染

**AI Summary：**
- 如果有 AI 总结，显示在顶部

### 7. 结果可视化组件

**Mermaid 图表渲染：**
- 使用 mermaid.js 库渲染 Mermaid 格式的图表
- 工具返回的 diagrams 中 type="mermaid" 的内容

**代码片段展示：**
- 搜索结果中的代码高亮（使用 highlight.js 或 shiki）
- 行号显示

**图谱可视化：**
- 知识图谱/调用图的节点-边图（使用 d3-force 或 react-flow）

## 验收标准

- [ ] 可创建全量仓库分析任务并成功运行
- [ ] 可创建指定文件分析任务
- [ ] 任务进度实时更新
- [ ] 每个工具的结果正确展示
- [ ] Mermaid 图表正确渲染
- [ ] AI 开关生效（关闭时跳过 deepwiki 和 LLM 总结）

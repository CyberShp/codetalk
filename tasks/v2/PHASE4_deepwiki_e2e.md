# Phase 4: deepwiki 端到端集成

**前置依赖：Phase 2 + Phase 3 完成**
**完成后解锁：Phase 5**
**预估复杂度：高（前后端全链路串联）**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。前端渲染 deepwiki 返回的 Markdown 和 Mermaid，不做任何文档生成。

## 目标

将后端 Task Engine + deepwiki adapter 与前端全部串联，实现完整的用户流程：

```
创建项目 → 添加仓库 → 创建分析任务(选 deepwiki) → 
查看运行进度 → 查看文档结果(Markdown + Mermaid) → 
查看 AI 总结(可选)
```

## 步骤

### 1. 后端 — Tasks API 真实实现

将桩替换为真实代码：

```python
@router.post("/api/tasks")
async def create_task(task: TaskCreate, db = Depends(get_db)):
    # 1. 验证 repository 存在
    # 2. 验证 tools 列表中的工具都已注册（MVP 只有 deepwiki）
    # 3. 保存 task 到数据库
    # 4. 用 asyncio.create_task 后台执行
    task_record = await save_task(db, task)
    asyncio.create_task(task_engine.run_task(task_record.id))
    return TaskResponse.from_orm(task_record)

@router.get("/api/tasks")
async def list_tasks(status: str = None, project_id: UUID = None, ...):
    # 支持按 status / project_id 过滤
    ...

@router.get("/api/tasks/{id}")
async def get_task(id: UUID, db = Depends(get_db)):
    # 返回 task 详情 + tool_runs 列表
    ...

@router.get("/api/tasks/{id}/results")
async def get_task_results(id: UUID, db = Depends(get_db)):
    # 返回所有 tool_run 的 result（包含 deepwiki 文档和图表）
    ...
```

### 2. 后端 — WebSocket 日志流

```python
@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs(websocket: WebSocket, task_id: UUID):
    await websocket.accept()
    # 从 task_logs 表轮询新日志推送
    # 或从 adapter.stream_logs() 收集
    ...
```

### 3. 后端 — Settings API 真实实现

```python
@router.get("/api/settings/llm")     # 获取 LLM 配置列表
@router.post("/api/settings/llm")    # 保存 LLM 配置
@router.delete("/api/settings/llm/{id}")  # 删除
@router.post("/api/settings/llm/test")    # 测试连接
```

API Key 加密存储（Fernet），前端只显示脱敏版。

### 4. 前端 — 创建分析任务

**路径：** Assets 页面点击仓库 → "Analyze" → 弹出 Modal

**创建任务 Modal：**
1. 仓库名（只读，已选中）
2. 任务类型选择：
   - 全量仓库分析
   - 指定文件路径（多行输入）
   - MR 链接（URL 输入）— MVP 可暂不实现
3. 工具选择：
   - deepwiki ✅ (可选)
   - Zoekt — Coming Soon (灰显)
   - Joern — Coming Soon (灰显)
   - CodeCompass — Coming Soon (灰显)
   - GitNexus — Coming Soon (灰显)
4. AI 开关 Toggle
5. "Start Analysis" 按钮

### 5. 前端 — Tasks 列表页串联

替换 mock 数据：
- 调用 `GET /api/tasks` 获取列表
- 5 秒轮询更新运行中任务的进度
- 过滤 tabs 切换 status 参数
- 点击行 → 跳转 Task Detail

### 6. 前端 — Task Detail 页面串联

调用 `GET /api/tasks/{id}/results`：

**Documentation tab（deepwiki 核心）：**
- 用 `MarkdownRenderer` 渲染 deepwiki 生成的文档
- 文档中的 Mermaid 代码块用 `MermaidRenderer` 渲染为交互图表
- 支持目录导航（从 Markdown 标题生成）

**Analysis Flow tab：**
- 展示工具执行状态节点（深wiki: preparing → analyzing → completed）
- 简单状态流程图

**AI Summary 面板：**
- 如果 task.ai_enabled 且有总结，显示在页面顶部
- 没有则不显示

**底部日志：**
- LiveLogConsole 组件接入 WebSocket `ws://localhost:8000/ws/tasks/{id}/logs`

### 7. 前端 — Settings 页面串联

- LLM Provider 配置表单接入 `POST /api/settings/llm`
- Test Connection 按钮接入 `POST /api/settings/llm/test`
- AI 全局开关（存在 localStorage 或后端配置）

### 8. 前端 — Dashboard 串联

- 统计卡片调用 `GET /api/projects` 和 `GET /api/tasks` 计算数字
- 最近活动 = 最近的 task 列表
- 工具健康 = `GET /api/tools` 检查在线状态

## 验收标准（端到端 Happy Path）

- [ ] 创建项目 → 添加 Git URL 仓库 → 仓库克隆成功
- [ ] 创建 deepwiki 分析任务 → 任务状态变为 running
- [ ] 任务完成后结果页展示 Markdown 文档
- [ ] 文档中 Mermaid 图表正确渲染为可视图表
- [ ] AI 开启时有 LLM 总结；关闭时无总结且 deepwiki 不可选
- [ ] Tasks 列表页实时更新任务状态
- [ ] WebSocket 日志流正常推送到 LogTerminal
- [ ] Settings 页可配置 LLM Provider 并测试连接
- [ ] Dashboard 展示真实统计数据
- [ ] **deepwiki 容器移除后，任务报错而非静默成功**

# Phase 4C: MR 分析 + 实时日志

**前置依赖：Phase 2A + 2B 完成**
**可与其他 Phase 4 任务并行**

## 任务目标

1. 实现 MR(Merge Request) URL 解析和 diff 文件提取
2. 实现 WebSocket 实时日志流

## Part 1: MR 分析

### 1. Git 服务 — MR Diff 获取 (`backend/app/services/git_service.py`)

支持 GitHub 和 GitLab 的 MR/PR 链接：

```python
async def get_mr_diff(mr_url: str) -> list[str]:
    """解析 MR URL，通过 API 获取变更文件列表"""
    parsed = parse_mr_url(mr_url)

    if parsed.platform == "github":
        # GitHub API: GET /repos/{owner}/{repo}/pulls/{number}/files
        resp = await httpx.get(
            f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/pulls/{parsed.number}/files",
            headers={"Accept": "application/vnd.github.v3+json"}
        )
        files = [f["filename"] for f in resp.json()]
        return files

    elif parsed.platform == "gitlab":
        # GitLab API: GET /projects/{id}/merge_requests/{iid}/changes
        resp = await httpx.get(
            f"{parsed.base_url}/api/v4/projects/{parsed.project_id}/merge_requests/{parsed.iid}/changes"
        )
        files = [c["new_path"] for c in resp.json()["changes"]]
        return files

def parse_mr_url(url: str) -> MRInfo:
    """解析各种格式的 MR URL"""
    # https://github.com/org/repo/pull/123
    # https://gitlab.com/org/repo/-/merge_requests/456
    # 自定义 GitLab 域名
    ...
```

### 2. MR 分析流程

当 `task_type == "mr_diff"` 时：
1. 解析 MR URL 获取变更文件列表
2. 将文件列表设为 `request.target_files`
3. 各 adapter 只分析这些文件（如果 adapter 支持文件级过滤）
4. 不支持文件级过滤的 adapter 仍然全量分析，但结果中标注 MR 相关文件

### 3. 前端 — MR URL 输入

在创建任务 Modal 中，当选择 "MR Diff" 类型时：
- 显示 URL 输入框
- 支持粘贴 GitHub PR / GitLab MR 链接
- 提交后自动解析获取变更文件列表
- 显示变更文件列表供用户确认

---

## Part 2: 实时日志

### 4. 后端 — WebSocket 日志端点 (`backend/app/api/ws.py`)

```python
@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs(websocket: WebSocket, task_id: UUID):
    await websocket.accept()

    try:
        # 1. 获取任务关联的所有 tool_runs
        tool_runs = get_tool_runs(task_id)

        # 2. 创建日志收集器
        log_queue = asyncio.Queue()

        # 3. 为每个运行中的工具启动日志收集
        collectors = []
        for run in tool_runs:
            adapter = get_adapter(run.tool_name)
            task = asyncio.create_task(
                collect_logs(adapter, run.id, log_queue)
            )
            collectors.append(task)

        # 4. 从队列读取并推送到 WebSocket
        while True:
            log_entry = await asyncio.wait_for(log_queue.get(), timeout=30)
            await websocket.send_json({
                "tool": log_entry.tool_name,
                "level": log_entry.level,
                "message": log_entry.message,
                "timestamp": log_entry.timestamp.isoformat(),
            })

    except (WebSocketDisconnect, asyncio.TimeoutError):
        for t in collectors:
            t.cancel()

async def collect_logs(adapter, run_id, queue):
    async for line in adapter.stream_logs(run_id):
        await queue.put(LogEntry(
            tool_name=adapter.name(),
            level=detect_level(line),
            message=line,
            timestamp=datetime.utcnow(),
        ))
```

### 5. 后端 — 日志持久化

同时将日志写入 `task_logs` 表：
```python
async def persist_log(tool_run_id, level, message):
    async with get_session() as session:
        log = TaskLog(tool_run_id=tool_run_id, level=level, message=message)
        session.add(log)
        await session.commit()
```

历史日志查询：
```python
@router.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: UUID, limit: int = 100, offset: int = 0):
    # 从 task_logs 表查询历史日志
    ...
```

### 6. 前端 — LogTerminal 组件串联

将 Phase 2B 中创建的 LogTerminal 组件接入真实 WebSocket：

```typescript
// src/components/tasks/LiveLogConsole.tsx
function LiveLogConsole({ taskId }: { taskId: string }) {
  const [logs, setLogs] = useState<LogEntry[]>([]);

  useEffect(() => {
    const ws = connectTaskLogs(taskId, (log) => {
      setLogs(prev => [...prev, log]);
    });
    return () => ws.close();
  }, [taskId]);

  return <LogTerminal logs={logs} />;
}
```

LogTerminal 颜色编码：
- `[INFO]` → primary-fixed-dim 色
- `[SUCCESS]` → secondary-fixed-dim 色
- `[WARN]` → tertiary-fixed-dim 色
- `[ERROR]` → tertiary 色
- 工具名称前缀用不同颜色区分

### 7. 前端 — 日志导出

任务详情页添加 "Export Logs" 按钮：
- 调用 `GET /api/tasks/{id}/logs?limit=0` 获取全部日志
- 生成 .txt 文件下载

## 验收标准

- [ ] 支持 GitHub PR 链接解析（获取变更文件列表）
- [ ] 支持 GitLab MR 链接解析
- [ ] MR 分析任务只将变更文件传给工具
- [ ] WebSocket 日志流实时推送
- [ ] LogTerminal 正确显示彩色分级日志
- [ ] 工具名称前缀区分不同工具的日志
- [ ] 历史日志可查询
- [ ] 日志可导出为文本文件

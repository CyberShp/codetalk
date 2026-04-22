# Sprint 3: WebSocket 直连 — 实时双向通信

> **前置依赖**: Sprint 1 (上下文联动) ✅, Sprint 1.5 (InsightAsk Deep Research) ✅, Sprint 2 (Deep Research UI) ✅
> **执行者**: Sonnet (编码) → GPT52 (审查)
> **预估改动**: 3 新文件, 3 修改文件

---

## 架构决策（已确认）

| # | 决策 | 理由 |
|---|------|------|
| AD-1 | Backend WS relay：后端暴露 WS 给前端，内部仍用 httpx stream 调 deepwiki HTTP | deepwiki `/chat/completions/stream` 已验证稳定；不在 Sprint 3 耦合其 `/ws/chat`，降低风险 |
| AD-2 | 持久 WS 连接：每个聊天会话一条 WS，Deep Research 多轮续研复用同一条 | 消除每轮重建连接的延迟和开销；简化前端状态管理 |
| AD-3 | 仅 repo-centric 路径：FloatingChat + InsightAskPanel 共用的 `repo_chat` 走 WS | 两个组件已统一到 `api.repos.chat.stream()`；旧的 task-scoped chat 不动 |
| AD-4 | HTTP 兜底：WS 连接失败时透明降级到现有 HTTP fetch | 保证可用性；host-run 开发时 WS 可能被防火墙阻断 |

---

## 步骤

### Step 1: 后端 — WebSocket Chat Relay

**新建文件**: `backend/app/api/ws_chat.py`

```
路由: @router.websocket("/api/repos/{repo_id}/chat/ws")
```

**协议设计**:

```
← Client sends (JSON):
{
  "action": "chat",
  "messages": [{"role": "user", "content": "..."}],
  "file_path": "src/main.py",          // optional
  "included_files": ["a.py", "b.py"],  // optional
  "deep_research": false                // optional
}

→ Server sends (JSON, 逐条):
{ "type": "chunk", "content": "..." }           // 文本片段
{ "type": "done" }                               // 本轮完成
{ "type": "error", "message": "..." }            // 错误
{ "type": "research_round", "round": 2, "max": 5 } // Deep Research 轮次开始（前端可选展示）
```

**实现要点**:

1. `accept()` 后查 DB 获取 repo、LLM config（与 `repo_chat.py` 相同逻辑）
2. 进入 `while True` 消息循环，等待客户端 `action: "chat"` 消息
3. 收到消息后，构建 deepwiki payload（复用 `repo_chat.py` 的 payload 构建逻辑，提取为共享函数）
4. 用 `httpx.AsyncClient.stream("POST", "/chat/completions/stream", json=payload)` 调 deepwiki
5. 逐 chunk 读取 → `send_json({"type": "chunk", "content": chunk})` 发给前端
6. 流结束 → `send_json({"type": "done"})`
7. Deep Research: 如果 `deep_research=True`，后端在同一 WS 连接内自动执行最多 5 轮续研
   - 每轮开始前发 `{"type": "research_round", "round": N, "max": 5}`
   - 每轮的 chunk 照常发送
   - 全部轮次完成后发一个最终 `{"type": "done"}`
8. 异常处理：`httpx.ConnectError` → 发 error 消息，不断开 WS（允许重试）
9. 客户端断开（`WebSocketDisconnect`）→ 清理 httpx client
10. 客户端发 `{"action": "stop"}` → abort 当前 httpx stream（用 cancel scope 或 abort flag）

**共享 payload 构建**:

从 `repo_chat.py` 提取 payload 构建逻辑到 `backend/app/api/_chat_payload.py`（或 `backend/app/services/chat_payload.py`），使 `repo_chat.py` 和 `ws_chat.py` 共用：

```python
async def build_deepwiki_payload(
    repo: Repository,
    messages: list[ChatMessage],
    llm_config: LLMConfig | None,
    *,
    file_path: str | None = None,
    included_files: list[str] | None = None,
    deep_research: bool = False,
) -> dict:
    """构建 deepwiki 请求 payload。"""
    ...
```

### Step 2: 注册路由

**修改文件**: `backend/app/api/router.py`

```python
from app.api.ws_chat import router as ws_chat_router
api_router.include_router(ws_chat_router)
```

### Step 3: 前端 — WebSocket 客户端工具

**新建文件**: `frontend/src/lib/chatWs.ts`

```typescript
export interface ChatWsOptions {
  repoId: string;
  onChunk: (content: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  onResearchRound?: (round: number, max: number) => void;
}

export class ChatWsClient {
  private ws: WebSocket | null = null;
  private options: ChatWsOptions;

  constructor(options: ChatWsOptions) { ... }

  /** 连接到后端 WS。返回 false 表示连接失败（应降级到 HTTP） */
  connect(): Promise<boolean> { ... }

  /** 发送聊天消息 */
  send(params: {
    messages: Array<{ role: string; content: string }>;
    file_path?: string;
    included_files?: string[];
    deep_research?: boolean;
  }): void { ... }

  /** 中止当前流 */
  stop(): void { ... }

  /** 关闭连接 */
  close(): void { ... }

  /** 连接是否存活 */
  get connected(): boolean { ... }
}
```

**实现要点**:

1. `connect()` 构建 URL: `ws://${location.host}/api/repos/${repoId}/chat/ws`
2. 返回 Promise，`onopen` → resolve(true)，`onerror/onclose` → resolve(false)
3. `onmessage` 解析 JSON，分发到对应回调
4. `stop()` 发送 `{"action": "stop"}`
5. 自动重连：不做。连接断开后由调用方决定是否 `connect()` 或降级 HTTP

### Step 4: 前端 — FloatingChat WS 迁移

**修改文件**: `frontend/src/components/ui/FloatingChat.tsx`

改造 `streamSingle` 函数：

1. 组件挂载时（或首次发消息时）创建 `ChatWsClient` 实例，存入 ref
2. `streamSingle` 优先使用 WS：
   - 如果 `wsClientRef.current?.connected`，用 WS 发送
   - 否则尝试 `connect()`，成功则用 WS
   - 连接失败 → 降级到现有 HTTP fetch 逻辑（保留原代码路径）
3. Deep Research 多轮续研：
   - WS 模式下**不需要**前端 while 循环！后端在 WS 内自动完成 5 轮
   - 前端只需监听 `onResearchRound` 更新 UI（轮次 Ribbon）
   - HTTP 降级模式保留现有 while 循环
4. `handleStop`:
   - WS 模式：调 `wsClient.stop()`（发送 `action: stop` 给后端）
   - HTTP 模式：调 `abortController.abort()`（保持现有逻辑）
5. 组件卸载时 `wsClient.close()`

### Step 5: 前端 — InsightAskPanel WS 迁移

**修改文件**: `frontend/src/components/ui/InsightAskPanel.tsx`

与 FloatingChat 相同的迁移模式：

1. `startStreaming` 函数中，优先 WS，失败降级 HTTP
2. Deep Research 续研由后端驱动（WS 模式）或前端循环（HTTP 降级）
3. `onResearchRound` 回调更新阶段 Ribbon UI
4. `handleStop` 分 WS/HTTP 两条路径

### Step 6: Host-run 适配

确保 WS 在 host-run 模式下正常工作：

1. 前端 `chatWs.ts` 的 WS URL 使用 `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL`，host-run 默认落到 `localhost:8000`
2. Next.js 开发代理：如果前端 devServer 代理 API 请求，需确认 WS 升级请求也被代理
   - 检查 `next.config.js` 或 `next.config.ts` 中的 rewrites/proxy 配置
   - 如需要，添加 WS 代理规则
3. 后端 deepwiki URL 已通过 `.env.local` / `settings.deepwiki_base_url` 配置，无需额外改动

---

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `backend/app/api/ws_chat.py` | WebSocket chat relay 端点 |
| 新建 | `backend/app/services/chat_payload.py` | 共享 payload 构建逻辑 |
| 新建 | `frontend/src/lib/chatWs.ts` | WebSocket 客户端工具类 |
| 修改 | `backend/app/api/router.py` | 注册 ws_chat_router |
| 修改 | `backend/app/api/repo_chat.py` | 改用共享 payload 函数 |
| 修改 | `frontend/src/components/ui/FloatingChat.tsx` | WS 优先 + HTTP 降级 |
| 修改 | `frontend/src/components/ui/InsightAskPanel.tsx` | WS 优先 + HTTP 降级 |

---

## 验收标准

- [ ] 后端 WS 端点 `/api/repos/{repo_id}/chat/ws` 可连接、收发消息
- [ ] 前端 FloatingChat 通过 WS 发送问题，实时收到逐 chunk 回复
- [ ] 前端 InsightAskPanel 通过 WS 发送问题，实时收到逐 chunk 回复
- [ ] Deep Research 多轮续研在 WS 模式下自动执行，前端显示轮次 Ribbon
- [ ] WS 连接失败（如手动断开）→ 自动降级到 HTTP，用户无感知
- [ ] `stop` 操作在 WS 模式下正常中止流（无 race condition）
- [ ] Host-run 模式（前端 3005 / 后端 8000）WS 正常工作
- [ ] Docker 模式（前端 3000 / 后端 8000）WS 正常工作
- [ ] `repo_chat.py` 原 HTTP 端点仍正常工作（未被破坏）
- [ ] 无 lint 错误

---

## 注意事项

1. **不要**连接 deepwiki 的 `/ws/chat` 端点 — 本 Sprint 内部仍走 HTTP stream，只是对前端暴露 WS
2. payload 构建逻辑**必须**提取为共享函数，消除 `repo_chat.py` 和 `ws_chat.py` 之间的代码重复
3. Deep Research 的后端自动续研循环需要特别注意：
   - 每轮结束后检查是否需要继续（deepwiki 响应是否包含 `[RESEARCH COMPLETE]` 标记）
   - 如果没有明确停止标记，最多 5 轮后强制结束
   - 每轮之间发 `research_round` 消息让前端更新 UI
4. `stop` 的实现：客户端发 `{"action": "stop"}`，后端收到后需要取消正在进行的 httpx stream
   - 使用 `asyncio.Event` 或 flag 来通知流式读取循环退出
   - 取消后发 `{"type": "done"}` 通知前端本轮结束
5. 前端 WS 客户端的回调必须与组件生命周期正确绑定（ref 而非闭包），避免 stale closure 问题

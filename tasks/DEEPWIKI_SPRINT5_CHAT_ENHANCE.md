# Sprint 5: Chat 增强 — Wiki 并排 + 会话持久化

> **前置依赖**: Sprint 1-4 均已 commit ✅, Phase 5.1 (全屏 AI 问答) ✅ (InsightAskPanel at `/tasks/[id]/ask`)
> **执行者**: Sonnet (编码) → GPT52 (审查)
> **预估改动**: 2 新文件, 7 修改文件

---

## 5.2 Chat + Wiki 并排阅读

### 架构决策

| # | 决策 | 理由 |
|---|------|------|
| AD-1 | 从 FloatingChat 提取 `useChatEngine` 自定义 hook | FloatingChat 有 ~500 行逻辑（WS/HTTP 流式、deep research 自动续研）全部内嵌，无法复用。提取 hook 后，浮动模式和 docked 模式共享同一套引擎 |
| AD-2 | 新增 `ChatPanel` 纯展示组件 | 将消息列表 + 输入框 + deep research 控件的 UI 独立为组件。FloatingChat 和 docked chat 各自包装不同容器 |
| AD-3 | FloatingChat 瘦身为 hook + ChatPanel 的薄包装 | 保持向后兼容，现有调用方无需修改 |
| AD-4 | 文档 tab 使用 `grid-cols-[1fr_420px]` 布局 | 与 graph tab 的 sidebar 模式一致（`grid-cols-[1fr_520px]`），视觉统一 |
| AD-5 | 文档 tab 显示 docked chat 时隐藏 FloatingChat 气泡 | 避免两个 chat 入口同时可见造成混淆 |

### Step 1: 提取 `useChatEngine` hook

**新增文件**: `frontend/src/hooks/useChatEngine.ts`

从 `FloatingChat.tsx` 提取以下逻辑到独立 hook：

```typescript
interface UseChatEngineOptions {
  repoId: string;
  currentPageFilePaths?: string[];
}

interface ChatEngine {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  isStreaming: boolean;
  deepResearch: boolean;
  setDeepResearch: React.Dispatch<React.SetStateAction<boolean>>;
  researchIteration: number;
  isAutoResearching: boolean;
  researchStatus: string;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  inputRef: React.RefObject<HTMLInputElement | null>;
  handleSend: () => Promise<void>;
  handleStop: () => void;
}

export function useChatEngine(options: UseChatEngineOptions): ChatEngine;
```

**提取清单**（从 `FloatingChat.tsx` 剪切）：
1. `Message` 接口定义（lines 10-14）
2. `RESEARCH_STATUS` 常量（lines 22-28）
3. `checkResearchComplete` 函数（lines 30-37）
4. 所有 state 声明：`messages`, `input`, `isStreaming`, `deepResearch`, `researchIteration`, `isAutoResearching`, `researchStatus`（lines 42-54）
5. 所有 ref 声明：`scrollRef`, `abortRef`, `wsClientRef`, `inputRef`, `messagesRef`, `researchIterationRef`, `activeAssistantIdRef`（lines 56-66）
6. 所有 effects：scroll 同步、cleanup（lines 68-89）— **移除** `isOpen` 相关逻辑（那是浮动 UI 专属的）
7. `handleStop` 回调（lines 91-103）
8. `streamSingle` 回调（lines 107-146）
9. `handleSend` 回调（lines 148-345）— 这是核心，整体搬入

**注意**：
- hook 不管 `isOpen` 状态（那是容器的事）
- `Message` 接口和 `RESEARCH_STATUS` 需要 export，供 `ChatPanel` 使用
- `scrollRef` 通过返回值暴露，由 ChatPanel 绑定到滚动容器

### Step 2: 新增 `ChatPanel` 展示组件

**新增文件**: `frontend/src/components/ui/ChatPanel.tsx`

纯 UI 组件，接受 `ChatEngine` 返回值作为 props：

```typescript
import type { ChatEngine } from "@/hooks/useChatEngine";

interface ChatPanelProps {
  engine: ChatEngine;
  /** Container height CSS. Default: "100%". */
  height?: string;
  /** Additional CSS classes for the outer container. */
  className?: string;
}
```

**渲染内容**（从 FloatingChat 提取 UI 部分）：
1. 消息列表（可滚动区域，绑定 `engine.scrollRef`）
2. 每条消息的气泡（Bot/User icon + MarkdownRenderer）
3. 流式加载指示器
4. Deep Research 状态显示（迭代进度、阶段状态文本）
5. 输入框 + 发送按钮 + Deep Research 开关 + 停止按钮
6. 空状态 / 欢迎消息

**不包含**：浮动气泡、展开/收起动画、fixed 定位 — 这些由容器决定。

### Step 3: 瘦身 FloatingChat

**修改文件**: `frontend/src/components/ui/FloatingChat.tsx`

用 `useChatEngine` + `ChatPanel` 重写，保持外部 API 不变：

```typescript
import { useChatEngine } from "@/hooks/useChatEngine";
import ChatPanel from "./ChatPanel";

interface Props {
  repoId: string;
  currentPageFilePaths?: string[];
  /** When true, hide the floating bubble (used when docked chat is active). */
  hidden?: boolean;
}

export default function FloatingChat({ repoId, currentPageFilePaths, hidden }: Props) {
  const engine = useChatEngine({ repoId, currentPageFilePaths });
  const [isOpen, setIsOpen] = useState(false);

  if (hidden) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {isOpen && (
        <div className="... existing panel styling ...">
          <div className="... header with close button ...">
            <button onClick={() => setIsOpen(false)}>...</button>
          </div>
          <ChatPanel engine={engine} />
        </div>
      )}
      {!isOpen && (
        <button onClick={() => setIsOpen(true)}>
          {/* floating bubble */}
        </button>
      )}
    </div>
  );
}
```

**关键改动**：
1. 删除全部 state/ref/callback/effect（现在全在 hook 里）
2. 新增 `hidden` prop — 当 docked chat 可见时由父组件传入
3. 只保留 `isOpen` state 和浮动 UI 壳

### Step 4: 文档 tab 集成 docked chat

**修改文件**: `frontend/src/app/(app)/tasks/[id]/page.tsx`

在文档 tab 区域增加并排 chat 面板：

**新增 state**：
```typescript
const [showDocChat, setShowDocChat] = useState(false);
```

**新增 hook 调用**（仅当文档 tab + showDocChat 时挂载）：
```typescript
// 仅在文档 tab 激活时使用 — ChatPanel 需要引擎实例
const docChatEngine = useChatEngine({
  repoId: task?.repository_id ?? "",
  currentPageFilePaths: wikiPageFilePaths,
});
```

**文档 tab 布局改造**（替换 line 381-385）：
```tsx
{tab === "documentation" && (
  <div className={`grid gap-4 transition-all duration-500 ease-in-out ${
    showDocChat ? "grid-cols-[1fr_420px]" : "grid-cols-1"
  }`}>
    {/* 左侧：Wiki */}
    <GlassPanel className="p-0 overflow-hidden min-w-0">
      <WikiViewer
        taskId={taskId}
        repoId={task?.repository_id ?? undefined}
        onPageChange={handleWikiPageChange}
      />
    </GlassPanel>

    {/* 右侧：Docked Chat */}
    {showDocChat && (
      <GlassPanel className="p-0 overflow-hidden flex flex-col" style={{ height: "calc(100vh - 14rem)" }}>
        <div className="flex items-center justify-between px-4 py-2 border-b border-outline-variant/10">
          <span className="text-xs font-bold uppercase tracking-widest text-on-surface-variant/60">
            AI Chat
          </span>
          <button
            onClick={() => setShowDocChat(false)}
            className="text-on-surface-variant/40 hover:text-on-surface transition-colors"
          >
            <X size={14} />
          </button>
        </div>
        <ChatPanel engine={docChatEngine} className="flex-1" />
      </GlassPanel>
    )}
  </div>
)}
```

**文档 tab 顶部增加 toggle 按钮**（在 "打开全屏 AI 问答" 按钮旁边）：
```tsx
{tab === "documentation" && task.repository_id && (
  <button
    onClick={() => setShowDocChat((v) => !v)}
    className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/10 px-4 py-2 text-[11px] font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/15"
  >
    <MessageSquare size={12} />
    {showDocChat ? "关闭侧边 Chat" : "打开侧边 Chat"}
  </button>
)}
```

**FloatingChat 隐藏逻辑**：
```tsx
{task.repository_id && (
  <FloatingChat
    repoId={task.repository_id}
    currentPageFilePaths={tab === "documentation" ? wikiPageFilePaths : undefined}
    hidden={tab === "documentation" && showDocChat}
  />
)}
```

---

## 5.3 Chat 会话持久化

### 架构决策

| # | 决策 | 理由 |
|---|------|------|
| AD-6 | Model 挂在 `repo_id` 而非 `task_id` | Chat 走 repo-centric 路径（`/api/repos/{repo_id}/chat/stream`），与 task 解耦 |
| AD-7 | `messages` 字段用 JSON 列存储 | 消息量不大（几十条），无需拆表；JSON 列 query 方便 |
| AD-8 | CRUD 端点挂在 `/api/repos/{repo_id}/chat/sessions` | 与现有 repo chat 端点同族 |
| AD-9 | 自动标题：取第一条用户消息前 50 字符 | 简单有效，无需 LLM 起标题 |
| AD-10 | 前端 session list 在 ChatPanel 内展示 | 避免新增独立页面，保持 chat 面板自包含 |

### Step 5: 后端 — ChatSession 模型

**新增文件**: `backend/app/models/chat_session.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID

from app.models import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(200), nullable=True)
    messages = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

**注册到 `__init__.py`**：
```python
from app.models.chat_session import ChatSession  # noqa: E402, F401
```

### Step 6: Alembic 迁移

**新增文件**: `backend/alembic/versions/c3d4e5f6a7b8_add_chat_sessions.py`

```python
"""add chat_sessions table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id",
            UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("messages", JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_sessions")
```

### Step 7: 后端 — CRUD 端点

**修改文件**: `backend/app/api/repo_chat.py`

在现有 `repo_chat_stream` 端点之后新增 session CRUD：

```python
from app.models.chat_session import ChatSession


class ChatSessionCreate(BaseModel):
    title: str | None = None
    messages: list[dict] = []


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None


@router.get("/{repo_id}/chat/sessions")
async def list_chat_sessions(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """List chat sessions for a repository, newest first."""
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.repo_id == repo_id)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "repo_id": str(s.repo_id),
            "title": s.title,
            "message_count": len(s.messages) if s.messages else 0,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in sessions
    ]


@router.get("/{repo_id}/chat/sessions/{session_id}")
async def get_chat_session(
    repo_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a chat session with full messages."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Chat session not found")
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


@router.post("/{repo_id}/chat/sessions")
async def create_chat_session(
    repo_id: uuid.UUID,
    body: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    title = body.title
    if not title and body.messages:
        # Auto-title from first user message
        for msg in body.messages:
            if msg.get("role") == "user":
                title = msg["content"][:50]
                break

    session = ChatSession(
        repo_id=repo_id,
        title=title,
        messages=body.messages,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


@router.put("/{repo_id}/chat/sessions/{session_id}")
async def update_chat_session(
    repo_id: uuid.UUID,
    session_id: uuid.UUID,
    body: ChatSessionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a chat session (title and/or messages)."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Chat session not found")

    if body.title is not None:
        session.title = body.title
    if body.messages is not None:
        session.messages = body.messages

    await db.commit()
    await db.refresh(session)
    return {
        "id": str(session.id),
        "repo_id": str(session.repo_id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


@router.delete("/{repo_id}/chat/sessions/{session_id}")
async def delete_chat_session(
    repo_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session."""
    session = await db.get(ChatSession, session_id)
    if not session or session.repo_id != repo_id:
        raise HTTPException(404, "Chat session not found")
    await db.delete(session)
    await db.commit()
    return {"status": "deleted"}
```

**需要新增 import**：`from sqlalchemy import select`（如未引入）

### Step 8: 前端 — API 客户端方法

**修改文件**: `frontend/src/lib/api.ts`

在 `api.repos.chat` 对象内（`stream` 之后）新增：

```typescript
sessions: {
  list: (repoId: string) =>
    request<{ id: string; repo_id: string; title: string | null; message_count: number; created_at: string; updated_at: string }[]>(
      `/api/repos/${repoId}/chat/sessions`
    ),
  get: (repoId: string, sessionId: string) =>
    request<{ id: string; repo_id: string; title: string | null; messages: { role: string; content: string }[]; created_at: string; updated_at: string }>(
      `/api/repos/${repoId}/chat/sessions/${sessionId}`
    ),
  create: (repoId: string, data: { title?: string; messages: { role: string; content: string }[] }) =>
    request<{ id: string; repo_id: string; title: string | null; messages: { role: string; content: string }[]; created_at: string; updated_at: string }>(
      `/api/repos/${repoId}/chat/sessions`, {
        method: "POST",
        body: JSON.stringify(data),
      }
    ),
  update: (repoId: string, sessionId: string, data: { title?: string; messages?: { role: string; content: string }[] }) =>
    request<{ id: string }>(
      `/api/repos/${repoId}/chat/sessions/${sessionId}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }
    ),
  delete: (repoId: string, sessionId: string) =>
    request<void>(`/api/repos/${repoId}/chat/sessions/${sessionId}`, {
      method: "DELETE",
    }),
},
```

### Step 9: 前端 — ChatPanel 集成 session 管理

**修改文件**: `frontend/src/components/ui/ChatPanel.tsx`（在 Step 2 基础上增强）

在 ChatPanel 头部增加 session 控件：

1. **session 列表下拉**：点击展开，显示该 repo 的历史会话列表（调 `api.repos.chat.sessions.list(repoId)`）
2. **"新建会话" 按钮**：清空 `engine.messages`，重置为欢迎消息
3. **自动保存逻辑**：
   - 用户发送第一条消息时，调 `sessions.create()` 创建 session
   - 后续每条 assistant 回复完成后，调 `sessions.update()` 更新 messages
   - 使用 `useRef<string | null>` 追踪当前 session ID
4. **加载历史会话**：选择某个 session 后，调 `sessions.get()` 获取 messages，写入 `engine.setMessages()`

**ChatPanel props 扩展**：
```typescript
interface ChatPanelProps {
  engine: ChatEngine;
  /** Required for session persistence. Without it, sessions are disabled. */
  repoId?: string;
  height?: string;
  className?: string;
}
```

**session 相关 state**：
```typescript
const [sessions, setSessions] = useState<SessionSummary[]>([]);
const [showSessions, setShowSessions] = useState(false);
const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
const [savingSession, setSavingSession] = useState(false);
```

---

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `frontend/src/hooks/useChatEngine.ts` | Chat 引擎 hook（从 FloatingChat 提取） |
| 新增 | `frontend/src/components/ui/ChatPanel.tsx` | Chat 纯展示组件 + session 管理 UI |
| 修改 | `frontend/src/components/ui/FloatingChat.tsx` | 瘦身为 hook + ChatPanel 的薄包装；新增 `hidden` prop |
| 修改 | `frontend/src/app/(app)/tasks/[id]/page.tsx` | 文档 tab 并排布局 + docked chat toggle |
| 新增 | `backend/app/models/chat_session.py` | ChatSession SQLAlchemy 模型 |
| 新增 | `backend/alembic/versions/c3d4e5f6a7b8_add_chat_sessions.py` | Alembic 迁移 |
| 修改 | `backend/app/models/__init__.py` | 注册 ChatSession |
| 修改 | `backend/app/api/repo_chat.py` | 新增 session CRUD 5 个端点 |
| 修改 | `frontend/src/lib/api.ts` | 新增 `sessions` CRUD 方法 |

---

## 验收标准

### 5.2 Chat + Wiki 并排

- [ ] `useChatEngine` hook 导出 `ChatEngine` 类型，包含 messages/input/send/stop/deepResearch 全套
- [ ] `ChatPanel` 组件渲染消息列表、输入框、deep research 控件
- [ ] `FloatingChat` 瘦身后外部 API 不变（`repoId` + `currentPageFilePaths` props 照常工作）
- [ ] `FloatingChat` 新增 `hidden` prop，传 `true` 时不渲染
- [ ] 文档 tab 显示 "打开侧边 Chat" 按钮，点击后 wiki 和 chat 并排显示
- [ ] 并排布局使用 `grid-cols-[1fr_420px]`，wiki 左 chat 右
- [ ] WikiViewer 的 `onPageChange` 回调仍能正确传递 file paths 到 docked chat
- [ ] Docked chat 可见时 FloatingChat 气泡隐藏
- [ ] 切换到其他 tab 时 docked chat 不影响其他 tab 布局
- [ ] Deep Research（5 轮自动续研 + 阶段 Ribbon）在 docked 模式下正常工作

### 5.3 Chat 会话持久化

- [ ] `chat_sessions` 表通过 alembic upgrade 正确创建
- [ ] `GET /api/repos/{repo_id}/chat/sessions` 返回该 repo 的会话列表（按 updated_at 倒序）
- [ ] `POST /api/repos/{repo_id}/chat/sessions` 创建会话，无 title 时从首条用户消息自动截取
- [ ] `GET /api/repos/{repo_id}/chat/sessions/{id}` 返回完整 messages
- [ ] `PUT /api/repos/{repo_id}/chat/sessions/{id}` 更新 messages 和/或 title
- [ ] `DELETE /api/repos/{repo_id}/chat/sessions/{id}` 删除会话
- [ ] repo_id 不匹配时返回 404
- [ ] repo 删除时级联删除关联的 chat sessions（FK ondelete CASCADE）
- [ ] ChatPanel 头部有 session 列表下拉和 "新建会话" 按钮
- [ ] 用户发第一条消息后自动创建 session
- [ ] assistant 回复完成后自动更新 session messages
- [ ] 从历史会话列表选择一个 session 后，messages 正确加载到 ChatPanel
- [ ] 无 lint 错误

---

## 注意事项

1. **useChatEngine 提取是 Step 1-3 的核心风险**。需要把 FloatingChat ~345 行的 `handleSend` 完整搬入 hook，确保 ref 追踪、WS/HTTP 双通道、deep research 自动续研、错误处理全部正确。建议逐步提取并在每步验证 FloatingChat 的功能不退化
2. **`isOpen` effect 分离**：FloatingChat 原有 `useEffect(() => { if (isOpen && inputRef.current) inputRef.current.focus() }, [isOpen])`。此 effect 是容器级别的，不应进入 hook。FloatingChat 自行保留
3. **scroll effect 移交**：hook 内的 `scrollRef` 需要在 ChatPanel 渲染后绑定到 DOM 元素。ChatPanel 应该 `ref={engine.scrollRef}` 绑到滚动容器
4. **session 自动保存的时机**：在 `handleSend` 的 finally 块里（assistant 流式完成后）触发 save。不要在每个 chunk 时保存
5. **session messages 格式**：存储时去掉 `id` 字段（那是前端生成的 UUID），只存 `{role, content}`
6. **alembic migration 的 `down_revision`** 必须是 `b2c3d4e5f6a7`（当前最新）
7. **repo_chat.py 的 import**：`select` 来自 sqlalchemy，可能已由现有代码导入；`ChatSession` 是新 import

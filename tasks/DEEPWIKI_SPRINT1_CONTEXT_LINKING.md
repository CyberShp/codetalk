# Sprint 1: DeepWiki Chat 上下文联动

> 前置：无
> 预期工时：1 天
> 架构设计：宪宪/Opus-46
> 编码：@sonnet
> 视觉审查：@gemini25
> Code Review 门禁：@gpt52

## 目标

打通 WikiViewer → FloatingChat → deepwiki 的上下文链路，让 deepwiki 收到用户当前浏览的 wiki 页面关联文件，RAG 检索精准聚焦。

**一句话**：用户在 wiki 文档 tab 浏览某个页面时，右下角浮窗聊天自动携带该页面的 `filePaths`，deepwiki 的回答质量立即提升。

## 当前状态

| 组件 | 现状 | 问题 |
|---|---|---|
| `repo_chat.py` | 已支持 `file_path`, `included_files`, `deep_research`, `type: "local"` | ✅ 后端 ready |
| `api.repos.chat.stream()` | 已支持 `filePath`, `deepResearch`, `includedFiles` | ✅ 前端 API ready |
| `FloatingChat.tsx` | **死代码**，无任何页面 import | ❌ 需要复活并迁移到 repo-centric |
| `WikiViewer.tsx` | 无 `onPageChange` 回调 | ❌ 无法向外传递当前页面信息 |
| `tasks/[id]/page.tsx` | 无 FloatingChat，无 wiki 上下文 state | ❌ 需要接线 |

## 实施步骤

### Step 1：WikiViewer 暴露当前页面上下文

**文件**：`frontend/src/components/ui/WikiViewer.tsx`

**改动**：

1. 扩展 `WikiViewerProps` 接口：

```typescript
interface WikiViewerProps {
  taskId: string;
  standalone?: boolean;
  onPageChange?: (pageId: string, filePaths: string[]) => void;  // 新增
}
```

2. 在 `handlePageSelect` 中触发回调：

```typescript
const handlePageSelect = (pageId: string, hash?: string) => {
  setCurrentPageId(pageId);
  // 新增：通知父组件当前页面的文件路径
  const page = wiki?.generated_pages[pageId];
  if (page && onPageChange) {
    onPageChange(pageId, page.filePaths);
  }
  // ... 原有滚动逻辑不变
};
```

3. 在初始加载设置 `currentPageId` 后也触发回调。找到 `setCurrentPageId((prev) => { ... })` 的地方（`loadWiki` 函数中），在其后添加 effect 或在 `useEffect` 中监听 `currentPageId` 变化：

```typescript
// 在 currentPageId 变化时通知父组件
useEffect(() => {
  if (currentPageId && wiki && onPageChange) {
    const page = wiki.generated_pages[currentPageId];
    if (page) {
      onPageChange(currentPageId, page.filePaths);
    }
  }
}, [currentPageId, wiki, onPageChange]);
```

**注意**：`onPageChange` 放在 deps 中可能导致循环，如果父组件没有 useCallback 包裹。用 ref 保存回调更安全：

```typescript
const onPageChangeRef = useRef(onPageChange);
onPageChangeRef.current = onPageChange;

useEffect(() => {
  if (currentPageId && wiki) {
    const page = wiki.generated_pages[currentPageId];
    if (page) {
      onPageChangeRef.current?.(currentPageId, page.filePaths);
    }
  }
}, [currentPageId, wiki]);
```

### Step 2：FloatingChat 迁移到 repo-centric

**文件**：`frontend/src/components/ui/FloatingChat.tsx`

**改动**：

1. Props 接口改为：

```typescript
interface Props {
  repoId: string;
  currentPageFilePaths?: string[];  // 当前 wiki 页面关联的文件
}
```

2. `handleSend` 中替换 API 调用：

**现在**（第 84 行）：
```typescript
const response = await api.chat.stream(taskId, history, controller.signal);
```

**改为**：
```typescript
const response = await api.repos.chat.stream(
  repoId,
  history,
  {
    includedFiles: currentPageFilePaths,
  },
  controller.signal,
);
```

3. header 区域添加上下文指示（可选，视觉设计由 @gemini25 确认）：

在 header 的 `SYNCING_CONTEXT...` 处改为动态显示：

```typescript
<span className="text-[9px] font-mono text-on-surface-variant/40 hidden sm:block italic">
  {currentPageFilePaths?.length
    ? `${currentPageFilePaths.length} files in scope`
    : "GLOBAL_CONTEXT"}
</span>
```

### Step 3：任务详情页接线

**文件**：`frontend/src/app/(app)/tasks/[id]/page.tsx`

**改动**：

1. 新增 import：

```typescript
import FloatingChat from "@/components/ui/FloatingChat";
```

2. 新增 state（在 component 内部，约第 75 行后）：

```typescript
const [wikiPageFilePaths, setWikiPageFilePaths] = useState<string[]>([]);
```

3. WikiViewer 传入回调（约第 376 行）：

**现在**：
```tsx
<WikiViewer taskId={taskId} />
```

**改为**：
```tsx
<WikiViewer
  taskId={taskId}
  onPageChange={useCallback((_pageId: string, filePaths: string[]) => {
    setWikiPageFilePaths(filePaths);
  }, [])}
/>
```

> 注意：useCallback 需要添加到顶部 import 中（已有）。

4. 在页面底部、`</div>` 和 `<ConfirmDialog>` 之间添加 FloatingChat：

```tsx
{task.repository_id && (
  <FloatingChat
    repoId={task.repository_id}
    currentPageFilePaths={tab === "documentation" ? wikiPageFilePaths : undefined}
  />
)}
```

**关键细节**：只在 `documentation` tab 时传递 wiki 文件上下文。其他 tab 时传 `undefined`（全局上下文）。

### Step 4：清理 legacy chat.py 的 type 字段（低优先级）

**文件**：`backend/app/api/chat.py`

在 `chat_stream` 函数的 payload 构建处（约第 587 行），补上 `type: "local"`：

```python
payload: dict = {
    "repo_url": repo_path,
    "type": "local",          # 补上：CodeTalk 使用本地路径
    "messages": messages,
    "language": "zh",
}
```

这是个小修复，确保即使通过 legacy 路径也能正确告知 deepwiki 仓库类型。

## 验收标准

1. ✅ 用户在 Wiki 文档 tab 浏览页面时，右下角出现 FloatingChat 浮窗按钮
2. ✅ 切换 wiki 页面后，FloatingChat 发送的请求 payload 中 `included_files` 包含当前页面的 `filePaths`
3. ✅ 在非 documentation tab 下，FloatingChat 的 `included_files` 为空（全局上下文）
4. ✅ FloatingChat header 显示当前 scope 信息（文件数或 GLOBAL）
5. ✅ 网络请求可在 DevTools 中验证 payload 包含 `included_files` 和 `type: "local"`
6. ✅ 后端 `repo_chat.py` 日志显示收到 `included_files` 参数

## 不做什么

- ❌ 不改 InsightAskPanel（Sprint 1.5 单独处理，需要 repo-centric ask/context 端点）
- ❌ 不做 Deep Research UI（Sprint 2）
- ❌ 不做 WebSocket（Phase 3，backlog）
- ❌ 不做对话持久化（Phase 5，backlog）
- ❌ 不改 WikiViewer 的 UI 布局

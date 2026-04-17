# Sprint 1.5: InsightAskPanel → Repo-Centric + Deep Research

> 前置：Sprint 1（ef1e89d）+ Sprint 2（65cd07c）
> 预期工时：1 天
> 架构设计：宪宪/Opus-46
> 视觉设计：沿用 Sprint 2 视觉规范（烁烁/Gemini-25 已审核）
> 编码：@sonnet
> Code Review 门禁：@gpt52

## 目标

将全屏 AI 问答面板 `InsightAskPanel` 迁移到 repo-centric API，并移植 FloatingChat 已实现的 Deep Research 多轮研究 UI。用户在 `/tasks/[id]/ask` 页面也能启用 5 轮自动迭代深度研究。

## 当前状态

| 组件 | 现状 | 问题 |
|---|---|---|
| `InsightAskPanel.tsx` | 使用 `api.chat.stream(taskId, ...)` | ❌ task-scoped，不支持 `deepResearch` |
| `api.chat.askContext()` | task-scoped Zoekt 证据检索 | ✅ 保留（仍需要 taskId 查找 repo） |
| `api.repos.chat.stream()` | repo-centric，支持 `deepResearch` | ✅ 后端 ready |
| `FloatingChat.tsx` | Deep Research UI 已完整实现 | ✅ 参考实现 |
| `ask/page.tsx` | 只传 `taskId` 给 InsightAskPanel | ❌ 需要传 `repoId` |

## 架构决策

### Evidence + Repo-Centric 的衔接

**问题**：`api.repos.chat.stream()` 后端（`repo_chat.py`）不接受 `evidence` 参数。但 InsightAskPanel 的 Ask 流程需要将 Zoekt 证据注入 deepwiki 上下文。

**方案**：零后端改动。前端在构建 `messages` 数组时，将 evidence 格式化为 system message 前置。`repo_chat.py` 原样转发 messages 给 deepwiki。

```typescript
// 前端 evidence → system message 注入（纯格式转换，非分析逻辑）
function formatEvidenceAsSystem(evidence: Evidence[]): string {
  if (!evidence.length) return "";
  const parts = ["[代码证据 — 请在回答中使用 [1] [2] 等标记引用对应来源]\n"];
  evidence.forEach((ev, i) => {
    const loc = ev.type === "code"
      ? `${ev.file || ev.title}${ev.line_range ? ` (L${ev.line_range})` : ""}`
      : `文档: ${ev.title}`;
    parts.push(`[${i + 1}] ${loc}`);
    parts.push(ev.content);
    parts.push("");
  });
  return parts.join("\n");
}
```

### Deep Research + Evidence 的配合

- **Round 1**：`askContext` 获取 evidence → evidence 注入 system message → `deepResearch: true` 发送
- **Round 2-5**：auto-continue，不再重新搜索 evidence。deepwiki 内部管理上下文
- **Evidence sidecar**：始终显示 Round 1 的 evidence，不因续研而清空

## 实施步骤

### Step 1：Ask 页面传入 repoId

**文件**：`frontend/src/app/(app)/tasks/[id]/ask/page.tsx`

**改动**：将 `task.repository_id` 传给 InsightAskPanel。

```tsx
// 现在（第 67 行）：
<InsightAskPanel taskId={taskId} className="h-full" />

// 改为：
<InsightAskPanel
  taskId={taskId}
  repoId={task?.repository_id}
  className="h-full"
/>
```

注意 `task` 可能尚未加载（`null`），InsightAskPanel 内部需要处理 `repoId` 为 `undefined` 的情况（此时 DEEP toggle disabled）。

### Step 2：InsightAskPanel Props + State 扩展

**文件**：`frontend/src/components/ui/InsightAskPanel.tsx`

**Props 改为**：

```typescript
interface Props {
  taskId: string;
  repoId?: string;  // 新增：repo-centric 聊天需要
  className?: string;
}
```

**新增 state**（在现有 state 声明后）：

```typescript
const [deepResearch, setDeepResearch] = useState(false);
const [researchIteration, setResearchIteration] = useState(0);
const [isAutoResearching, setIsAutoResearching] = useState(false);
const [researchStatus, setResearchStatus] = useState("");
```

**新增 ref**：

```typescript
const researchIterationRef = useRef(0);
const activeAssistantIdRef = useRef<string | null>(null);
```

### Step 3：Evidence → System Message 辅助函数

**位置**：`InsightAskPanel.tsx` 文件顶部，`preprocessCitations` 函数附近。

```typescript
function formatEvidenceAsSystem(evidence: Evidence[]): string {
  if (!evidence.length) return "";
  const parts = ["[代码证据 — 请在回答中使用 [1] [2] 等标记引用对应来源]\n"];
  evidence.forEach((ev, i) => {
    const loc = ev.type === "code"
      ? `${ev.file || ev.title}${ev.line_range ? ` (L${ev.line_range})` : ""}`
      : `文档: ${ev.title}`;
    parts.push(`[${i + 1}] ${loc}`);
    parts.push(ev.content);
    parts.push("");
  });
  return parts.join("\n");
}

function checkResearchComplete(text: string): boolean {
  return (
    text.includes("## Final Conclusion") ||
    text.includes("## 最终结论") ||
    text.includes("# Final Conclusion") ||
    text.includes("# 最终结论")
  );
}

const RESEARCH_STATUS: Record<number, string> = {
  1: ">> ANALYZING_STRUCTURE...",
  2: ">> LINKING_CONTEXT...",
  3: ">> DEEP_INSPECTION...",
  4: ">> CROSS_REFERENCING...",
  5: ">> SYNTHESIZING...",
};
```

### Step 4：迁移 startStreaming 到 repo-centric

**改动**：`startStreaming` 改为使用 `api.repos.chat.stream()`。

```typescript
const startStreaming = useCallback(async (
  id: string,
  history: { role: string; content: string }[],
  evidence: Evidence[],
  prompt: string,
  drFlag: boolean,  // 新增
) => {
  setMessages((prev) => [...prev, { id, role: "assistant", content: "", evidence, prompt }]);
  setActiveAnswerId(id);
  activeAssistantIdRef.current = id;

  const controller = abortRef.current!;

  try {
    // repo-centric API — 支持 deepResearch
    const response = await api.repos.chat.stream(
      repoId!,
      history,
      { deepResearch: drFlag },
      controller.signal,
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let fullText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      fullText += text;
      setMessages((prev) => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last.id === id) {
          msgs[msgs.length - 1] = { ...last, content: last.content + text };
        }
        return msgs;
      });
    }
    return fullText;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      setMessages((prev) => prev.filter((msg) => msg.id !== id || msg.content.length > 0));
    } else {
      setMessages((prev) => {
        const msgs = [...prev];
        const target = msgs.find((m) => m.id === id);
        if (target && !target.content) {
          target.content = "> ⚠️ 检索或生成失败，请重试。";
        }
        return msgs;
      });
    }
    throw err; // re-throw for handleSend to catch
  }
}, [repoId]);
```

**关键变化**：
- 返回值从 `void` → `string`（fullText），供 auto-continue 判断
- 使用 `api.repos.chat.stream()` 替代 `api.chat.stream()`
- 新增 `drFlag` 参数
- 不再在 catch 中 `setIsStreaming(false)`，由 handleSend 的 finally 统一管理
- re-throw error 让 handleSend 的 catch 处理

### Step 5：改造 handleSend 支持 Deep Research + Auto-Continue

```typescript
const handleSend = useCallback(async () => {
  if (!input.trim() || isStreaming || isSearching || isAutoResearching) return;
  if (deepResearch && !repoId) return; // repoId 必须存在

  const query = input.trim();
  const userMsg: Message = { id: Date.now().toString(), role: "user", content: query };
  const assistantId = (Date.now() + 1).toString();

  // 重置研究状态
  researchIterationRef.current = 0;
  setResearchIteration(0);
  setIsAutoResearching(false);
  setResearchStatus("");

  const controller = new AbortController();
  abortRef.current = controller;

  // Phase 1: Zoekt 证据检索（不变）
  setMessages((prev) => [...prev, userMsg]);
  setInput("");
  setIsSearching(true);
  setCurrentEvidence([]);

  let evidence: Evidence[] = [];
  try {
    const ctx = await api.chat.askContext(taskId, query, controller.signal);
    if (controller.signal.aborted) return;
    evidence = (ctx.evidence ?? []).map((e, i) => ({ ...e, id: `ev-${i}` }));
    setCurrentEvidence(evidence);
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return;
  } finally {
    setIsSearching(false);
  }

  if (controller.signal.aborted) return;

  // Phase 2: 构建 history（注入 evidence 作为 system message）
  const evidenceSystem = formatEvidenceAsSystem(evidence);
  const baseHistory: { role: string; content: string }[] = [];
  if (evidenceSystem) {
    baseHistory.push({ role: "system", content: evidenceSystem });
  }
  baseHistory.push(
    ...messagesRef.current.filter((m) => m.content).map((m) => ({
      role: m.role,
      content: m.content,
    })),
    { role: "user", content: query },
  );

  setIsStreaming(true);

  // Deep Research 初始化
  if (deepResearch) {
    researchIterationRef.current = 1;
    setResearchIteration(1);
    setResearchStatus(RESEARCH_STATUS[1]);
  }

  try {
    // 选择 API 路径：有 repoId 用 repo-centric，否则 fallback task-scoped
    let fullText: string;
    if (repoId) {
      fullText = await startStreaming(assistantId, baseHistory, evidence, query, deepResearch) ?? "";
    } else {
      // fallback：task-scoped（不支持 deep research）
      await startStreamingLegacy(assistantId, baseHistory, evidence, query);
      return;
    }

    // Auto-continue 逻辑（与 FloatingChat.tsx 相同模式）
    let accHistory = [...baseHistory, { role: "assistant", content: fullText }];

    if (deepResearch) {
      let lastResponse = fullText;

      while (
        !checkResearchComplete(lastResponse) &&
        researchIterationRef.current < 5
      ) {
        if (controller.signal.aborted) break;

        const nextIter = researchIterationRef.current + 1;
        researchIterationRef.current = nextIter;
        setResearchIteration(nextIter);
        setResearchStatus(RESEARCH_STATUS[nextIter] ?? ">> PROCESSING...");
        setIsAutoResearching(true);

        await new Promise<void>((r) => setTimeout(r, 1000));
        if (controller.signal.aborted) break;

        accHistory = [
          ...accHistory,
          { role: "user", content: "Continue the research" },
        ];

        const continueId = `continue-${Date.now()}`;
        activeAssistantIdRef.current = continueId;
        setMessages((prev) => [
          ...prev,
          { id: continueId, role: "assistant", content: "", evidence: [], prompt: query },
        ]);

        const continueResponse = await api.repos.chat.stream(
          repoId,
          accHistory,
          { deepResearch: true },
          controller.signal,
        );
        if (!continueResponse.ok) break;

        const reader = continueResponse.body!.getReader();
        const decoder = new TextDecoder();
        lastResponse = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          lastResponse += chunk;
          setMessages((prev) => {
            const msgs = [...prev];
            const target = msgs.find((m) => m.id === continueId);
            if (target) {
              return msgs.map((m) =>
                m.id === continueId ? { ...m, content: m.content + chunk } : m
              );
            }
            return msgs;
          });
        }

        accHistory = [...accHistory, { role: "assistant", content: lastResponse }];
      }
    }
  } catch (e) {
    const targetId = activeAssistantIdRef.current;
    if ((e as Error).name === "AbortError") {
      if (targetId) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === targetId && !m.content
              ? { ...m, content: "> 已停止生成。" }
              : m
          ),
        );
      }
      return;
    }
    // 非 AbortError 的错误已在 startStreaming 的 catch 中处理了 UI
  } finally {
    setIsStreaming(false);
    setIsAutoResearching(false);
    setResearchStatus("");
    if (abortRef.current?.signal.aborted) {
      abortRef.current = null;
    }
  }
}, [input, isStreaming, isSearching, isAutoResearching, taskId, repoId, deepResearch, startStreaming]);
```

**重要**：保留原有的 `startStreaming` 重命名为 `startStreamingLegacy`，作为没有 `repoId` 时的 fallback。或者更简单地，如果 `repoId` 不存在就禁用 Deep Research toggle。

**编码者注意**：上面的伪代码是架构指引，不是可直接粘贴的代码。请阅读现有的 `handleSend` 和 `startStreaming`，理解当前流程后再整合。特别注意：

1. `startStreaming` 的 error handling 需要重构 — 当前在 catch 中自行处理，需要改为 re-throw 让 handleSend 统一处理
2. `isStreaming` 的设置从 `startStreaming.finally` 移到 `handleSend.finally`
3. 如果 `repoId` 不存在，完全走原来的 task-scoped 路径，不做任何改动

### Step 6：handleStop 修复

**沿用 Sprint 2 的 race condition fix**：`handleStop` 只调用 `abort()`，不触碰任何 state。

```typescript
const handleStop = useCallback(() => {
  abortRef.current?.abort();
  // 不在这里 setIsStreaming / setIsSearching — 由 handleSend 的 finally 负责
}, []);
```

### Step 7：DEEP Toggle UI

**位置**：Search Header 的按钮区域，停止/提问按钮左侧。

```tsx
{/* DEEP Research Toggle — 在提问/停止按钮左侧 */}
<div className="flex gap-2">
  {repoId && (
    <button
      onClick={() => setDeepResearch((v) => !v)}
      disabled={isStreaming || isSearching || isAutoResearching}
      className={`h-10 px-3 rounded-xl text-[10px] font-mono tracking-wider transition-all duration-300 shrink-0 border disabled:opacity-30 ${
        deepResearch
          ? "bg-primary/15 text-primary border-primary/30 shadow-[0_0_12px_rgba(164,230,255,0.15)]"
          : "bg-white/[0.03] text-on-surface-variant/40 border-white/10 hover:text-on-surface-variant/70 hover:border-white/20"
      }`}
      title="深度研究模式：自动进行 5 轮迭代深入分析"
    >
      DEEP
    </button>
  )}

  {(isStreaming || isSearching || isAutoResearching) ? (
    <button onClick={handleStop} ...>停止</button>
  ) : (
    <button onClick={handleSend} ...>提问</button>
  )}
</div>
```

### Step 8：Research Phase Ribbon

**位置**：Search Header（`sticky top-0` 区域）和内容区域之间。

```tsx
{/* Research Phase Ribbon — 仅在深度研究进行中显示 */}
{deepResearch && researchIteration > 0 && (
  <div className="flex h-1 shrink-0">
    {(["Plan", "R1", "R2", "R3", "Done"] as const).map((label, i) => (
      <div
        key={label}
        className={`flex-1 transition-all duration-500 ${
          i < researchIteration - 1
            ? "bg-gradient-to-r from-primary to-secondary"
            : i === researchIteration - 1
              ? "bg-primary/50 animate-pulse"
              : "bg-white/5"
        }`}
        title={`${label}${
          i < researchIteration - 1 ? " ✓"
            : i === researchIteration - 1 ? " (进行中)"
            : ""
        }`}
      />
    ))}
  </div>
)}
```

### Step 9：Auto-Research Status Bar

**位置**：底部 Status Bar 上方（或替换当前 Status Bar 内容）。

```tsx
{/* 自动续研状态 — Status Bar 上方 */}
{isAutoResearching && (
  <div className="border-t border-white/5 bg-black/20 px-6 py-2">
    <span className="text-[9px] font-mono text-primary/70 tracking-widest">
      {researchStatus}
    </span>
    <div className="mt-1.5 h-0.5 rounded-full bg-primary/10 overflow-hidden">
      <div
        className="h-full bg-gradient-to-r from-primary to-secondary rounded-full"
        style={{
          animation: "breathe 2s ease-in-out infinite",
          transformOrigin: "left",
        }}
      />
    </div>
  </div>
)}
```

在组件顶部添加 breathe keyframes（与 FloatingChat 相同）：

```tsx
{/* breathe keyframes */}
<style>{`
  @keyframes breathe {
    0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
    50%       { transform: scaleX(1);   opacity: 1;   }
  }
`}</style>
```

### Step 10：Conclusion Highlighting (Halo Conclusion)

**位置**：assistant 消息渲染区域。检测包含结论标记的消息，添加 primary glow。

在 assistant 消息的外层 `<div>` 上添加条件样式：

```tsx
{/* 结论高亮 — 检测 Final Conclusion 标记 */}
<div
  key={m.id}
  className={`group animate-in fade-in duration-1000 ${
    m.role === "assistant" && checkResearchComplete(m.content)
      ? "ring-1 ring-primary/20 rounded-2xl p-4 bg-primary/[0.02] shadow-[0_0_30px_rgba(164,230,255,0.06)]"
      : ""
  }`}
>
```

### Step 11：Status Bar 增强

底部 Status Bar 反映 Deep Research 状态：

```tsx
<div className={`w-1 h-1 rounded-full ${
  isAutoResearching ? "bg-primary animate-pulse"
    : isStreaming || isSearching ? "bg-primary animate-pulse"
    : "bg-green-500/50"
}`} />
{isAutoResearching
  ? `深度研究 ${researchIteration}/5 轮`
  : isSearching ? "检索代码证据..."
  : isStreaming ? "深度推理中..."
  : "系统就绪"}
```

## 验收标准

1. ✅ `/tasks/[id]/ask` 页面的 InsightAskPanel 输入栏旁出现 DEEP toggle 按钮
2. ✅ 有 `repoId` 时 DEEP 按钮可用，无 `repoId` 时不渲染
3. ✅ 非 Deep Research 模式下，行为与现有完全一致（regression-safe）
4. ✅ Deep Research 模式：API 调用切换到 `api.repos.chat.stream(repoId, ...)`
5. ✅ Evidence 以 system message 注入到 messages 数组第一条
6. ✅ 第一轮响应完成后自动检测结论标记，未完成则 auto-continue
7. ✅ 续研消息 "Continue the research" 不显示在 UI 中
8. ✅ Research Phase Ribbon 正确显示 1-5 轮进度
9. ✅ Auto-research 时显示呼吸光带 + 状态文字
10. ✅ 第 5 轮或检测到结论标记时停止
11. ✅ 结论消息有 primary glow 视觉区分
12. ✅ streaming / searching / autoResearching 时 toggle 和输入框 disabled
13. ✅ 用户可在任意时刻点停止中断续研
14. ✅ Evidence sidecar 始终显示 Round 1 的证据，不因续研而变化

## 不做什么

- ❌ 不改后端（零后端改动！evidence 在前端格式化为 system message）
- ❌ 不改 FloatingChat（已完成，独立组件）
- ❌ 不做对话持久化
- ❌ 不做 WebSocket
- ❌ 不改 `askContext` 端点（保持 task-scoped）

## 关键约束

- `handleStop` 只调用 `abort()` — 与 Sprint 2 的 race condition fix 保持一致
- `researchIteration` 必须用 ref 跟踪异步状态
- 续研消息 "Continue the research" 不作为 user Message 显示在 UI
- Evidence sidecar 在续研期间保持显示 Round 1 的证据
- `repoId` 为 `undefined` 时完全走原有 task-scoped 路径，不触发任何 Deep Research 逻辑
- `formatEvidenceAsSystem` 是纯格式转换（字符串拼接），符合铁律

## 参考实现

- `FloatingChat.tsx`：Deep Research UI + auto-continue 逻辑的参考实现
- `repo_chat.py:68-70`：`[DEEP RESEARCH]` 标记注入
- `chat.py:473-491`：`_format_evidence_as_context()` — 前端 `formatEvidenceAsSystem` 的对照

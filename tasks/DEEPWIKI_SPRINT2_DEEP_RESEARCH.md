# Sprint 2: Deep Research 多轮研究 UI

> 前置：Sprint 1（已合入 ef1e89d）
> 预期工时：2 天
> 架构设计：宪宪/Opus-46
> 视觉设计：烁烁/Gemini-25（已审核）
> 编码：@sonnet
> Code Review 门禁：@gpt52

## 目标

在 FloatingChat 中实现 DeepWiki 的多轮深度研究能力：用户开启「深度研究」后，系统自动进行最多 5 轮迭代（计划 → 研究 1-3 → 结论），前端自动续研并展示阶段导航。

## DeepWiki Deep Research 机制（后端已支持）

1. 用户消息包含 `[DEEP RESEARCH]` 标记时激活
2. deepwiki 内部自动计数 `research_iteration`（统计 assistant 消息数量）
3. 根据迭代次数选择不同的 system prompt：
   - 第 1 轮：制定研究计划
   - 第 2-4 轮：逐步深入研究
   - 第 5 轮：综合结论（输出包含 `## Final Conclusion` 或 `## 最终结论`）
4. 每轮结束后，前端发送 `"Continue the research"` 触发下一轮
5. deepwiki 会自动将 `"Continue the research"` 替换为原始问题

**后端已 ready**：`repo_chat.py:68-70` 已实现 `[DEEP RESEARCH]` 标记注入。前端 `api.repos.chat.stream()` 已支持 `deepResearch` 参数。

## 实施步骤

### Step 1：新增状态和类型

**文件**：`frontend/src/components/ui/FloatingChat.tsx`

在现有 state 声明后（约第 32 行后）添加：

```typescript
const [deepResearch, setDeepResearch] = useState(false);
const [researchIteration, setResearchIteration] = useState(0);
const [isAutoResearching, setIsAutoResearching] = useState(false);
const [researchStatus, setResearchStatus] = useState(""); // 当前研究状态文案
```

### Step 2：Deep Research Toggle

**位置**：输入框右侧、发送按钮左侧（`[input] [DEEP toggle] [Send/Stop]`）

**视觉规范**（烁烁/Gemini-25 设计，宪宪简化审核后）：

```tsx
{/* Deep Research Toggle — 在 input 和 send button 之间 */}
<button
  onClick={() => setDeepResearch(!deepResearch)}
  disabled={isStreaming || isAutoResearching}
  className={`h-9 px-2.5 rounded-md text-[9px] font-mono tracking-wider transition-all duration-300 shrink-0
    ${deepResearch
      ? "bg-primary/15 text-primary shadow-[0_0_12px_rgba(164,230,255,0.15)]"
      : "bg-white/[0.03] text-on-surface-variant/40 hover:text-on-surface-variant/70"
    } disabled:opacity-30`}
  title="深度研究模式：自动进行 5 轮迭代深入分析"
>
  DEEP
</button>
```

### Step 3：Research Phase Ribbon

**位置**：header 下方，仅在 `deepResearch && researchIteration > 0` 时渲染。

**视觉规范**：4px 高光感细线，5 段（Plan / R1 / R2 / R3 / Conclusion）。

```tsx
{/* Research Phase Ribbon — header 和 messages 之间 */}
{deepResearch && researchIteration > 0 && (
  <div className="flex h-1 shrink-0">
    {["Plan", "R1", "R2", "R3", "Done"].map((label, i) => (
      <div
        key={label}
        className={`flex-1 transition-all duration-500 ${
          i < researchIteration
            ? "bg-gradient-to-r from-primary to-secondary"
            : i === researchIteration
              ? "bg-primary/50 animate-pulse"
              : "bg-white/5"
        }`}
        title={`${label}${i < researchIteration ? " ✓" : i === researchIteration ? " (进行中)" : ""}`}
      />
    ))}
  </div>
)}
```

### Step 4：修改 handleSend 支持 Deep Research

核心改动在 `handleSend` 中，需要：

1. 发送时传递 `deepResearch` 参数
2. 流式完成后检测是否需要自动续研
3. 自动续研最多 5 轮

```typescript
const handleSend = useCallback(async () => {
  if (!input.trim() || isStreaming || isAutoResearching) return;

  const userContent = input.trim();
  // ... 现有 userMsg / assistantId / setMessages 逻辑不变

  // 发送时如果是 deep research，重置迭代计数
  if (deepResearch) {
    setResearchIteration(1);
    setResearchStatus(">> ANALYZING_STRUCTURE...");
  }

  // ... 现有 history 构建不变

  try {
    const controller = new AbortController();
    abortRef.current = controller;

    const response = await api.repos.chat.stream(
      repoId,
      history,
      {
        includedFiles: currentPageFilePaths,
        deepResearch,  // 新增：传递 deep research 标记
      },
      controller.signal,
    );

    // ... 现有流式读取逻辑不变，但需要收集完整响应文本

    // 流式完成后，检测是否需要续研
    // （见 Step 5 的 autoResearchContinue）

  } catch (e) {
    // ... 现有错误处理不变
  }
}, [input, isStreaming, isAutoResearching, messages, repoId, currentPageFilePaths, deepResearch]);
```

### Step 5：自动续研逻辑

这是 Sprint 2 最核心的逻辑。在流式响应完成后：

```typescript
// 在 handleSend 的 while(true) 循环结束后添加
// （或提取为独立函数 autoResearchContinue）

// 收集完整响应文本 — 在流式循环中累积
let fullResponse = "";
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const text = decoder.decode(value, { stream: true });
  fullResponse += text;
  // ... 现有 setMessages 更新逻辑
}

// Deep Research 自动续研判断
if (deepResearch && !isResearchComplete(fullResponse) && researchIterationRef.current < 5) {
  setIsAutoResearching(true);
  const nextIteration = researchIterationRef.current + 1;
  researchIterationRef.current = nextIteration;
  setResearchIteration(nextIteration);

  // 状态文案
  const statusTexts = [
    "", // 0
    ">> ANALYZING_STRUCTURE...",  // 1
    ">> LINKING_CONTEXT...",      // 2
    ">> DEEP_INSPECTION...",      // 3
    ">> CROSS_REFERENCING...",    // 4
    ">> SYNTHESIZING...",         // 5
  ];
  setResearchStatus(statusTexts[nextIteration] || ">> PROCESSING...");

  // 延迟 1 秒后自动发送续研消息
  await new Promise(r => setTimeout(r, 1000));

  // 构建续研消息（不显示给用户，只发送给 deepwiki）
  const continueHistory = [
    ...messagesRef.current
      .filter(m => m.id !== "welcome")
      .map(m => ({ role: m.role, content: m.content })),
    { role: "user" as const, content: "Continue the research" },
  ];

  // 新增 assistant placeholder
  const continueAssistantId = Date.now().toString();
  setMessages(prev => [
    ...prev,
    { id: continueAssistantId, role: "assistant" as const, content: "" },
  ]);

  // 再次发送流式请求（不携带 [DEEP RESEARCH] — deepwiki 内部已在追踪迭代）
  const continueResponse = await api.repos.chat.stream(
    repoId,
    continueHistory,
    { includedFiles: currentPageFilePaths, deepResearch: true },
    controller.signal,
  );

  // 流式读取续研响应...（同样的 reader 逻辑）
  // 递归检测是否需要再续...
}

// 辅助函数
function isResearchComplete(text: string): boolean {
  return text.includes("## Final Conclusion")
    || text.includes("## 最终结论")
    || text.includes("# Final Conclusion")
    || text.includes("# 最终结论");
}
```

**重要**：`researchIteration` 在异步续研过程中可能 stale，必须用 ref 跟踪当前值：

```typescript
const researchIterationRef = useRef(0);
// 每次 setResearchIteration 时同步 ref
```

**实现建议**：将续研逻辑提取为递归函数 `continueResearch(iteration, controller)`，避免 handleSend 膨胀。

### Step 6：自动续研状态 UI

**位置**：输入框上方，仅在 `isAutoResearching` 时显示。

**视觉规范**（烁烁设计 + 宪宪简化）：JetBrains Mono 状态文字 + 渐变呼吸光带。

```tsx
{/* 自动续研状态 — input 区域上方 */}
{isAutoResearching && (
  <div className="px-4 py-2 border-t border-white/5">
    <div className="flex items-center gap-2">
      <span className="text-[9px] font-mono text-primary/70 tracking-widest">
        {researchStatus}
      </span>
    </div>
    {/* 呼吸光带 */}
    <div className="mt-1.5 h-0.5 rounded-full bg-primary/10 overflow-hidden">
      <div
        className="h-full bg-gradient-to-r from-primary to-secondary rounded-full animate-[breathe_2s_ease-in-out_infinite]"
        style={{ transformOrigin: "left" }}
      />
    </div>
  </div>
)}
```

需要在 Tailwind 配置或内联 style 中添加 `breathe` 动画：

```css
@keyframes breathe {
  0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
  50% { transform: scaleX(1); opacity: 1; }
}
```

如果不想改 tailwind.config，可以用内联 style：
```tsx
style={{
  animation: "breathe 2s ease-in-out infinite",
  transformOrigin: "left",
}}
```
配合一个 `<style>` 标签或 CSS module。

### Step 7：结论高亮 (Halo Conclusion)

**位置**：在消息渲染区域，检测 assistant 消息是否包含结论标记。

修改消息气泡的渲染逻辑：

```tsx
{/* 消息气泡 — 修改 assistant 分支 */}
<div
  className={`p-3 rounded-lg text-xs leading-relaxed ${
    m.role === "user"
      ? "bg-secondary/5 text-on-surface border border-secondary/10"
      : isConclusion(m.content)
        ? "bg-primary/[0.05] text-on-surface-variant shadow-[0_0_20px_rgba(164,230,255,0.08)]"
        : "bg-white/[0.03] text-on-surface-variant border border-white/5"
  }`}
>
```

其中：
```typescript
function isConclusion(text: string): boolean {
  return text.includes("## Final Conclusion")
    || text.includes("## 最终结论")
    || text.includes("# Final Conclusion")
    || text.includes("# 最终结论");
}
```

### Step 8：重置逻辑

当用户发送新的非续研消息时，重置研究状态：

```typescript
// 在 handleSend 开头
setResearchIteration(0);
researchIterationRef.current = 0;
setIsAutoResearching(false);
setResearchStatus("");
```

## 验收标准

1. ✅ 输入框旁出现 DEEP toggle 按钮，点击切换 `deepResearch` 状态
2. ✅ `deepResearch=true` 时发送的 API payload 包含 `deep_research: true`
3. ✅ 第一轮响应完成后，自动检测是否包含结论标记
4. ✅ 未到达结论时，自动发送 "Continue the research" 并流式展示下一轮
5. ✅ 研究阶段 ribbon 正确显示当前进度（1-5）
6. ✅ 自动续研时显示呼吸光带 + JetBrains Mono 状态文字
7. ✅ 第 5 轮或检测到结论标记时停止自动续研
8. ✅ 结论消息气泡有 primary glow 视觉区分
9. ✅ streaming 或 autoResearching 时 toggle 和输入框 disabled
10. ✅ 用户可在任意时刻点 Stop 中断续研

## 不做什么

- ❌ 不做阶段点击切换内容（简化：ribbon 只做进度指示，不做点击导航）
- ❌ 不做对话持久化
- ❌ 不做 WebSocket
- ❌ 不改 InsightAskPanel（独立任务）
- ❌ 不改后端（已支持）

## 关键约束

- `breathe` 动画如果不便改 tailwind.config，用内联 `<style>` 标签
- `researchIteration` 必须用 ref 跟踪异步状态
- 续研消息 "Continue the research" 不要作为 user Message 显示在 UI 中
- 最终 `messagesRef` 要反映完整对话历史（包括隐藏的续研消息），因为 deepwiki 需要完整上下文

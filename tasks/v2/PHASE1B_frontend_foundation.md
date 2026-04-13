# Phase 1B: 前端基础框架

**前置依赖：Phase 0 完成**
**可与 Phase 1A (后端基础) 并行**
**完成后解锁：Phase 3, Phase 4**
**预估复杂度：中**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。前端只负责展示工具返回的结果。

## 目标

用 mock 数据构建完整前端骨架：Kinetic Shadow Framework 主题、Layout、所有页面、基础 UI 组件。

## 设计参考

- `/Users/shepard/Downloads/stitch_graybox_ui/DESIGN.md` — Kinetic Shadow Framework 规范
- `/Users/shepard/Downloads/stitch_graybox_ui/code.html` — 文档详情页
- `/Users/shepard/Downloads/stitch/dashboard_graybox_soc/code.html` — Dashboard
- `/Users/shepard/Downloads/stitch/task_manager_real_time_logs/code.html` — Task Manager
- `/Users/shepard/Downloads/stitch/analysis_task_detail_workspace/code.html` — Task Detail
- `/Users/shepard/Downloads/stitch/task_manager_updated_nav/code.html` — Nav 参考

**将 SOC 安全内容替换为代码分析内容。**

## 步骤

### 1. Next.js 初始化

```bash
cd /Volumes/Media/codetalk
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir
```

### 2. Tailwind 配置 — Kinetic Shadow Framework

将以下色彩 token 写入 `tailwind.config.ts`：

```
surface:               #10141A
surface-container-low: #181C22
surface-container:     #1C2026
surface-container-high:#262A31
primary:               #A4E6FF
on-primary:            #003544
primary-container:     #00687F
secondary:             #ECFFE3
tertiary:              #FFD1CD
on-surface:            #DFE2EB
on-surface-variant:    #BFC5D0
outline-variant:       用于 ghost border (15% opacity)
```

字体：Space Grotesk (display), Inter (UI), JetBrains Mono (code/data)
通过 Google Fonts CDN 加载。

### 3. Layout 组件

**`src/components/layout/Sidebar.tsx`:**
- 固定左侧 264px, `bg-surface-container-low`
- Logo: "CODETALKS" + 副标题 "Code Analysis Platform"
- 导航项: Dashboard, Tasks, Tools, Assets, Settings
- 活跃状态: 左 border primary + `bg-surface-container-high`
- 底部: "New Analysis" 按钮

**`src/components/layout/TopBar.tsx`:**
- 固定顶部, `backdrop-blur-xl` 毛玻璃
- 品牌名
- 全局搜索框 (JetBrains Mono)
- 系统状态指示灯

**`src/app/layout.tsx`:**
- Sidebar + TopBar + main content

### 4. 基础 UI 组件 (`src/components/ui/`)

| 组件 | 说明 |
|------|------|
| `StatusBadge.tsx` | 状态徽章，Signal Light 发光效果 |
| `GlassPanel.tsx` | 毛玻璃面板，`backdrop-filter: blur(12px)` 60% opacity |
| `LogTerminal.tsx` | 日志终端，surface-container-lowest + JetBrains Mono |
| `CyberInput.tsx` | 输入框，focus 时 inner glow |
| `DataTable.tsx` | 无水平线表格，交替行色 |
| `ToolCard.tsx` | 工具卡片：图标+名称+能力标签+状态 |
| `ProgressBar.tsx` | 渐变色进度条 |
| `MarkdownRenderer.tsx` | Markdown 渲染 (react-markdown + remark-gfm) |
| `MermaidRenderer.tsx` | Mermaid 图表渲染 (mermaid.js) |

**MarkdownRenderer 和 MermaidRenderer 是 deepwiki 结果展示的核心组件。**

### 5. 页面（全部用 mock 数据）

**Dashboard (`/dashboard`):**
- 4 个统计卡片: Total Projects / Active Tasks / Completed / Tool Health
- 最近活动列表
- 活跃任务进度

**Tasks (`/tasks`):**
- 过滤 tabs: All / Running / Completed / Failed
- 任务表格: ID, Repository, Tools, Progress, Duration, Actions
- 底部: LogTerminal (mock 日志)

**Task Detail (`/tasks/[id]`):**
- Tab 1: Analysis Flow（工具执行状态可视化）
- Tab 2: Documentation（Markdown + Mermaid 渲染 — deepwiki 产出）
- Tab 3: Findings（其他工具结果，暂为空）
- 右侧: AI Summary 面板
- 底部: 实时日志

**Tools (`/tools`):**
- 工具卡片 Grid
- MVP 只展示 deepwiki (Online), 其他 4 个标记 Coming Soon
- 每个卡片: 名称/描述/能力标签/状态

**Assets (`/assets`):**
- 左侧: 项目树
- 右侧: 仓库表格
- 添加项目/仓库 Modal

**Settings (`/settings`):**
- LLM Provider 配置表单
- AI 全局开关
- 系统健康概览

### 6. API Client (`src/lib/api.ts`)

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = {
  projects: { list, create, get, update, delete },
  tasks: { list, create, get, getResults, cancel },
  tools: { list, healthCheck },
  settings: { getLLM, saveLLM, testConnection },
};
```

### 7. WebSocket Client (`src/lib/ws.ts`)

```typescript
export function connectTaskLogs(taskId: string, onMessage: (log: LogEntry) => void): WebSocket
```

### 8. TypeScript 类型 (`src/lib/types.ts`)

与后端 Pydantic schema 对应的 TypeScript 接口。

## 验收标准

- [ ] `npm run dev` 启动成功，访问 localhost:3000
- [ ] 6 个页面全部可访问且展示 mock 数据
- [ ] 暗色主题与 Kinetic Shadow Framework 一致
- [ ] Sidebar 导航可切换页面
- [ ] LogTerminal 展示彩色 mock 日志
- [ ] MarkdownRenderer 正确渲染 Markdown（含代码高亮）
- [ ] MermaidRenderer 正确渲染 Mermaid 图表
- [ ] 无 1px 实线边框（遵循 No-Line Rule）

# Phase 2B: 前端骨架

**前置依赖：Phase 1 完成（Next.js 初始化 + Tailwind 配置）**
**可与 Phase 2A (后端核心) 并行**
**完成后解锁：Phase 4 功能集成**

## 任务目标

用 mock 数据构建完整的前端骨架：Layout、所有页面、基础 UI 组件。参照 stitch 系列 mockup 的设计语言。

## 设计参考文件

- `/Users/shepard/Downloads/stitch_graybox_ui/DESIGN.md` — Kinetic Shadow Framework 设计规范
- `/Users/shepard/Downloads/stitch_graybox_ui/code.html` — 知识库/文档详情页 HTML
- `/Users/shepard/Downloads/stitch/dashboard_graybox_soc/code.html` — Dashboard HTML
- `/Users/shepard/Downloads/stitch/task_manager_real_time_logs/code.html` — Task Manager HTML
- `/Users/shepard/Downloads/stitch/analysis_task_detail_workspace/code.html` — Task Detail HTML
- `/Users/shepard/Downloads/stitch/toolbox_penetration_tools/code.html` — Toolkit HTML
- `/Users/shepard/Downloads/stitch/asset_inventory_infrastructure_map/code.html` — Asset Inventory HTML
- `/Users/shepard/Downloads/stitch/task_manager_updated_nav/code.html` — Updated Nav HTML

**重要：读取这些 HTML 文件参考设计语言和布局结构，但要将 SOC 安全内容替换为代码分析内容。**

## 步骤

### 1. Layout 组件

**`src/components/layout/Sidebar.tsx`:**
- 固定左侧，宽度 264px，`bg-surface-container-low`
- Logo: "CODETALKS" + 副标题
- 导航项：Dashboard, Tasks, Tools, Assets (Projects/Repos), Settings
- 活跃状态：左 border primary 色 + `bg-surface-container-high`
- 底部：action button "New Analysis" + Support/Sign Out
- 参考 dashboard_graybox_soc/code.html 的侧边栏结构

**`src/components/layout/TopBar.tsx`:**
- 固定顶部，毛玻璃效果 `backdrop-blur-xl`
- 品牌名 "CODETALKS"
- 全局搜索框（JetBrains Mono 字体）
- 系统状态指示灯
- 用户头像

**`src/app/layout.tsx`:**
- 组合 Sidebar + TopBar + main content area
- 全局字体加载：Space Grotesk, Inter, JetBrains Mono

### 2. 基础 UI 组件 (`src/components/ui/`)

**`StatusBadge.tsx`:**
- 状态徽章，带发光效果
- 变体：success(secondary色), info(primary色), warning(tertiary色), error(tertiary深色)
- 参考 DESIGN.md 中的 "Signal Light" 处理

**`GlassPanel.tsx`:**
- 毛玻璃面板容器
- `backdrop-filter: blur(12px)`, 60% opacity
- 用于浮动工具栏和信息面板

**`LogTerminal.tsx`:**
- 日志终端组件
- `surface-container-lowest` 背景，JetBrains Mono 字体
- 彩色日志行：success=secondary-fixed-dim, system=primary-fixed-dim, error=tertiary
- 自动滚动到底部
- 用 mock 数据先展示效果

**`CyberInput.tsx`:**
- 输入框组件
- 默认：`surface-container-lowest` 背景，`outline-variant` 20% opacity border
- Focus：1px solid `primary-container` + inner glow

**`DataTable.tsx`:**
- 数据表格
- 无水平分割线，用交替行色区分
- 表头：`label-md` 全大写

**`ToolCard.tsx`:**
- 工具卡片
- 图标 + 名称 + 描述 + 能力标签 + 状态徽章(发光)
- Launch / Configure 按钮

**`ProgressBar.tsx`:**
- 进度条，渐变色 primary → primary-container

### 3. Dashboard 页面 (`src/app/dashboard/page.tsx`)

参考 `dashboard_graybox_soc` 设计：
- 4 个统计卡片（大数字 Space Grotesk）：
  - Total Projects（替代 TOTAL EVENTS）
  - Active Tasks（替代 ACTIVE）
  - Completed（替代 SYSTEM）
  - Tool Health（显示 X/5 在线）
- 最近活动列表（替代 RECENT ALERTS）
- 活跃任务进度（替代 ACTIVE OPERATIONAL TASKS）
- 用 mock 数据

### 4. Tasks 列表页 (`src/app/tasks/page.tsx`)

参考 `task_manager_real_time_logs` 设计：
- 顶部过滤 tabs：All Tasks / Running / Completed / Failed
- 任务表格列：Task ID, Repository, Tools, Progress, Duration, Actions
- 底部面板：Live Log Console（LogTerminal 组件）
- 用 mock 数据

### 5. Task 详情页 (`src/app/tasks/[id]/page.tsx`)

参考 `analysis_task_detail_workspace` 设计：
- 顶部标签：Analysis Flow / Findings / Documentation
- Analysis Flow：可视化节点图（工具执行路径），用 SVG 或 div 布局
- 右侧面板：工具输出上下文（替代 DEEPWIKI_CONTEXT）
- 底部：实时分析日志
- 用 mock 数据

### 6. Tools 页面 (`src/app/tools/page.tsx`)

参考 `toolbox_penetration_tools` 设计：
- Grid/List 视图切换
- 5 个工具卡片：Zoekt, CodeCompass, GitNexus, deepwiki-open, Joern
- 每个卡片显示：名称、描述、能力、状态(Online/Offline)、Launch/Configure
- 底部：工具部署历史日志

### 7. Assets 页面 (`src/app/assets/page.tsx`)

参考 `asset_inventory_infrastructure_map` 设计：
- 左侧：项目树（可展开显示仓库）
- 右侧：仓库列表表格（Name, Source Type, Language, Last Analyzed, Actions）
- Filter + Export 按钮
- 添加项目/仓库的表单（Modal）

### 8. Settings 页面 (`src/app/settings/page.tsx`)

- LLM Provider 配置表单：
  - Provider 下拉选择：OpenAI / Anthropic / Ollama / Custom
  - Model Name 输入
  - API Key 输入（密码类型）
  - Base URL 输入（Ollama/Custom 时显示）
- AI 全局开关 Toggle
- 系统健康概览

### 9. API Client (`src/lib/api.ts`)

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = {
  projects: {
    list: () => fetch(`${API_BASE}/api/projects`),
    create: (data) => fetch(`${API_BASE}/api/projects`, { method: "POST", body: JSON.stringify(data) }),
    get: (id) => fetch(`${API_BASE}/api/projects/${id}`),
    update: (id, data) => fetch(`${API_BASE}/api/projects/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id) => fetch(`${API_BASE}/api/projects/${id}`, { method: "DELETE" }),
  },
  tasks: { /* 类似结构 */ },
  tools: { /* 类似结构 */ },
  settings: { /* 类似结构 */ },
};
```

### 10. WebSocket Client (`src/lib/ws.ts`)

```typescript
export function connectTaskLogs(taskId: string, onMessage: (log: LogEntry) => void) {
  const ws = new WebSocket(`ws://localhost:8000/ws/tasks/${taskId}/logs`);
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  return ws;
}
```

### 11. TypeScript 类型 (`src/lib/types.ts`)

与后端 Pydantic schema 对应的 TypeScript 接口。

## 验收标准

- [ ] 所有 6 个页面可访问且展示 mock 数据
- [ ] 设计风格与 stitch mockup 一致（暗色主题、发光效果、毛玻璃）
- [ ] Sidebar 导航可切换页面
- [ ] LogTerminal 组件展示彩色 mock 日志
- [ ] Tools 页面展示 5 个工具卡片
- [ ] 响应式布局（至少 1280px+ 适配）

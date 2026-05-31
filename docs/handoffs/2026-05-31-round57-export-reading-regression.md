# CodeTalk Handoff - Round57 Export + Reading Regression

## What

本轮围绕用户反馈的三件事做回归与修复：

1. 工作空间报告导出改为单任务/当前版本导出。
   - `backend/app/services/export_service.py` 支持 `task_id`，默认只导出最新 completed task 的报告。
   - `backend/app/api/workspaces.py` 的 `/api/workspaces/{ws_id}/export` 接收 `task_id` query。
   - `frontend/src/lib/api.ts` 和 `frontend/src/app/workspaces/[id]/page.tsx` 导出按钮带上当前选择版本的 `task_id`。
   - UI 文案从“导出全版本”改成“导出当前版本”。

2. 工作空间与 DeepWiki 阅读体验统一到 CodeTalk Markdown 渲染层。
   - 工作空间 `ReportCard` 从 `<pre>` 原文展示改为 `MarkdownRenderer`。
   - DeepWiki 删除手写逐行 `MarkdownContent`，改用 `MarkdownRenderer`。
   - `MarkdownRenderer` 增加 `rehypeRaw`、`details/summary` 样式、`break-words`，支持折叠块、表格、代码块、Mermaid 等结构化内容。
   - DeepWiki 三栏布局在中小宽度隐藏右侧元信息栏，优先保证正文宽度。

3. 已做一次真实浏览器点击回归。
   - 工作空间页刷新后展开“模块地图”，确认 heading/table/code/blockquote 被渲染。
   - 点击了工作空间 `md` 导出按钮。
   - DeepWiki Smoke 仓库打开后确认表格、代码、引用、折叠块展示正常。

## Why

用户指出两个真实产品问题：

1. 当前工作空间导出会把所有历史任务的报告打包，实际应该是“当前任务/当前版本”的报告。
2. 文档阅读体验不仅 DeepWiki 要修，工作空间报告也要修；不能再把 Markdown 当纯文本输出。

另外，embedding 只消耗约 12000 token 的原因不是“全链路只读了这么多”。embedding token 只统计向量化输入，且 smoke 仓库只有 2 个小文件；真实报告生成、聊天生成、工具编排消耗在 LLM completion/request 侧，不会计入 embedding token。重复运行时如向量库已有缓存，embedding 还会更低。

## Tradeoff

1. `rehypeRaw` 能让 `<details>` 等报告结构真正渲染，但如果未来把不可信外部 Markdown 直接展示给公网用户，需要补 `rehype-sanitize` 白名单。当前 CodeTalk 是内网/本地文档工具，先换取阅读体验。
2. 本轮只完成“渲染与导出边界由 CodeTalk 控制”。用户提出的更大目标“AI 只生成内容，排版、格式、图、表由 CodeTalk 参与”还没有完全落成，需要下一轮做结构化报告 IR。
3. 浏览器内嵌环境不支持暴露 download event；我实际点击了导出按钮，然后用同一个后端接口请求验证 zip 内容。

## Open Questions

技术 OQ：

1. 报告生成仍然让 AI 输出 Markdown/表格/Mermaid 文本，内网模型仍可能截断或格式不闭合。建议改为：AI 输出 `ReportIR(JSON)` 的章节事实与叙述，CodeTalk 根据 schema 渲染 Markdown/DOCX/XML、表格和图。
2. `MarkdownRenderer` raw HTML 支持需要安全策略：内网可接受，公网/多人上传场景需要 sanitize 白名单。
3. 当前后端运行环境使用系统 Python 启动，仓库 `backend/.venv311` 缺 `sqlalchemy`，不能直接跑完整 app。已用系统 Python 重启 8100。

价值 OQ：

1. 图表由 CodeTalk 生成后，哪些图必须基于工具事实自动生成，哪些允许 AI 给“图意图”？
2. 导出文件是只导出当前版本的 7 份报告，还是用户未来还需要一个显式“导出全部历史版本”的高级入口？

## Evidence

自动化：

1. `backend/.venv311/Scripts/python.exe -m pytest backend/tests/test_export_service.py -q`
   - 20 passed, 12 existing asyncio-mark warnings.
2. `backend/.venv311/Scripts/python.exe -m pytest backend/tests/test_export_service.py::TestExportWorkspaceReports -q`
   - 5 passed.
3. `npx eslint "src/app/workspaces/[id]/page.tsx" "src/app/deepwiki/[repoId]/page.tsx" "src/components/ui/MarkdownRenderer.tsx" "src/lib/api.ts"`
   - passed.
4. `npm run build`
   - passed.

接口实证：

1. 重启后端前，请求 `/api/workspaces/{ws_id}/export?format=md&task_id=bb8b2abf...` 仍返回旧进程结果：420 entries，说明 8100 后端还没加载新代码。
2. 重启 8100 后，同一请求返回：
   - filename: `workspace-975784c8-bb8b2abf.zip`
   - EntryCount: 7
   - Entries: `项目结构初步理解.md, 模块地图.md, 源码定向阅读记录.md, 关键业务流程分析.md, GitNexus 结果可信度评估.md, 测试视角代码理解.md, 需求-设计-代码追踪.md`
3. 不带 `task_id` 的默认导出也返回同一个最新 task 的 7 entries。

浏览器截图：

1. 工作空间 Markdown 渲染与当前版本导出：
   - `E:\codetalk_test\codetalks-Test\codetalk\frontend\manual-round57-workspace-markdown-export.png`
2. DeepWiki 修复前窄列观察：
   - `E:\codetalk_test\codetalks-Test\codetalk\frontend\manual-round57-deepwiki-markdown.png`
3. DeepWiki 宽度修复后：
   - `E:\codetalk_test\codetalks-Test\codetalk\frontend\manual-round57-deepwiki-markdown-wide.png`

## Next Action

建议开发组 AI 下一轮做：

1. 设计并落地 `ReportIR`：
   - AI 输出章节事实、自然语言段落、证据引用、图表意图，不直接负责 Markdown 表格/Mermaid/DOCX 版式。
   - CodeTalk renderer 统一产出 Markdown、DOCX、XML。
   - 图表从 GitNexus/CGC/evidence cards 的结构化数据生成。

2. 增加 e2e/集成测试：
   - 工作空间存在多个历史版本时，点击导出当前版本，zip 只包含当前 task 的报告。
   - DeepWiki 页面中 Markdown 表格、代码块、`details`、Mermaid 至少各有一个 DOM 断言。

3. 补安全白名单：
   - 给 `MarkdownRenderer` 增加 `rehype-sanitize` schema，允许 `details/summary/code/table`，禁止脚本和危险属性。

4. 修运行环境一致性：
   - 当前 8100 需要系统 Python 才能启动，`backend/.venv311` 缺 `sqlalchemy`。
   - 开发组需要决定补齐 venv 依赖，或者统一文档与启动脚本使用系统 Python。

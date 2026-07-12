---
feature_ids: [F002]
topics: [agent-sandbox, staged-execution, artifacts, workflow-cockpit]
doc_kind: bug-report
created: 2026-07-12
---

# 发布债务独立审查缺陷闭环

## 报告人

独立 reviewer Sagan 在提交 `eedb7b1d` 上审查发现；CodeTalk 真实 Playwright 回归补充发现执行器目录污染和 specialized workflow 隐形 Agent。

## 复现步骤

1. 让分阶段任务按“SFMEA、黑盒、流程”乱序声明输出，实际按用户文字顺序执行，且同阶段第二个文件被丢弃。
2. 让 provider 首次抛临时异常或返回非法 JSON，任务不重试；任务取消后仍继续阶段调用。
3. 在 macOS sandbox 中运行 OpenCode，旧策略可读整个宿主机但又无法写自身日志，真实运行报 `EPERM`。
4. 在 manifest 验收后替换文件，旧下载接口仍返回替换内容。
5. 在驾驶舱选择 `local-search` 作为执行器，任务显示可用但以 unknown provider 失败。
6. 导入并保存纯本地 source-flow 工作流，设计器额外注入 Claude `agent_task`。
7. 在右侧主结果面板预览大 JSON，界面只有“下载预览”，完整文件下载藏在重复的旧诊断区。
8. 从驾驶舱快速切回设计器并立即点击节点，服务端 HTML 已可见但 React 尚未 hydration，第一次点击被无声吞掉。

期望：依赖拓扑、重试/取消、OS 边界、下载验收和设计器 DSL 均 fail-closed 且与 UI 描述一致。

## 根因分析

- 阶段编译器边遍历边裁剪依赖，并用 stage id 去重 artifact；重试只包住 truncation。
- sandbox 把 `/` 全盘只读挂载或使用全局 `file-read*`，同时没有为合法 CLI 状态目录建边界。
- manifest 只在生成时校验，下载路径信任旧哈希。
- 工作流能力目录直接复用为执行器目录。
- specialized merge 在草稿没有 Agent 时仍接受 builder 自动生成的 contract Agent。
- 主结果面板复用的 `ArtifactPreviewCard` 仍使用浏览器内存中的截断预览下载，没有接入任务产物完整下载端点。
- 工作台服务端首屏在 hydration 前仍允许 pointer 事件，浏览器会命中尚未绑定 React handler 的节点。

## 修复方案

- 以固定拓扑编译阶段，支持一阶段多文件；异常、空输出、JSON/schema 无效均阶段内重试，并在调用前后检查取消。
- macOS/Linux 只暴露系统运行时、工作区、显式 provider 状态和 artifact 路径；真实 OpenCode 状态目录可写，其他宿主文件不可读。
- 单文件和 ZIP 下载前重新核对状态、路径、大小、sha256 和 schema，并以内存响应消除校验后的替换窗口。
- 驾驶舱仅列 `agent_cli`、`agent_runtime`、`codetalk_builtin_llm`；工具仍保留在工作流上下文节点。
- specialized workflow 仅合入画布显式新增的 Agent。
- 共享预览卡接入经重新校验的完整产物下载 URL，主面板直接提供完整/完整脱敏下载。
- 工作台根节点以 hydration 状态为交互门禁；客户端事件绑定完成前禁止 pointer 事件，完成后恢复鼠标和键盘操作。

## 验证方式

- `backend/tests/test_ai_staged_execution.py` 覆盖乱序、多文件、异常/非法 JSON 重试和取消。
- `backend/tests/test_agent_sandbox.py`、`test_agent_cli_bridge.py` 覆盖读写边界；macOS 真实 OpenCode 返回 `OK` 且策略为 `active`。
- `backend/tests/test_ai_thread_artifacts.py` 和 AI API 测试覆盖验收后替换返回 409。
- `frontend/scripts/workflow-builder-canvas-contract.test.mjs` 覆盖纯本地 workflow 不注入 Agent。
- Playwright 真实 hover、点击、输入、运行与下载覆盖驾驶舱最终行为；节点切页点击连续 4 次通过，完整 JSON 下载内容经本地文件读取复核。
- 首轮最终门禁：后端 `2,062 passed, 8 skipped`；浏览器 E2E `47 passed`；前端契约 `40 passed`；部署器 `173 passed, 1 skipped`；lint、TypeScript、生产构建、密钥扫描和工件卫生通过。

## 最终复审追加闭环

独立 reviewer 在提交 `1e4d20c0` 上继续发现 1 个 P0、2 个 P1、2 个 P2：Agent artifact 符号链接可诱导后端读取宿主文件；分阶段取消不打断活跃模型且会被晚到异常改写为失败；GitNexus 只有串行锁而没有队列准入上限；长 `Retry-After` 被客户端 30 秒退避上限截断；部署文档的分支与队列描述不一致。

修复后，artifact 验收和复制只接受运行目录内、无符号链接的普通文件，并通过 `openat`/`O_NOFOLLOW` 逐级安全读取；manifest 与下载前复验也拒绝符号链接。分阶段模型调用改为可取消任务并轮询运行状态，`fail_run` 仅能从 queued/running 转为 failed。GitNexus 新增 `GITNEXUS_INDEX_QUEUE_MAX`（默认 8）、队列饱和状态和中文拒绝提示；客户端指数退避仍封顶 30 秒，但服务端 `Retry-After` 优先遵守并设置 1 小时安全上限。部署真相源统一为 `feat`。

新增红绿测试覆盖符号链接首次验收与验收后替换、活跃 provider 取消、cancelled 终态保护、队列第 3 个请求拒绝、120 秒 `Retry-After`；相关回归 `424 passed`。

追加修复后的最终门禁：后端 `2,070 passed, 8 skipped`；浏览器 E2E `47 passed`；前端契约 `40 passed`；部署器 `173 passed, 1 skipped`；lint、TypeScript 和生产构建通过。

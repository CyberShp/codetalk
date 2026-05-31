# Handoff: DeepWiki Open E2E Regression - 2026-05-31

## What

本轮围绕 deepwiki-open “能部署但点击生成经常 500/XML 错误”做了真实浏览器 E2E 回归。

已确认当前 CodeTalk 有可用 embedding 配置：

- Active chat: `deepseek-chat` via OpenAI-compatible endpoint.
- Active embedding: `text-embedding-v4` via DashScope OpenAI-compatible endpoint.
- 直接 embedding 探针成功，返回 1024 维向量。

修复和验证内容：

- `deployer/deepwiki_launcher.py`
  - 新增 deepwiki-open `.env` 加载逻辑，在 import upstream `api.api` 前执行。
  - `.env` 值覆盖继承环境变量，保证 CodeTalk 设置页同步出的配置是 DeepWiki 运行时真相源。
  - 增加 dependency-free fallback dotenv parser，避免 `python-dotenv` 缺失时启动器完全失效。
  - 将 `adalflow.core.component`、`api.openai_client` 的 INFO 日志降到 WARNING，避免日志打印模型客户端密钥字段或完整 prompt/source 片段。
- `deployer/test_deepwiki_launcher.py`
  - 覆盖 `.env` 覆盖继承环境变量。
  - 覆盖 fallback parser 解析 `export KEY="VALUE"`。
  - 覆盖敏感日志降级。
- 已对 `D:\coworkers\deepwiki-open\api\logs\application.log*` 做一次 `sk-...` 模式历史日志脱敏。

本轮 E2E 过程：

- 浏览器打开 `http://localhost:3005/deepwiki/6cab30e4-7107-427a-bfef-d3af80fe5518`。
- 第一次点击 `生成 Wiki` 复现失败：
  - CodeTalk status: `failed`
  - Error: `HTTP 500: {"detail":"Error preparing retriever: Environment variable OPENAI_API_KEY must be set"}`
- 修复 launcher `.env` 加载后重启 `deepwiki-api`。
- 第二次浏览器点击 `生成 Wiki` 成功：
  - 进度从 5% 到 100%
  - 最终 `completed`
  - 生成 10 页
- 日志降噪补丁后再次重启 `deepwiki-api`，浏览器点击 `重新生成` 成功：
  - 耗时约 67 秒
  - 最终 `completed / 100%`
  - 生成 9 页
  - deepwiki-open 日志检查：实际 `sk-...` key 匹配数 0，补丁后 `Restoring class using from_dict` 和 `api_kwargs:` 日志数 0。

## Why

根因不是“没有 embedding 模型”，而是 native launcher 绕过了 deepwiki-open 原本会加载 `.env` 的入口。

具体链路：

1. CodeTalk 设置同步已经正确写入 `D:\coworkers\deepwiki-open\.env`。
2. `api/config/embedder.json` 已经引用 `${DEEPWIKI_EMBEDDING_API_KEY}` 和 `${DEEPWIKI_EMBEDDING_BASE_URL}`。
3. 但 native launcher 直接运行 `uvicorn api.api:app`，没有经过 upstream `api/main.py` 中的 `load_dotenv()`。
4. 因此 DeepWiki API 进程里没有 `OPENAI_API_KEY` / `DEEPWIKI_EMBEDDING_*`。
5. 点击生成进入 retriever 准备阶段时，OpenAIClient 找不到环境变量，抛 500。

这个问题会表现成用户侧“点击生成 500 / 等一会儿后 XML 或解析错误”，因为生成链路的第一步、结构生成、RAG 检索、页面流式生成都可能把上游配置问题包装成不同错误。

## Tradeoff

采用在 CodeTalk launcher 中加载 `.env`，没有修改 third-party deepwiki-open 主体代码。

放弃方案：

- 直接改 `D:\coworkers\deepwiki-open\api\api.py` 或 `config.py`：更贴近 upstream，但不利于后续替换 deepwiki-open 版本。
- 只在 ProcessManager env 里传所有密钥：会把 CodeTalk 设置同步逻辑分散到进程启动层，且 active model 更新后更容易漏同步。
- 关闭全部 INFO 日志：更安全但损失生成进度、HTTP 状态等排障信息。本轮只降级会打印密钥/源码上下文的两个 logger。

## Open Questions

技术遗留：

- DeepWiki 内容页的 Markdown 渲染仍有明显问题：
  - `<details>` / `<summary>` 被当作普通文本显示。
  - fenced code block 逐行渲染，不是真正代码块。
  - Mermaid 和 Markdown 表格没有渲染。
  - 位置：`frontend/src/app/deepwiki/[repoId]/page.tsx` 的自定义 `MarkdownContent`。
  - 建议复用现有 `frontend/src/components/ui/MarkdownRenderer.tsx` 或引入 `react-markdown + remark-gfm + rehype-raw`，Mermaid 单独组件化渲染。
- 生成页数有 LLM 非确定性：同一 smoke repo 两轮分别 10 页和 9 页。功能可用，但不应把 page_count 当强确定断言。
- 本轮 smoke repo 只有 2 个文件，DeepWiki prompt 要求至少 5 个源文件，模型会写出“仓库仅有 2 个文件”的 caveat。真实大仓还需要回归。

质量/安全遗留：

- deepwiki-open 历史日志已脱敏当前 `application.log*`，但如果其他目录还有旧日志，需要开发组再做一次全局 secret scan。
- `api.openai_client` INFO 日志已被 launcher 降级；如果 upstream 后续改 logger 名，需重新检查日志是否打印 key/prompt/source。

## Next Action

建议开发组 AI 下一轮做：

1. 修复 DeepWiki 文档页 Markdown 渲染，重点覆盖 `<details>`、代码块、表格、Mermaid。
2. 用一个 20-50 文件的真实小仓做浏览器 E2E：
   - 添加仓库
   - 点击生成
   - 观察进度
   - 打开 3 个页面
   - 检查引用、代码块、图表、目录跳转
3. 增加一个 smoke E2E/集成测试：
   - mock 或小型 real repo
   - 断言 repo 从 `pending/running` 到 `completed`
   - 断言 pages 非空
   - 断言失败状态会把 DeepWiki 500 detail 传回状态接口
4. 对日志做自动化检查：
   - `sk-`、`api_key`、`_api_key` 不应出现在 `api/logs/application.log*`
   - prompt/source 全量内容不应出现在 INFO 级日志

## Verification

已跑通过：

```powershell
python -m pytest deployer/test_deepwiki_launcher.py -q
```

结果：`3 passed`

```powershell
$env:PYTHONPATH='backend'; backend\.venv311\Scripts\python.exe -m pytest `
  backend/tests/test_settings_api.py::test_update_general_settings_syncs_deepwiki_embedding_full_config `
  backend/tests/test_settings_api.py::test_update_general_settings_syncs_deepwiki_embedder_json `
  backend/tests/test_settings_api.py::test_deepwiki_generation_uses_active_chat_model `
  backend/tests/test_tools_api.py::test_deepwiki_registry_uses_venv_launcher_and_declared_ports -q
```

结果：`4 passed`

浏览器 E2E：

- 失败复现：`17:43:22`，500 `OPENAI_API_KEY must be set`
- 修复后成功：`17:45:51` 至 `17:47:13`，`completed / 100% / 10 pages`
- 日志降噪后回归成功：`17:51:06` 至 `17:52:12`，`completed / 100% / 9 pages`


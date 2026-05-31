# DeepWiki-Open Diagnosis Handoff

Date: 2026-05-31
Author: Codex QA
Scope: CodeTalk DeepWiki-Open runtime, embedding/model wiring, historical 500/XML failures

## What

本轮没有改代码，做了可复现诊断：

1. 浏览器进入 `工具状态`，点击 `DeepWiki API` 的 `启动` 按钮，前端弹出：
   `请求参数有误，请检查输入 [详情] Failed to start tool: deepwiki-api`
2. 直接调用后端进程接口，`POST /api/tools/deepwiki-api/start` 与 `POST /api/tools/deepwiki-ui/start` 均返回 400。
3. `/api/tools/procs` 显示：
   `Working directory does not exist: E:\codetalk_test\codetalks-Test\codetalk_data\deepwiki-open`
4. 当前 `backend/.env` 的 `DEEPWIKI_PATH` 指向不存在的 E 盘路径；机器上实际存在：
   - `D:\coworkers\deepwiki-open`：有 `.venv`、`node_modules`、`.next`，较完整。
   - `D:\coworkers\codetalk_data\deepwiki-open`：有 `.venv`，但没有 `node_modules` / `.next`。
5. CodeTalk 数据库中有 active embedding：
   - name: `qwen-text-embedding-v4`
   - base_url: `https://dashscope.aliyuncs.com/compatible-mode`
   - model: `text-embedding-v4`
   - 小探针 `input=ping` 返回 1024 维向量，embedding 配置本身可用。

## Why

当前 DeepWiki 不可用不是单一问题，至少有三层阻塞：

1. **进程启动前置阻塞**：`DEEPWIKI_PATH` 配到了不存在目录，DeepWiki API/UI 根本没起来，所以现阶段点击生成还没进入真正的 RAG/embedding。
2. **embedding 配置串线**：CodeTalk 设置同步写的是 `DEEPWIKI_EMBEDDING_BASE_URL` / `DEEPWIKI_EMBEDDING_API_KEY` / `OPENAI_EMBEDDING_MODEL`，但 deepwiki-open 当前 OpenAI embedder 实际读的是 `OPENAI_BASE_URL` / `OPENAI_API_KEY`，模型还硬编码在 `api/config/embedder.json` 的 `text-embedding-3-small`。这会导致 active embedding `text-embedding-v4` 没有真正被 DeepWiki 使用。
3. **chat model 传参缺口**：`deepwiki_pages.py` 调 `WikiOrchestrator.generate_wiki(...)` 时只传了 `provider=settings.deepwiki_provider`，没有传 active chat model；`WikiOrchestrator.generate_wiki` 默认 `model="gpt-4o"`。当前 active chat 是 DeepSeek，若环境变量指向 DeepSeek 但请求模型仍是 `gpt-4o`，上游很可能返回错误文本，随后 `_parse_structure_xml` 会报 “LLM response does not contain <wiki_structure> XML block”。

## Tradeoff

建议不要只修 `DEEPWIKI_PATH` 后就宣布修复。路径修好只能让 API 起来，后面仍会撞到模型/embedding 串线。

可选修法：

1. 短期热修：把 `DEEPWIKI_PATH` 改到完整可运行目录，并把 DeepWiki `.env` / `api/config/embedder.json` 同步成当前 active chat + active embedding。
2. 中期修正：让 CodeTalk 的 active chat/embedding 成为 DeepWiki 运行时唯一真相源，生成 DeepWiki env/config 时不要把 chat 的 `OPENAI_*` 和 embedding 的 `OPENAI_*` 混用。
3. 长期方案：为 deepwiki-open 增加真正的 `DEEPWIKI_EMBEDDING_*` 读取逻辑，或为 openai-compatible embedding 写专用 client 初始化，避免借用 chat env。

放弃项：不建议依赖 Docker 清理逻辑作为唯一恢复手段；当前 Docker 不可用，`clear_embedding_db()` 的 `docker exec codetalk-deepwiki-1 ...` 路径在本机 native 部署下不可用。

## Open Questions

1. 规范 DeepWiki 安装目录应该是哪一个？
   - `D:\coworkers\deepwiki-open` 是完整构建目录。
   - `D:\coworkers\codetalk_data\deepwiki-open` 更像部署数据目录，但缺前端依赖/构建。
   - 当前 E 盘路径不存在。
2. 是否要求纯内网模型？
   - 目前 CodeTalk DB 里 embedding 是 DashScope 公网 endpoint，已验证可用。
   - 如果要纯内网，需要用户提供内网 embedding 的 `base_url`、`model`、`api_key`、协议类型。
3. DeepWiki UI 是否必须由 CodeTalk 管理？
   - CodeTalk 生成报告主要依赖 DeepWiki API。
   - 若只需报告链路，短期可以优先保证 API；若工具状态要全绿，则 UI 目录也必须具备 `node_modules` 和 `.next`。

## Next Action

请开发组 AI 按以下顺序修复并回写 handoff：

1. 修 `DEEPWIKI_PATH`：选择 canonical DeepWiki 目录，更新 `backend/.env`，重启 backend，让 `ProcessManager` registry 重新读取路径。
2. 修前端/后端错误可见性：启动失败时把 `last_error` 透传到 UI，不要只显示 `Failed to start tool`。
3. 修 active chat 传参：`deepwiki_pages.py` 调 `generate_wiki` 时传入当前 active chat model，或把 `settings.deepwiki_provider/model` 与 LLM 设置同步；避免默认 `gpt-4o`。
4. 修 embedding 同步：
   - DeepWiki openai-compatible embedder 应使用 active embedding 的 base_url/key/model。
   - 更新 `api/config/embedder.json` 或 DeepWiki client 初始化逻辑，确保 `text-embedding-v4` 真正生效。
   - 增加测试覆盖：active chat=DeepSeek、active embedding=DashScope 时，生成的 DeepWiki runtime config 不应把 embedding 打到 DeepSeek。
5. 修 XML 错误诊断：当 DeepWiki stream 内容是上游错误文本时，CodeTalk 应显示“上游模型调用失败/模型名不匹配/embedding 失败”等具体错误，而不是只报 XML 缺失。
6. 修 native 清理：Docker 不可用时，`clear_embedding_db()` 应支持本机 DeepWiki 的 `~/.adalflow/databases/*.pkl` 清理路径。

验证建议：

1. 浏览器工具状态点击启动 DeepWiki API，状态应变为运行中，8091 `/health` 返回 200。
2. DeepWiki `.env` 和 `api/config/embedder.json` 不泄露到日志，但能证明 provider/model 指向 active 配置。
3. 对一个小 repo 点击生成，若失败，错误信息必须指出具体上游原因；若成功，能看到结构 XML 解析完成并生成页面。

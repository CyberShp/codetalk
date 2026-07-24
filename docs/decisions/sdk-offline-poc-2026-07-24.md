---
feature_ids: [agent-harness-v3, zero-public-egress]
topics: [sdk, poc, sbom, offline]
doc_kind: decision-record
created: 2026-07-24
---

# SDK 离线导入 POC 记录（2026-07-24）

## 决策

本次 POC 不把任何厂商 SDK 加入 CodeTalk 的后端依赖或启动路径。现有
`AgentHarnessFacade` 和本地 Workflow Runner 继续是唯一的产品运行时。候选 SDK
只能在独立 Adapter 进程中继续评估，且必须先满足运行时零自治出站、会话、取消、
事件、产物收集和真实 SPDK 对照验收。

这是一份开发侧证据，不是内网部署许可，也不是 SDK 选型完成记录。

## 隔离边界

- Wheelhouse：`/Volumes/Media/codetalk-v3-sdk-poc/wheels/`；
- Python POC：`/Volumes/Media/codetalk-v3-sdk-poc/venv/`，Python 3.11；
- Claude Node POC：`/Volumes/Media/codetalk-v3-sdk-poc/claude/`；
- 产品仓库未新增 `requirements`、`package.json` 或启动时 import；
- Python 安装使用 `pip install --no-index --find-links ...`，没有在安装阶段访问网络；
- 导入验证把 `socket.connect` 和 `socket.create_connection` 替换为立即抛错，随后只做
  SDK import 与基础对象构建，不发起模型调用。

## 主包 SBOM

| 候选 | 版本 | 许可证/条款 | SHA-256 | POC 结果 | 运行时结论 |
| --- | --- | --- | --- | --- | --- |
| Claude Agent SDK | `0.3.218` | `SEE LICENSE IN README.md`，需法务确认商业条款 | `e0d9154a09b0ae2c07a462db0c3bdd3e1c163d10995e05a3ab33f36c32f04fe1` | Node 模块导入成功，无网络连接 | 仅可作为 CLI/SDK Adapter 候选，不能默认随产品分发 |
| OpenAI Agents SDK | `0.18.3` | MIT | `c6ed971fdeb34d39a9931787bd3960c1e84dc5d7345705794cc5cab8a1158d07` | `agents.Agent(...)` 创建成功，无网络连接 | 默认 tracing 必须在 Adapter 初始化前显式关闭；不得使用默认公网端点 |
| Microsoft Agent Framework Core | `1.12.1` | MIT | `4cdd687d434af9592e42a708f5da9291fbae80b6a02b39ddc1456aa988b6941e` | `agent_framework` 导入成功，无网络连接 | 依赖 OpenTelemetry API；未配置 exporter 前仍不得进入主进程 |
| LangGraph | `1.2.9` | MIT | `c2d98ad94333937922ba04148641c1da2bfe45b5b8e55d7b6dcb0bb2df809e76` | `langgraph` 导入成功，无网络连接 | 依赖链包含 LangSmith；必须禁用 tracing/Studio/远端服务并做流量捕获 |

完整依赖 wheelhouse 共 64 个文件；Claude Node 依赖锁定在
`/Volumes/Media/codetalk-v3-sdk-poc/claude/package-lock.json`。发布前应把该目录转为
内部制品库可验证 bundle，并补充所有传递依赖的许可证与 hash 清单。

## 可复现命令

```bash
python3.11 -m pip install --no-index \\
  --find-links /Volumes/Media/codetalk-v3-sdk-poc/wheels \\
  openai-agents langgraph agent-framework-core

CODETALK_POC_OFFLINE=1 /Volumes/Media/codetalk-v3-sdk-poc/venv/bin/python - <<'PY'
import socket
socket.create_connection = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network"))
socket.socket.connect = socket.create_connection
from agents import Agent
import langgraph
import agent_framework
Agent(name="offline-poc", instructions="Do not run.")
print("offline imports passed")
PY
```

## 已确认的风险与下一门禁

1. 这只证明 import 和对象创建不出网，不证明运行、trace、MCP、更新检查或子进程不出网。
2. OpenAI Agents 默认 tracing、LangGraph 的 LangSmith 依赖、Microsoft 的 OpenTelemetry
   以及 Claude SDK 的商业条款都是 P0 选型门禁。
3. 下一步必须在独立 Adapter 进程中，以同一 RunSnapshot 和 loopback 模型/MCP fixture
   验证 start、event、cancel、resume、artifact 以及流量捕获；不得连接产品正式服务。
4. 只有上述 POC 和真实 SPDK 对照完成后，才可决定是否采用一个 Durable Stage Runtime；
   若无显著收益，正式 ADR 必须选择保留当前本地 runner。

# CodeTalk Internal Release Gate

本文面向 CodeTalk 的开发和测试团队，用于判断一个版本是否可以交给同事试用。普通使用者不需要阅读本文。

## 当前结论

截至 2026-05-31，核心链路达到 Internal Release 标准：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1 -RunE2E
```

最近一次验证结果：

| 检查项 | 结果 |
|---|---|
| 后端测试收集 | 757 tests collected |
| 后端产品路由契约 | 37 passed |
| 前端 lint | passed |
| 前端 production build | passed |
| 浏览器真实点击 release E2E | 1 passed |
| Deployer 测试收集 | 129 tests collected |
| Deployer full tests | 128 passed, 1 skipped |
| Release gate | passed |
| 端口释放 | 9000 / 3004 / 3003 均已释放 |

## Release 命令

快速门禁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1
```

完整内部 release 门禁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1 -RunE2E
```

## 必须覆盖的真实点击流

`frontend/release-e2e/release-clickthrough.spec.ts` 必须覆盖：

- 打开 deployer 首页。
- 进入部署向导。
- 选择 native 模式。
- 运行环境检查。
- 关闭可选组件。
- 配置 frontend/backend 端口与 CORS。
- 进入启动页。
- 点击 Start All。
- 打开真实 CodeTalk 前端。
- 进入工作空间列表页。
- 新建工作空间。
- 进入设置页。
- 点击 Stop All。
- 验证 backend/frontend 端口释放。

这条 E2E 不允许使用 mock 代替产品主链路。

## 离线依赖证据

发布候选必须在不联网的环境生成依赖清单：

```bash
python3 scripts/generate-offline-sbom.py --output /Volumes/Media/codetalk-release-evidence/sbom.json
```

该命令只读取本地 `backend/requirements.txt`、`frontend/package-lock.json` 和
已安装的 Python 分发元数据，记录输入 SHA256、组件版本和可获得的许可证字段；
产物中的 `network_used` 必须为 `false`，且不得有 `UNRESOLVED` 后端依赖。它是
AC-SEC-005 的可重复基础证据，不替代管理员执行的出站 PCAP、网关日志审查或批准的
离线 bundle 签收。

## Blocker 定义

以下问题属于 release blocker：

- Deployer 无法启动。
- Quickstart / Start All 无法启动 backend + frontend。
- 前端无法 production build。
- 后端 import-time 崩溃。
- 核心 API 崩溃。
- 浏览器 E2E smoke 失败。
- Stop All 后端口未释放。
- 配置保存后实际启动未生效。
- 启动失败时 deployer 没有明确错误，表现为静默卡住。
- 发布候选没有离线 SBOM，或 SBOM 记录了未解析的后端依赖。

以下问题可以进入 release notes，但不阻塞当前内部 release：

- Windows asyncio resource warning。
- pytest unknown mark warning。
- pip upgrade notice。
- 可选组件未配置导致 health degraded。
- 默认端口被占用，但可通过配置切换端口。

## 可选组件策略

GitNexus / CGC / 本机 Agent 当前属于 optional enhancement。旧 Wiki 组件已从当前产品和部署系统中移除，不进入内部发布范围。

Internal Release 不要求全部强制可用，但必须保证：

- 关闭可选组件时，CodeTalk 核心系统仍可部署、启动、使用。
- 开启可选组件时，deployer 必须做端口、路径、依赖检查。
- 可选组件不可用时，前端/后端应降级，不得拖垮核心服务。

每个可选组件进入“推荐启用”状态前，需要单独专项 smoke：

- 安装/配置
- 启动
- health/status
- 最小业务调用
- 停止/重启

## 维护规则

修改部署、启动、端口、配置、核心路由时，必须同步考虑：

1. 是否影响 `scripts/release-gate.ps1`
2. 是否影响 `frontend/release-e2e/release-clickthrough.spec.ts`
3. 是否影响 Stop All 后端口释放
4. 是否改变可选组件的 release 状态
5. 是否需要更新根目录 `README.md`

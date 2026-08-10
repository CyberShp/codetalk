# CodeHub MR 访问规则

## 适用场景

本规则仅在 `issue-regression`（问题单/代码修改回归）场景中强制启用。

## 必需输入

用户必须提供本次问题修复对应的 MR 链接。

用户未提供时，先询问：

```text
请提供本次问题修复对应的 MR 链接。问题单回归分析需要结合 MR 的实际代码修改、
影响范围和评审信息进行分析。
```

## 必需工具

```text
codehub-mcp-server
```

必须通过该 MCP 读取 MR 元数据、分支、Commit、修改文件、Diff、评审意见和可访问的流水线信息。

## 未安装提示模板

```text
当前环境未安装或未连接 codehub-mcp-server，因此我暂时无法读取该 MR 的代码差异、
修改文件和评审信息。

请先安装并连接 codehub-mcp-server：

安装地址：<CODEHUB_MCP_SERVER_INSTALL_URL>

安装完成后，请重新提供或确认 MR 链接，我将继续进行问题修复机制、影响范围和回归测试分析。
```

安装地址占位符由 Skill 维护者填写。Agent 不得猜测、搜索或替换该地址。

## 阻塞状态

工具不可用时：

- Verdict：`BLOCKED`
- block_reason：`codehub_mcp_unavailable`

可以整理问题事实，但不得完成修复机制、影响范围和完整回归分析。

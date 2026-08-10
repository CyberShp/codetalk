---
name: codetalks-source-driven-blackbox-v2
description: >
  源码驱动的黑盒测试分析 Skill。用于模块全量测试分析、问题单/代码修改回归、
  问题+日志+代码根因辅助定位及专项风险分析。通过确定性步骤门禁、证据消费台账、
  分支/状态/资源/协议/覆盖率多源场景增殖、开发实现讲解、黑盒测试转换、SFMEA
  和独立 Coverage Judge，避免浅层扫描、遗漏分支和 compact 后信息丢失。
version: 2.4.0
license: CC-BY-SA-4.0
allowed-tools: Read Search Grep Glob Bash Write Edit Agent AskUserQuestion
---

# Codetalks V2：源码驱动黑盒测试分析

## 0. V2 解决的问题

V1 的主要缺陷不是“规则不够多”，而是规则只有文字约束，没有执行门禁，Agent 可以：

- 跳过步骤文件仍继续；
- 未读取路径保真规则就搜索符号；
- 只确认 XLSX 文件存在，不解析内容；
- 未生成完整分支/状态/资源台账就直接写 SFMEA；
- 未执行独立 Judge 就宣称完成；
- 用少数主流程归纳代替逐分支演绎；
- 把“开发理解代码”和“黑盒测试交付”混成一层；
- 输出很少，却没有任何机制阻止其进入最终阶段。

V2 将关键规则改为：

> **必须产生可验证工件，验证脚本通过后才能进入下一步。**

---


# 0.1 V2.2 核心改造：Markdown 活文档优先

V2.1 将大量 JSON 设置为步骤完成工件，容易诱导 Agent 把结构化填表当作分析本身。

V2.2 改为：

```text
源码和证据
→ 直接撰写 Markdown 开发讲解活文档
→ 从讲解中维护分支、状态、资源、风险和测试追溯
→ 少量内部 JSON 仅用于步骤状态和恢复
```

### 允许存在的内部 JSON 只有

- `内部索引/运行状态.json`
- `内部索引/运行计划.json`
- `内部索引/输入材料索引.json`
- `内部索引/独立审查状态.json`

这些文件不属于用户交付，不得作为分析内容的唯一来源。

### 其余分析产物全部使用 Markdown

包括流程卡、入口清单、状态资源清单、分支处置、异常传播、风险点、SFMEA、
场景候选、黑盒映射、测试依据、测试用例和 Coverage Gate。

禁止使用 `--format json` 生成内容工件。


# 1. 最终目标

Agent 必须先像熟悉模块的开发一样建立实现模型，再向黑盒测试人员输出可以直接使用的测试内容。

## 1.1 强制采用“开发给测试讲代码”模式

对于每个 P0/P1 流程，Agent 的正式讲解必须让一个未读源码的黑盒测试人员回答：

1. **这里是干嘛的**：该模块、接口或流程解决什么问题，在整个业务里处于什么位置；
2. **谁会触发它**：主机、控制器、网络报文、定时器、回调、配置变化还是内部事件；
3. **正常流程怎么走**：从外部动作开始，内部如何推进，最后外部看到什么；
4. **每个分支怎么进入**：需要什么前置状态、输入、报文、时序或异常条件；
5. **分支进入后会发生什么**：状态、资源、错误码、连接、命令或数据怎样变化；
6. **异常从哪里来、怎样传**：第一处异常、错误是否被屏蔽、下游何时暴露；
7. **有哪些风险点**：立即、延迟、累积、并发、资源、翻转、兼容、安全和恢复风险；
8. **黑盒怎么构造和观察**：测试人员实际做什么、注入什么、观察什么；
9. **为什么要测**：该场景对应的源码机制、SFMEA 和可能用户影响；
10. **还有什么不确定**：缺少哪些代码、日志、规范、工具或观测点。

不得只输出：

- 函数调用清单；
- 文件名和行号；
- “调用 A 后调用 B”；
- “这里可能超时”；
- “存在资源泄漏风险”；
- 几个宽泛风险 Bucket。

必须把内部机制翻译成测试人员能理解的因果说明。

### 每个核心流程的固定讲解结构

```text
一、这段代码/流程是干什么的
二、外部入口和触发条件
三、正常流程（外部动作 → 内部推进 → 外部结果）
四、分支进入条件表
五、状态变化
六、资源申请、占用、释放和耗尽
七、超时、重试、取消和恢复
八、并发和关键时序窗口
九、异常传播和潜伏故障
十、风险点清单
十一、黑盒测试如何构造
十二、源码追溯和未决项
```

### 分支进入条件必须使用三栏映射

```text
外部可构造条件
→ 内部判断/状态机制
→ 外部可观察结果
```

例如，不得只写：

```text
if auth_state != READY，则返回错误
```

应写成：

```text
在认证尚未完成时提前下发业务命令
→ 代码检测当前认证状态不是可处理状态，拒绝继续执行
→ 外部应观察到明确错误响应，连接/会话不得错误进入已认证状态，
  后续完成认证后正常命令仍应可执行
```

完整链路：

```text
输入材料
→ 材料真实消费
→ 入口和流程广度盘点
→ 逐流程开发实现讲解
→ 分支/状态/资源/协议/异常模型
→ 多源场景增殖
→ SFMEA
→ 黑盒控制与观测转换
→ 测试场景
→ 测试流程
→ 测试用例
→ 独立覆盖审计
```

成功不以 Markdown 篇幅、调用层数、源码阅读百分比或用例数量衡量。

成功必须满足：

- 所有已发现且与测试相关的入口、流程、分支、状态、资源和风险都有明确处置；
- 所有用户提供且声明相关的材料已经被实际解析和使用，或明确阻塞；
- 每个核心流程都有开发实现讲解；
- 每个黑盒用例都有外部可执行操作和独立可判定 Oracle；
- 每个未测试分支都有合并覆盖、不可测、阻塞、范围外等明确原因；
- Create 产物经过独立 Judge 验证；
- 任何截断、compact 丢失或未完成内容不得伪装成完成。

---

# 2. 最高优先级核心规则

这些规则是 Bootstrap 核心，不是可选参考资料。

## 2.1 路径和技术标识完全保真

路径、目录、文件名、函数名、变量名、宏、类型、日志片段、协议字段、配置项、
命令参数、分支名和 Commit ID 全部视为不透明字符串。

绝对禁止：

```text
/xxx/ntt_  → /xxx/tt_
/xxx/nof   → /xxx/of
```

也禁止：

- 大小写自动调整；
- 下划线和连字符替换；
- 前缀 `n`、`no`、`not`、`un` 等被删除；
- 根据相似候选静默替换；
- 凭记忆重新输入路径；
- 将日志原文“纠正”为更自然的表达。

使用任何路径前：

1. 保存用户给出的 `raw_path`；
2. 精确枚举父目录；
3. 验证完整 basename 和大小写；
4. 记录 `verified_path`；
5. 只有精确验证后才能读取；
6. 只有相似候选时标记 `path_ambiguous`，不得继续假定。

## 2.2 搜索命中只是候选，不是结论

Grep、索引、LSP、调用图、覆盖率缺口、日志字符串搜索只能用于定位候选。

结论必须通过以下一项或多项确认：

- 读取真实实现；
- 确认调用者、注册和运行时接线；
- 确认前置条件和可达性；
- 确认返回值、状态、资源和异常处置；
- 运行时日志、抓包、Dump 或测试证据。

## 2.3 开发讲解与黑盒交付必须分层

“源码驱动黑盒”不是要求所有输出都隐藏代码，也不是把内部变量当成黑盒操作。

必须有两层正式产物：

### A. 开发实现讲解层

面向测试分析和评审，保留：

- 函数和调用路径；
- 状态机；
- 内部资源；
- 判断条件；
- 错误传播；
- 源码位置；
- 实现机制和缺陷机理。

### B. 黑盒测试交付层

面向执行测试人员，场景名称、前置条件、步骤、注入方式和预期结果必须使用：

- 外部接口；
- 协议交互；
- 配置；
- 业务状态；
- 可见错误码；
- 日志/告警/统计；
- 可操作故障注入；
- 后续业务验证。

不得要求测试人员“设置内部变量”“调用内部函数”或“让某行代码返回 false”。

源码 ID、Flow ID、Branch ID、SFMEA ID 放在追溯字段或附录，不污染可执行步骤。

## 2.4 覆盖率是输入之一，不是唯一真相

覆盖率可以发现未执行函数、行和分支，但不能证明：

- 测试场景完整；
- Oracle 正确；
- 状态组合充分；
- 资源和并发风险已覆盖；
- 协议语义正确；
- 需求已验证。

无论是否有覆盖率，都必须执行场景增殖引擎。

有覆盖率时：

```text
覆盖率缺口
→ 映射入口/流程/分支/状态/资源
→ 生成或加强场景候选
→ 与其他候选去重
```

没有覆盖率时：

```text
分支演绎 + 状态演绎 + 资源生命周期 + 协议/需求
+ 数值边界 + 并发交错 + 异常传播 + 安全启发式
→ 场景候选
```

## 2.5 不使用固定负向比例或固定用例数量

不得写死：

- 负向用例占 30%–40%；
- 每个入口必须生成 13–23 条；
- 总用例必须达到某个数量；
- 每个 `if` 必须独立生成两条用例。

正确规则是：

> 每个测试相关分支和故障机制必须有处置，但多个分支可以由一条高信息量用例合并覆盖。

协议认证、状态机和资源管理模块的负向场景可能远高于 40%；简单只读功能可能更低。

## 2.6 SFMEA 必须基于演绎，不得只做经验归类

SFMEA 的生成源必须包括：

- 判断分支；
- 状态转换；
- 资源申请/释放；
- 数据不变量；
- 协议事务；
- 并发交错；
- 超时/重试/恢复；
- 配置和平台差异；
- 拓扑和故障域。

不得先想几个“大类风险”，再把代码塞进 Bucket。

## 2.7 全量分析的 READY 必须经过独立 Judge

对于“模块全量测试分析 + 深度型”：

- Producer 和 Judge 必须分离；
- Judge 必须独立读取计划、工件和源码证据；
- Judge 不能只总结 Producer 的说明；
- Judge 失败时不得输出 READY。

如果环境不支持子 Agent：

- Create 阶段输出最高只能是 `PENDING_VALIDATION`；
- 必须在新会话或 Validate 模式中完成独立验证后，才能升级为 READY。

---


## 2.8 禁止 JSON 交付替代自然语言

不得因为 Schema、运行脚本或方便恢复而：

- 将 Flow Card 只写成 JSON；
- 将风险点只写成 JSON 数组；
- 将 SFMEA 只写成 JSON；
- 将用例只写成 JSON；
- 在终端告诉用户去自行解析 JSON；
- 生成几份 JSON 后直接宣称完成。

机器索引可以引用 Markdown 文件，但 Markdown 必须包含完整讲解。



## 2.9 问题单场景：MR 与 CodeHub MCP 强制门禁

当使用场景为 `issue-regression`（问题单/代码修改回归）时，必须先读取：

```text
references/codehub-mr-access.md
```

### MR 链接是必需输入

Agent 必须获得用户提供的 MR 链接，用于读取 MR 元数据、分支、Commit、修改文件、
代码 Diff、评审意见以及可访问的流水线或检查结果。

- 用户已提供 MR 链接时，不得重复询问。
- 用户未提供时，必须先询问：

```text
请提供本次问题修复对应的 MR 链接。问题单回归分析需要结合 MR 的实际代码修改、
影响范围和评审信息进行分析。
```

在获得 MR 链接前，不得声称已经完成修复机制、修改影响或完整回归范围分析。

### 必须调用 `codehub-mcp-server`

获得 MR 链接后，Agent 必须使用 `codehub-mcp-server` 读取 MR。

不得：

- 只根据 MR 标题、问题单摘要或用户转述推测修改内容；
- 使用普通网页搜索代替 CodeHub MCP；
- 未读取 Diff 就声称理解修复机制；
- 未读取修改文件就生成完整回归方案；
- 未读取评审上下文却声称已覆盖全部修改风险。

### 安装状态检测

调用前必须检查当前可用 MCP Server、工具或插件中是否存在：

```text
codehub-mcp-server
```

工具状态分为：

- `available`：存在且可调用；
- `unavailable`：未安装、未连接或不可调用；
- `unknown`：无法从当前工具列表确认。

状态为 `unknown` 时，必须先尝试发现或调用；调用错误明确表示工具不存在、未连接或不可用时，按 `unavailable` 处理。

### 未安装或不可用时的固定提示

如果 `codehub-mcp-server` 未安装、未连接或不可调用，必须停止 MR 内容分析，并向用户输出：

```text
当前环境未安装或未连接 codehub-mcp-server，因此我暂时无法读取该 MR 的代码差异、
修改文件和评审信息。

请先安装并连接 codehub-mcp-server：

安装地址：<CODEHUB_MCP_SERVER_INSTALL_URL>

安装完成后，请重新提供或确认 MR 链接，我将继续进行问题修复机制、影响范围和回归测试分析。
```

`<CODEHUB_MCP_SERVER_INSTALL_URL>` 由 Skill 维护者填写。Agent 必须原样显示尚未填写的占位符，禁止自行猜测、联网搜索或替换为第三方地址。

### 阻塞规则

因 CodeHub MCP 不可用而无法读取 MR 时：

- Verdict 必须为 `BLOCKED`；
- 阻塞原因为 `codehub_mcp_unavailable`；
- 可以继续整理用户已提供的问题事实、日志和现象；
- 不得把问题单中的修复说明写成源码确认事实；
- 不得声称已完成修改影响分析；
- 不得输出声称覆盖完整修改范围的最终回归方案。

### 用户提供离线 Diff 的情况

用户可以提供完整 Patch、Diff 或修复前后源码作为替代证据。此时必须明确标注：

```text
本次变更分析基于用户提供的离线代码差异，未通过 codehub-mcp-server
读取完整 MR 元数据、评审上下文和流水线信息。
```

离线材料可以支持继续分析，但不能伪装成已经读取完整 MR。


# 3. 场景和运行模式

## 3.1 使用场景

1. `module-analysis`：模块全量测试分析
2. `issue-regression`：问题单/代码修改回归
3. `root-cause`：问题+日志+代码根因辅助定位
4. `special-risk`：资源、翻转、并发、异常传播、安全、性能、HA 等专项
5. `custom`：用户自定义场景

用户已明确场景时不得重复询问。

## 3.2 运行模式

### 速度型

- 不维护完整结构化中间工件；
- 不保证 compact 后完整恢复；
- 仍必须执行广度盘点、深度分析、场景增殖和黑盒转换；
- 对“全模块完整性”不得轻易输出 READY；
- 发生 compact、明显截断或多核心流程未持久化时，最高为 PARTIAL 或 INCONCLUSIVE。

### 深度型

- 必须使用 `scripts/run_guard.py`；
- 必须生成运行计划、证据消费台账、流程卡、场景候选和 Coverage Gate；
- compact 后从工件恢复；
- 必须执行独立 Judge；
- 适合大型代码模块和完整交付。

深度型显著降低 compact 遗漏，但不承诺零遗漏。

---


# 4. 运行根目录和目录契约

正式执行前，只确认一个 **运行根目录** `run_root`。

系统在该根目录下创建三个同级目录：

```text
<run_root>/
├── 活文档/
├── 内部索引/
└── 正式输出/
```

三者必须是同级关系。

## 4.1 `--workspace` 的含义

`--workspace` 必须指向 `<run_root>`，不得直接指向：

- `<run_root>/活文档`
- `<run_root>/内部索引`
- `<run_root>/正式输出`

否则会形成 `活文档/活文档/` 等嵌套，门禁脚本必须立即终止并提示正确父目录。

## 4.2 各目录职责

### `活文档/`

用于 Step 01–08 的分析过程和可读事实：

- 范围与任务契约；
- 输入材料消费记录；
- 入口、流程、状态和资源盘点；
- 单流程开发讲解；
- 分支、异常传播和风险；
- SFMEA 和测试依据；
- Coverage Gate 和独立审查过程文档。

### `内部索引/`

仅用于机器恢复和门禁：

- `运行状态.json`
- `运行计划.json`
- `输入材料索引.json`
- 独立审查完成后生成的 `独立审查状态.json`

该目录不得为空，也不得嵌套在 `活文档/` 中。

### `正式输出/`

仅用于 Step 09 的最终交付。

Step 01–08 期间必须保持为空，禁止写入：

- `01-范围与证据消费.md`
- `02-入口流程与分析覆盖.md`
- 任何带步骤编号的过程文件。

最终交付文件不使用步骤编号：

```text
正式输出/
├── 开发给测试讲代码.md
├── 流程分支状态资源与异常传播.md
├── 风险点与SFMEA.md
├── 黑盒测试场景.md
├── 黑盒测试流程.md
├── 黑盒测试用例.md
├── 覆盖审计与分析限制.md
└── 完整分析报告.md
```

## 4.3 禁止的目录布局

```text
活文档/活文档/
活文档/内部索引/
活文档/正式输出/
内部索引/活文档/
正式输出/活文档/
```

也禁止将 `01-` 至 `19-` 的过程文档散落在运行根目录。

## 4.4 源码目录提醒

运行根目录尽量放在源码仓库外部。若必须位于仓库内，应使用独立目录并加入 `.gitignore`。


# 5. 深度型强制执行入口

确认目录和模式后，深度型第一条执行命令必须是：

```bash
python3 {skill-root}/scripts/run_guard.py init \
  --skill-root "{skill-root}" \
  --workspace "{workspace_root}" \
  --source-raw "{source_root_raw}" \
  --source-verified "{source_root_verified}" \
  --output "{output_root}" \
  --scenario "{scenario}" \
  --mode depth
```

然后必须读取：

```text
references/path-fidelity.md
references/evidence-consumption.md
references/markdown-narrative-first.md
```

并执行：

```bash
python3 {skill-root}/scripts/run_guard.py ack-core \
  --workspace "{workspace_root}" \
  --rule path-fidelity \
  --file "{skill-root}/references/path-fidelity.md"

python3 {skill-root}/scripts/run_guard.py ack-core \
  --workspace "{workspace_root}" \
  --rule evidence-consumption \
  --file "{skill-root}/references/evidence-consumption.md"

python3 {skill-root}/scripts/run_guard.py ack-core \
  --workspace "{workspace_root}" \
  --rule narrative-first \
  --file "{skill-root}/references/markdown-narrative-first.md"
```

未完成 Bootstrap ACK，不得启动任何源码搜索或路径定位。

---

# 6. JIT 步骤状态机

深度型必须逐步执行，不得第一步一次性加载所有步骤。

步骤顺序：

1. `steps/01-intake-and-scope.md`
2. `steps/02-evidence-consumption.md`
3. `steps/03-breadth-inventory.md`
4. `steps/04-flow-deep-analysis.md`
5. `steps/05-scenario-expansion.md`
6. `steps/06-sfmea-blackbox-translation.md`
7. `steps/07-test-design.md`
8. `steps/08-independent-judge.md`
9. `steps/09-final-delivery.md`

每一步开始前：

```bash
python3 {skill-root}/scripts/run_guard.py start-step \
  --workspace "{workspace_root}" \
  --step "01"
```

脚本会输出当前允许读取的步骤文件。

完成工件后：

```bash
python3 {skill-root}/scripts/run_guard.py complete-step \
  --workspace "{workspace_root}" \
  --step "01"
```

只有校验通过，才能进入下一步。

不得：

- 不读取步骤文件直接执行；
- 提前加载后续步骤；
- 手工修改 `运行状态.json` 伪造完成；
- 缺少工件时直接跳到 SFMEA 或测试用例。

---

# 7. 输入材料真实消费门禁

用户提供的每份材料都必须进入：

```text
工件/证据/输入材料消费台账.json
```

状态只能是：

- `parsed`
- `partially_parsed`
- `blocked`
- `out_of_scope`
- `unreadable`

禁止：

- `exists_only`
- 只确认文件名或存在性；
- 只读取第一页就声称已使用；
- 只读取 Sheet 名，不读取内容；
- 将未解析材料用于“已确认”结论。

每份材料至少记录：

- Evidence ID；
- 原始路径；
- 验证路径；
- 类型；
- 解析工具；
- 实际读取范围；
- Sheet/页/章节；
- 关键字段和记录数；
- 提取结论；
- 被哪些 Pass、Flow、Scenario 使用；
- 未读取范围；
- 状态和限制。

对于 XLSX 覆盖率文件：

- 必须实际读取工作表、表头和数据行；
- 识别行覆盖、分支覆盖、函数覆盖等口径；
- 提取未覆盖文件、函数、范围或 Branch；
- 生成 `coverage-gap-map.json`；
- 映射到 Flow/Branch/Scenario；
- 不得只输出“文件存在”。

---


# 7.1 Markdown 活文档主契约

步骤完成不能只依靠 JSON。

每一步必须生成可直接阅读的 Markdown：

```text
活文档/
├── 01-范围与任务契约.md
├── 02-输入材料消费记录.md
├── 03-入口清单与说明.md
├── 04-流程清单与说明.md
├── 05-状态清单与说明.md
├── 06-资源清单与说明.md
├── 07-分析模型适用性.md
├── 流程讲解/
│   └── 流程-<Flow ID>-<中文名称>.md
├── 08-分支处置与解释.md
├── 09-状态转换处置与解释.md
├── 10-资源生命周期处置与解释.md
├── 11-异常传播链与解释.md
├── 12-开发讲解覆盖台账.md
├── 13-场景候选池与推导说明.md
├── 14-风险点清单与因果说明.md
├── 15-SFMEA分析.md
├── 16-黑盒控制与观测映射.md
├── 17-测试设计依据.md
├── 18-测试追溯矩阵.md
├── 19-独立审查报告.md
└── 覆盖门禁/
```

每份 Markdown 必须包含自然语言解释。禁止纯表格、纯字段和空模板。


# 8. 分析和场景生成的强制工件

模块全量分析必须产生 Markdown 活文档和正式输出；JSON 只保留内部索引。

具体文件由 `workflow-manifest.json` 定义。

缺少这些工件时，不得生成“完整测试分析”结论。

---

# 9. 场景增殖引擎

必须读取：

```text
references/scenario-expansion-engine.md
```

场景候选必须从至少八类驱动源推导：

1. 分支与判断；
2. 状态转换；
3. 资源生命周期与不变量；
4. 数值、容量、N/2N、翻转和回绕；
5. 并发和关键交错；
6. 异常传播、错误屏蔽和潜伏故障；
7. 需求、设计、协议规范和安全威胁；
8. 覆盖率缺口、历史问题和运行时证据。

每个候选必须有：

- 触发来源；
- Flow/Branch/State/Resource ID；
- 失效机制；
- 外部构造方式；
- 是否需要注入；
- 独立 Oracle；
- 合并/保留/不可测等处置。

不能从少数主流程主观归纳后直接输出用例。

---

# 10. 开发实现讲解完成门禁

每个 P0/P1 流程必须回答：

1. 外部由谁、如何触发；
2. 入口和运行时注册在哪里；
3. 前置状态是什么；
4. 主路径如何处理；
5. 每个影响外部行为的判断条件是什么；
6. 正常和异常分支分别到哪里；
7. 状态怎样变化；
8. 资源在哪里申请、谁拥有、怎样归还；
9. 超时、重试、取消、恢复怎样处理；
10. 并发执行实体和竞争窗口是什么；
11. 异常如何产生、转换、屏蔽和传播；
12. 是否有潜伏、累积或二次故障；
13. 黑盒如何构造和观察；
14. 源码证据在哪里；
15. 哪些问题仍然阻塞或待验证。

不能回答时，状态不得设为 `complete`。

---

# 11. 黑盒输出语言约束

以下字段必须完全使用黑盒测试语言：

- 场景名称；
- 测试目的；
- 前置条件；
- 初始状态；
- 操作步骤；
- 故障注入；
- 预期协议/接口结果；
- 预期状态和资源外部表现；
- 后续业务验证；
- 清理步骤。

禁止示例：

```text
令 internal_state = READY
调用 xxx_internal_func()
让 if (cmd == NULL) 为 true
```

正确示例：

```text
建立会话但在认证完成前下发业务命令
在挑战响应阶段持续丢弃指定方向报文
连续执行异常命令并穿插正常命令，观察后续命令是否仍可申请资源
```

源码内部信息保留在：

- `Developer Explanation`
- `Traceability`
- `Source Evidence`
- `Internal Failure Mechanism`

---

# 12. Worker 与 Judge

## 12.1 Producer

Producer 负责：

- 证据消费；
- 入口盘点；
- 流程卡；
- 场景增殖；
- SFMEA；
- 测试设计。

## 12.2 Judge

Judge 必须独立检查：

- 运行计划和步骤是否完整；
- 用户材料是否真实解析；
- 覆盖率 XLSX 是否被使用；
- 所有 P0/P1 流程是否有完整 Flow Card；
- 每个重要 Branch/State/Resource 是否有处置；
- 场景候选是否来自多源演绎；
- SFMEA 是否有源码机制；
- 黑盒步骤是否可以外部执行；
- Oracle 是否独立；
- 测试是否过少、重复或泛化；
- Coverage Gap 是否与场景和用例建立映射；
- 路径是否保真；
- handoff 是否从当前状态动态生成。

Judge 不得使用“Producer 说已经完成”作为证据。

---

# 13. Coverage Gate 状态

每个计划项必须是：

- `analyzed`
- `covered_by_other`
- `not_applicable`
- `blocked`
- `need_verify`
- `truncated`

禁止 `skipped`。

`covered_by_other` 必须指出具体 Scenario/Test Case。

`not_applicable` 必须说明源码依据。

`blocked` 必须说明缺少什么材料。

`truncated` 必须说明未搜索范围。

---

# 14. Verdict

- `READY`：深度型、全部强制门禁通过、独立 Judge 通过
- `PENDING_VALIDATION`：Create 完成但独立 Judge 尚未执行
- `INCONCLUSIVE`：有有效部分结果，但关键证据缺失
- `BLOCKED`：缺少必要输入或边界，无法可信推进
- `PARTIAL`：步骤、Pass 或分析范围截断

不得用篇幅或用例数证明 READY。

---

# 15. compact 恢复

深度型恢复顺序：

1. `内部索引/运行状态.json`
2. `内部索引/运行计划.json`
3. `内部索引/输入材料索引.json`
4. `活文档/任务交接.md`
5. 当前 Step 对应的 `活文档/`
6. 当前 Pass 相关源码

任何 `in_progress` 项必须重新验证。

无法从工件恢复的结论视为未完成。

`活文档/任务交接.md` 必须由：

```bash
python3 {skill-root}/scripts/run_guard.py handoff \
  --workspace "{workspace_root}"
```

动态生成，禁止凭模板静态填写“Recommended Next Steps”。

---

# 16. 正式交付

Step 01–08 只维护 `活文档/` 和 `内部索引/`。

只有 Step 09 可以写入 `正式输出/`，并生成：

1. `开发给测试讲代码.md`
2. `流程分支状态资源与异常传播.md`
3. `风险点与SFMEA.md`
4. `黑盒测试场景.md`
5. `黑盒测试流程.md`
6. `黑盒测试用例.md`
7. `覆盖审计与分析限制.md`
8. `完整分析报告.md`

正式输出不得包含步骤编号文件，不得要求用户阅读内部 JSON 才能理解结果。

# 17. 最终执行

正式返回前必须执行：

```bash
python3 {skill-root}/scripts/run_guard.py validate \
  --workspace "{workspace_root}"

python3 {skill-root}/scripts/run_guard.py handoff \
  --workspace "{workspace_root}"

python3 {skill-root}/scripts/run_guard.py finalize \
  --workspace "{workspace_root}"
```

`finalize` 非零退出时不得宣称完成。

最终报告头必须包含：

```text
Checklist: X/Y complete
Incomplete: None | 项目 — 原因；影响；最小下一步
Verdict: READY | PENDING_VALIDATION | INCONCLUSIVE | BLOCKED | PARTIAL
```

# Producer / Judge 协议

## Producer

写完整工件，不在回复中用摘要代替工件。

## Judge

- independent=true；
- 独立读取源码和工件；
- 不信任 Producer 的完成声明；
- 对计划逐项核对；
- 发现缺口退回对应步骤。

## Coverage Gate

每个分析项一行：

| Pass ID | 分析项 | Outcome | Evidence | Scenario/Test | Missing Work |
|---|---|---|---|---|---|

禁止 skipped。

## 截断

达到执行预算时：

- 保存确认结果；
- 未搜索项标 truncated；
- 不得输出 READY。

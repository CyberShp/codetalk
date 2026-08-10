# 覆盖率使用规则

## 前置使用

解析覆盖率并生成缺口地图：

- 文件；
- 函数；
- 行；
- 分支；
- 未匹配路径；
- 覆盖口径。

映射：

```text
Coverage Gap → Flow/Branch/State/Resource → Scenario Candidate
```

## 后置使用

测试设计完成后验证：

```text
Coverage Gap → Scenario → Test Flow → Test Case → Oracle
```

## 限制

覆盖率是执行证据，不是测试正确性证明。

高覆盖可能仍缺：

- 错误 Oracle；
- 状态组合；
- 并发时序；
- 资源耗尽；
- 协议非法序列；
- 安全攻击；
- 长稳和恢复。

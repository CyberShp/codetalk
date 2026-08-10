# 多源场景增殖引擎

## 目标

无论是否有覆盖率，都从实现模型系统性推导场景。

## Generator A：分支演绎

对测试相关的：

- if/else
- switch case/default
- error return
- early return
- guard condition
- feature flag
- compile condition

逐项记录：

- 条件；
- true/false 或 case 的后续行为；
- 外部触发可能性；
- 可观察后果；
- 候选或 disposition。

不是每个分支都必须单独一条用例，但不能消失。

## Generator B：状态演绎

- 每个合法状态转换；
- 非法状态下收到合法事件；
- 合法状态下收到非法/重复/迟到事件；
- 中间态断链、重试、配置变化；
- 状态部分更新和模块间状态不一致。

## Generator C：资源和不变量

- 申请成功/失败；
- 申请后中途失败；
- 正常/异常/超时/断链释放；
- 重复释放、错误所有者释放；
- 资源耗尽；
- 释放后重新申请；
- 实体、计数、链表和 Bitmap 一致性；
- N、N+1、重复 N 轮和 2N。

## Generator D：数值和翻转

根据实际类型和计算检查：

- 0、1、N-1、N、N+1；
- 2N-1、2N、2N+1；
- 类型最大值前后；
- wraparound 0/1；
- 有符号/无符号；
- 大小端；
- head-tail、max-used；
- 序列号/Tag/Generation 重用。

只有代码机制支持时才生成，不机械灌水。

## Generator E：并发交错

识别关键窗口：

```text
A 检查状态
B 改变状态
A 使用旧状态
```

检查：

- 正常完成 vs 超时；
- 命令完成 vs 断链；
- 旧响应 vs 新命令；
- 清理 vs 新建；
- 配置更新 vs 业务运行；
- 双重完成/释放；
- 锁外对象生命周期。

## Generator F：异常传播

从第一处异常向下游追踪：

- 错误码忽略/覆盖/转换；
- 部分初始化；
- 当前外部成功、内部异常；
- 下游正常分支消费异常状态；
- 延迟、累积、级联和二次故障。

## Generator G：需求、协议、安全和配置

- 合法/非法/保留字段；
- 顺序、重传、重放和幂等；
- 协商和降级；
- 算法组合；
- 安全伪造、降级、DoS；
- 动态配置和平台差异。

## Generator H：覆盖率和历史证据

- 未覆盖函数、行和 Branch；
- 覆盖下降；
- 历史问题和复现路径；
- 日志中的第一异常；
- 生产告警；
- 已有测试薄弱 Oracle。

覆盖率只增殖和优先化，不替代 A~G。

## 候选处置

- retain
- merge_into
- covered_by_other
- not_testable
- not_applicable
- blocked
- need_verify

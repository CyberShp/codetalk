# 输入材料真实消费规则

## 目的

防止 Agent 只确认文件存在，却声称已使用其中证据。

## 每份材料必须记录

- id
- type
- raw_path
- verified_path
- parser
- consumed_ranges
- sheets/pages/sections
- records_parsed
- extracted_facts
- used_by_passes
- unread_ranges
- status
- limitations

## 状态

- parsed
- partially_parsed
- blocked
- out_of_scope
- unreadable

禁止 `exists_only`。

## XLSX

必须记录：

- 工作表名；
- 表头；
- 实际解析行数；
- 公式值或显示值的口径；
- 覆盖率类型；
- 未覆盖数据；
- 与源码路径的匹配规则；
- 映射失败项。

## 材料冲突

设计表示“应该如何”；源码表示“当前如何”；日志表示“当时实际发生什么”。

冲突必须并列展示，不得静默选一个。

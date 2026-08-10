# 路径和标识符保真

本文件必须在任何源码、符号和日志定位前读取并 ACK。

## 原则

技术标识不是自然语言。

保存：

- raw_value
- verified_value
- source
- case_sensitive
- symlink_target
- verification_status

## 禁止

- `/xxx/ntt_` 改成 `/xxx/tt_`
- `/xxx/nof` 改成 `/xxx/of`
- 大小写折叠
- 下划线/连字符替换
- 模糊候选静默代替
- 凭记忆重新输入
- 日志字符串自动纠错

## 精确验证

1. 获取父目录；
2. 精确枚举；
3. 比较完整 basename；
4. 检查大小写；
5. 读取精确文件；
6. 不存在时输出原值和候选，标记 `path_ambiguous`。

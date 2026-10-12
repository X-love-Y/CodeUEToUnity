# 翻译单元

- 单元名称: `{{UNIT_NAME}}`
- 源文件: `{{SOURCE_FILES}}`
- 包含的 UE 类型: `{{DECLARED_TYPES}}`

# 这个单元用到的映射表条目

以下是从符号映射表中自动筛选出的、与本单元相关的条目. **严格按此对应关系翻译**, 尤其是 `note` 里写的语义差异:

{{MAPPING_TABLE}}

# 映射表尚未覆盖的 UE 符号

以下符号在映射表里找不到对应. 你需要**基于自己的 UE/Unity 知识判断**如何翻译, 并把不确定的写进 `notes`(`severity=manual`):

{{UNMAPPED_SYMBOLS}}

# 单元源码

{{SOURCE_CODE}}

# 要求

按系统提示的 JSON 格式输出. 特别注意:

- 保持原有功能完整, 不要省略任何逻辑分支.
- 该补的 Unity 侧基础设施(重力、跳跃、落地检测、弹簧臂)要补全.
- 无法确定的地方写进 `todos`, 交给人在编辑器里处理.

{{FIX_SECTION}}

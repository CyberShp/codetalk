"""JSON schemas for structured LLM output validation.

These schemas define the expected structure of LLM responses for each report type.
They are used for optional validation -- the pipeline does not enforce strict JSON
output from the LLM (reports are markdown), but these schemas help validate
intermediate structured data like module summaries.
"""

# Schema for a single module summary (output of MODULE_SUMMARY_PROMPT)
MODULE_SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "module_name": {
            "type": "string",
            "description": "模块名称",
        },
        "responsibility": {
            "type": "string",
            "description": "模块职责（一句话）",
        },
        "core_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["name", "role"],
            },
            "description": "核心组件列表（3-5 个）",
        },
        "external_interfaces": {
            "type": "array",
            "items": {"type": "string"},
            "description": "对外接口列表",
        },
        "internal_dependencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "内部依赖的关键调用链",
        },
        "tech_highlights": {
            "type": "array",
            "items": {"type": "string"},
            "description": "技术要点",
        },
    },
    "required": ["module_name", "responsibility"],
}

# Schema for report metadata (common header for all generated reports)
REPORT_METADATA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "report_type": {
            "type": "string",
            "enum": [
                "module_map",
                "business_flow",
                "source_reading",
                "test_design",
                "requirements",
                "traceability",
            ],
            "description": "报告类型标识",
        },
        "task_id": {
            "type": "string",
            "description": "关联的任务 ID",
        },
        "generated_at": {
            "type": "string",
            "format": "date-time",
            "description": "生成时间（ISO 8601）",
        },
        "model": {
            "type": "string",
            "description": "使用的 LLM 模型名称",
        },
        "token_usage": {
            "type": "object",
            "properties": {
                "prompt_tokens": {"type": "integer"},
                "completion_tokens": {"type": "integer"},
                "total_tokens": {"type": "integer"},
            },
        },
    },
    "required": ["report_type", "task_id", "generated_at"],
}

# Mapping of report types to their output filenames
REPORT_FILE_MAP: dict[str, str] = {
    "module_map": "01-项目与模块地图.md",
    "business_flow": "02-关键业务流程分析.md",
    "source_reading": "03-源码定向阅读记录.md",
    "test_design": "04-测试设计输入.md",
    "requirements": "05-需求与设计理解.md",
    "traceability": "06-需求设计代码追踪.md",
}

import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import get_db

router = APIRouter(prefix="/api/prompts", tags=["提示词模板"])

SYSTEM_TEMPLATE_ID = "system-default"

DEFAULT_TEMPLATE_CONTENT = """\
你是一位资深代码分析专家。请根据用户的分析需求，对目标代码仓库进行系统性深度分析。

## 分析目标
{analysis_focus}

## 分析步骤

### 第一步：项目结构总览
使用 GitNexus 读取项目的文件结构和目录层级，了解项目的整体组织方式，识别主要技术栈和构建工具。

### 第二步：模块映射
识别项目中的核心模块、子系统及其边界。梳理模块间的依赖关系，绘制模块依赖地图。

### 第三步：入口点识别
找到与分析目标直接相关的入口点（main 函数、API 端点、事件处理器、配置入口等），标注其所在文件和行号。

### 第四步：调用链分析
从入口点出发，追踪关键的函数调用链和数据流向。重点关注：
- 核心业务逻辑的执行路径
- 跨模块的调用关系
- 异步/回调/事件驱动的控制流

### 第五步：源代码阅读清单
列出与分析目标最相关的源代码文件清单，按阅读优先级排序，并说明每个文件的核心职责和阅读要点。

### 第六步：风险点识别
使用 GitNexus 识别代码中的潜在风险点，包括但不限于：
- 安全风险（注入、越权、敏感数据泄露、不安全的加密等）
- 性能隐患（N+1 查询、内存泄漏、阻塞操作、无限循环等）
- 可维护性（重复代码、过度耦合、魔法数字、缺少错误处理等）
- 兼容性（API 版本、依赖版本、平台差异等）

### 第七步：后续分析建议
基于以上分析结果，给出后续深入分析的建议方向和优先级，包括需要重点关注的模块、建议补充的测试、以及可能需要重构的区域。

## 输出要求
- 所有输出使用中文
- 每个步骤给出具体的分析结论，而非泛泛而谈
- 引用具体的文件路径、函数名、类名
- 用 Markdown 格式组织输出，层次清晰
"""


class PromptTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=32_000)


class PromptTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=32_000)


class PromptTemplateResponse(BaseModel):
    id: str
    name: str
    content: str
    is_system: bool
    created_at: str


def _row_to_template(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["is_system"] = bool(d.get("is_system", 0))
    return d


async def seed_default_template(db: aiosqlite.Connection) -> None:
    async with db.execute(
        "SELECT id FROM prompt_templates WHERE id = ?", (SYSTEM_TEMPLATE_ID,)
    ) as cur:
        if await cur.fetchone():
            return

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO prompt_templates (id, name, content, is_system, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (SYSTEM_TEMPLATE_ID, "默认分析模板", DEFAULT_TEMPLATE_CONTENT, now),
    )
    await db.commit()


@router.get("", response_model=list[PromptTemplateResponse])
async def list_templates(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM prompt_templates ORDER BY is_system DESC, created_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_template(r) for r in rows]


@router.get("/{tpl_id}", response_model=PromptTemplateResponse)
async def get_template(tpl_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    return _row_to_template(row)


@router.post("", response_model=PromptTemplateResponse, status_code=201)
async def create_template(
    data: PromptTemplateCreate, db: aiosqlite.Connection = Depends(get_db)
):
    if not data.name.strip():
        raise HTTPException(status_code=422, detail="模板名称不能为空")

    tpl_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO prompt_templates (id, name, content, is_system, created_at) "
        "VALUES (?, ?, ?, 0, ?)",
        (tpl_id, data.name.strip(), data.content, now),
    )
    await db.commit()

    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_template(row)


@router.put("/{tpl_id}", response_model=PromptTemplateResponse)
async def update_template(
    tpl_id: str,
    data: PromptTemplateUpdate,
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")

    existing = dict(row)
    if existing.get("is_system"):
        raise HTTPException(status_code=403, detail="系统模板不可修改")

    _ALLOWED_FIELDS = frozenset({"name", "content"})
    updates = {
        k: v for k, v in data.model_dump(exclude_none=True).items()
        if k in _ALLOWED_FIELDS
    }
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if not updates["name"]:
            raise HTTPException(status_code=422, detail="模板名称不能为空")
    if "content" in updates:
        updates["content"] = updates["content"].strip()
        if not updates["content"]:
            raise HTTPException(status_code=422, detail="模板内容不能为空")

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE prompt_templates SET {set_clause} WHERE id = ?",
            (*updates.values(), tpl_id),
        )
        await db.commit()

    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_template(row)


@router.delete("/{tpl_id}", status_code=204)
async def delete_template(tpl_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    if dict(row).get("is_system"):
        raise HTTPException(status_code=403, detail="系统模板不可删除")

    await db.execute("DELETE FROM prompt_templates WHERE id = ?", (tpl_id,))
    await db.commit()

# Phase 4A: 项目/仓库管理集成

**前置依赖：Phase 2A (后端) + Phase 2B (前端) 完成**
**可与其他 Phase 4 任务并行**

## 任务目标

将后端项目/仓库 CRUD API 与前端 Assets 页面串联，实现完整的项目和仓库管理功能。

## 步骤

### 1. 后端 API 实现

确保 `backend/app/api/projects.py` 中的路由已完整实现：
- `GET /api/projects` — 分页列表，支持 name 搜索
- `POST /api/projects` — 创建项目
- `GET /api/projects/{id}` — 项目详情（包含仓库列表和统计）
- `PUT /api/projects/{id}` — 更新项目名/描述
- `DELETE /api/projects/{id}` — 删除项目（级联删除仓库和任务）
- `GET /api/projects/{id}/repos` — 项目下仓库列表
- `POST /api/projects/{id}/repos` — 添加仓库

仓库创建 API 需处理三种来源：

```python
@router.post("/api/projects/{project_id}/repos")
async def add_repo(project_id: UUID, repo: RepositoryCreate):
    if repo.source_type == "git_url":
        # 后台克隆到 /data/repos/{project_id}/{repo_name}/
        local_path = await source_manager.clone_git(repo.source_uri, repo.branch)
    elif repo.source_type == "local_path":
        # 验证路径存在
        local_path = source_manager.validate_local_path(repo.source_uri)
    elif repo.source_type == "zip_upload":
        # 保存上传文件并解压
        local_path = await source_manager.extract_zip(repo.source_uri)

    # 保存到数据库
    ...
```

### 2. ZIP 上传端点

新增文件上传端点：

```python
@router.post("/api/upload")
async def upload_file(file: UploadFile):
    # 保存到 /data/repos/uploads/{uuid}/
    # 如果是 zip/tar.gz，解压
    # 返回解压后的路径
    ...
```

### 3. 前端 — Assets 页面真实数据

替换 mock 数据，接入真实 API：

**左侧项目树：**
- 调用 `GET /api/projects` 获取项目列表
- 展开项目时调用 `GET /api/projects/{id}/repos` 获取仓库
- 选中项目/仓库时右侧显示详情

**仓库表格：**
- 显示：名称、来源类型（图标区分 Git/Local/ZIP）、分支、最后分析时间
- 操作：查看详情、删除、创建分析任务

**添加项目 Modal：**
- 项目名称 + 描述

**添加仓库 Modal：**
- 来源类型选择（3 个 tab：Git URL / 本地路径 / 上传压缩包）
- Git URL tab：URL 输入 + Branch 输入
- 本地路径 tab：路径输入
- 上传 tab：文件拖拽上传区域
- 仓库名称（自动从 URL/路径提取，可编辑）

### 4. Git 克隆进度

克隆大仓库可能很慢，需要反馈：
- 后端：克隆作为后台任务，通过 WebSocket 推送进度
- 前端：仓库卡片上显示 "Cloning..." 状态 + 进度

## 验收标准

- [ ] 可创建/编辑/删除项目
- [ ] 可通过 Git URL 添加仓库（自动克隆）
- [ ] 可通过本地路径添加仓库
- [ ] 可通过 ZIP 上传添加仓库
- [ ] 项目树正确展示项目和仓库层级
- [ ] 删除项目时级联删除相关仓库和任务

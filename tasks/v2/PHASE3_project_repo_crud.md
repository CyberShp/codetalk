# Phase 3: 项目/仓库 CRUD 管理

**前置依赖：Phase 1A + 1B 完成**
**可与 Phase 2 并行（前端部分在 Phase 2 之后集成）**
**完成后解锁：Phase 4**
**预估复杂度：中**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。本阶段是纯 CRUD，不涉及分析。

## 目标

实现项目和仓库的增删查改，支持三种代码来源（Git URL、本地路径、ZIP 上传）。将前端 Assets 页面接入真实 API。

## 步骤

### 1. 后端 — Projects API (`backend/app/api/projects.py`)

将路由桩替换为真实实现：

- `GET /api/projects` — 分页列表，支持 name 搜索
- `POST /api/projects` — 创建项目
- `GET /api/projects/{id}` — 项目详情（含仓库列表+统计）
- `PUT /api/projects/{id}` — 更新
- `DELETE /api/projects/{id}` — 删除（级联删除仓库和任务）
- `GET /api/projects/{id}/repos` — 项目下仓库列表
- `POST /api/projects/{id}/repos` — 添加仓库

### 2. 仓库创建 — 三种来源

```python
@router.post("/api/projects/{project_id}/repos")
async def add_repo(project_id: UUID, repo: RepositoryCreate, db: AsyncSession = Depends(get_db)):
    match repo.source_type:
        case "git_url":
            # 后台异步克隆到 /data/repos/{project_id}/{repo_name}/
            local_path = await git_service.clone_repo(
                repo.source_uri, repo.branch,
                f"/data/repos/{project_id}/{repo.name}"
            )
        case "local_path":
            local_path = source_manager.validate_local_path(repo.source_uri)
        case "zip_upload":
            local_path = repo.source_uri  # upload 端点已解压

    # 保存到数据库
    ...
```

### 3. ZIP 上传端点

```python
@router.post("/api/upload")
async def upload_file(file: UploadFile):
    # 保存到 /data/repos/uploads/{uuid}/
    # 如果是 zip/tar.gz，解压
    # 返回解压后的路径
    ...
```

### 4. Git Service 实现 (`backend/app/services/git_service.py`)

```python
async def clone_repo(url: str, branch: str, target_dir: str) -> str:
    # 用 subprocess 执行 git clone
    # git clone --branch {branch} --depth 1 {url} {target_dir}
    ...

async def pull_repo(local_path: str) -> None:
    # git -C {local_path} pull
    ...
```

### 5. 前端 — Assets 页面接入真实 API

替换 mock 数据：

**左侧项目树：**
- `GET /api/projects` → 项目列表
- 展开项目 → `GET /api/projects/{id}/repos` → 仓库列表

**仓库表格：**
- 列：名称、来源类型（图标区分 Git/Local/ZIP）、分支、最后分析时间
- 操作：删除、创建分析任务

**添加项目 Modal：**
- 项目名称 + 描述

**添加仓库 Modal：**
- 3 个 Tab：Git URL / 本地路径 / 上传压缩包
- Git URL tab：URL + Branch 输入
- 本地路径 tab：路径输入
- 上传 tab：文件拖拽区域

### 6. 删除仓库时清理

- 数据库级联删除关联任务和 tool_runs
- 如果是 git_url 或 zip_upload 来源，删除 /data/repos/ 下的文件

## 验收标准

- [ ] 可创建/编辑/删除项目
- [ ] 可通过 Git URL 添加仓库（自动克隆）
- [ ] 可通过本地路径添加仓库（验证路径存在）
- [ ] 可通过 ZIP 上传添加仓库（解压到指定目录）
- [ ] 前端 Assets 页面展示真实数据
- [ ] 删除项目时级联清理

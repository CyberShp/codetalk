import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project
from app.models.repository import Repository
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.repository import RepositoryCreate, RepositoryResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    responses = []
    for p in projects:
        count_q = await db.execute(select(sa_func.count()).where(Repository.project_id == p.id))
        responses.append(ProjectResponse(
            id=p.id, name=p.name, description=p.description,
            created_at=p.created_at, updated_at=p.updated_at,
            repo_count=count_q.scalar() or 0,
        ))
    return responses


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    project = Project(name=data.name, description=data.description)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at, updated_at=project.updated_at,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    count_q = await db.execute(select(sa_func.count()).where(Repository.project_id == project.id))
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at, updated_at=project.updated_at,
        repo_count=count_q.scalar() or 0,
    )


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: uuid.UUID, data: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    await db.commit()
    await db.refresh(project)
    count_q = await db.execute(select(sa_func.count()).where(Repository.project_id == project.id))
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at, updated_at=project.updated_at,
        repo_count=count_q.scalar() or 0,
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)
    await db.commit()


@router.get("/{project_id}/repositories", response_model=list[RepositoryResponse])
async def list_repositories(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await db.execute(
        select(Repository).where(Repository.project_id == project_id).order_by(Repository.created_at.desc())
    )
    return [RepositoryResponse.model_validate(r) for r in result.scalars().all()]


@router.post("/{project_id}/repositories", response_model=RepositoryResponse, status_code=201)
async def add_repository(project_id: uuid.UUID, data: RepositoryCreate, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = Repository(
        project_id=project_id,
        name=data.name,
        source_type=data.source_type.value,
        source_uri=data.source_uri,
        branch=data.branch,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return RepositoryResponse.model_validate(repo)

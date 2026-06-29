from fastapi import APIRouter

from app.api.analysis_global import router as analysis_global_router
from app.api.agent_workbench import router as agent_workbench_router
from app.api.ai_conversations import router as ai_conversations_router
from app.api.projects import router as projects_router
from app.api.tasks import router as tasks_router
from app.api.tools import router as tools_router
from app.api.settings import router as settings_router
from app.api.repos import router as repos_router
from app.api.gitnexus_proxy import router as gitnexus_router
from app.api.components import router as components_router
from app.api.ws import router as ws_router
from app.api.repo_graph import router as repo_graph_router
from app.api.repo_analysis import router as repo_analysis_router

api_router = APIRouter()
api_router.include_router(analysis_global_router)
api_router.include_router(agent_workbench_router)
api_router.include_router(ai_conversations_router)
api_router.include_router(projects_router)
api_router.include_router(tasks_router)
api_router.include_router(tools_router)
api_router.include_router(settings_router)
api_router.include_router(repos_router)
api_router.include_router(gitnexus_router)
api_router.include_router(components_router)
api_router.include_router(ws_router)
# Repo-centric endpoints keyed by repo_id directly.
api_router.include_router(repo_graph_router)
api_router.include_router(repo_analysis_router)

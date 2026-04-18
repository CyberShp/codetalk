from fastapi import APIRouter

from app.api.chat import router as chat_router
from app.api.projects import router as projects_router
from app.api.tasks import router as tasks_router
from app.api.tools import router as tools_router
from app.api.settings import router as settings_router
from app.api.repos import router as repos_router
from app.api.gitnexus_proxy import router as gitnexus_router
from app.api.components import router as components_router
from app.api.wiki import router as wiki_router
from app.api.ws import router as ws_router
from app.api.repo_wiki import router as repo_wiki_router
from app.api.repo_chat import router as repo_chat_router
from app.api.repo_graph import router as repo_graph_router
from app.api.repo_analysis import router as repo_analysis_router
from app.api.ws_chat import router as ws_chat_router

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(projects_router)
api_router.include_router(tasks_router)
api_router.include_router(tools_router)
api_router.include_router(settings_router)
api_router.include_router(repos_router)
api_router.include_router(gitnexus_router)
api_router.include_router(components_router)
api_router.include_router(wiki_router)
api_router.include_router(ws_router)
# Repo-centric endpoints (wiki/chat/graph keyed by repo_id directly)
api_router.include_router(repo_wiki_router)
api_router.include_router(repo_chat_router)
api_router.include_router(repo_graph_router)
api_router.include_router(ws_chat_router)
api_router.include_router(repo_analysis_router)

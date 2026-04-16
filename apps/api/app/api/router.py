from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import analytics, auth, chat, notebooks, notes, quizzes, sources

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(notebooks.router, tags=["notebooks"])
api_router.include_router(sources.router, tags=["sources"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(notes.router, tags=["notes"])
api_router.include_router(quizzes.router, tags=["quizzes"])
api_router.include_router(analytics.router, tags=["analytics"])


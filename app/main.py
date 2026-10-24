"""Ponto de entrada da aplicação FastAPI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.factory import criar_provedor
from app.routes import health


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Cria o provedor já aqui: configuração inválida (ex.: gemini em production) impede o start.
    provedor = llm or criar_provedor(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await provedor.aclose()

    app = FastAPI(title="Conexão Futuro", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.llm = provedor
    app.include_router(health.router)
    return app


app = create_app()

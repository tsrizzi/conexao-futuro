"""Páginas HTML (Jinja2). O conteúdo dinâmico do chat e do resultado vem da API."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.agent.formulario import carregar_formulario
from app.web.markdown import para_html

RAIZ = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=RAIZ / "app" / "templates")
router = APIRouter()


@lru_cache
def _aviso_privacidade() -> Markup:
    return para_html((RAIZ / "docs" / "AVISO_PRIVACIDADE.md").read_text(encoding="utf-8"))


def _pagina(request: Request, nome: str, **contexto: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        request, nome, {"edicao_cnct": request.app.state.edicao_cnct, **contexto}
    )


@router.get("/", response_class=HTMLResponse)
async def inicio(request: Request) -> HTMLResponse:
    return _pagina(request, "inicio.html", total_cursos=request.app.state.total_cursos)


@router.get("/chat", response_class=HTMLResponse)
async def chat(request: Request) -> HTMLResponse:
    return _pagina(request, "chat.html")


@router.get("/resultado", response_class=HTMLResponse)
async def resultado(request: Request) -> HTMLResponse:
    return _pagina(request, "resultado.html")


@router.get("/privacidade", response_class=HTMLResponse)
async def privacidade(request: Request) -> HTMLResponse:
    return _pagina(request, "privacidade.html", conteudo=_aviso_privacidade())


@router.get("/formulario", response_class=HTMLResponse)
async def formulario(request: Request) -> HTMLResponse:
    dados = carregar_formulario()
    return _pagina(request, "formulario.html", perguntas=dados.perguntas, escala=dados.escala)


@router.get("/codigo", response_class=HTMLResponse)
async def codigo(request: Request) -> HTMLResponse:
    return _pagina(request, "codigo.html")

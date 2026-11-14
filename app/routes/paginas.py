"""Páginas HTML (Jinja2). O conteúdo dinâmico do chat e do resultado vem da API."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.agent.formulario import carregar_formulario
from app.web.entrada import codigo_liberado
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


# As páginas são síncronas: conferir a liberação consulta o banco (o FastAPI as roda num
# pool de threads, sem travar o chat).


def _porta() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def inicio(request: Request) -> Response:
    """Sem código de entrada válido, a página inicial é a porta (seção 4.4)."""
    if codigo_liberado(request) is None:
        return _pagina(request, "entrada.html")
    return _pagina(request, "inicio.html", total_cursos=request.app.state.total_cursos)


@router.get("/chat", response_class=HTMLResponse)
def chat(request: Request) -> Response:
    if codigo_liberado(request) is None:
        return _porta()
    return _pagina(request, "chat.html")


@router.get("/resultado", response_class=HTMLResponse)
def resultado(request: Request) -> Response:
    if codigo_liberado(request) is None:
        return _porta()
    return _pagina(request, "resultado.html")


@router.get("/privacidade", response_class=HTMLResponse)
async def privacidade(request: Request) -> HTMLResponse:
    return _pagina(request, "privacidade.html", conteudo=_aviso_privacidade())


@router.get("/formulario", response_class=HTMLResponse)
def formulario(request: Request) -> Response:
    if codigo_liberado(request) is None:
        return _porta()
    dados = carregar_formulario()
    return _pagina(request, "formulario.html", perguntas=dados.perguntas, escala=dados.escala)


@router.get("/codigo", response_class=HTMLResponse)
def codigo(request: Request) -> Response:
    if codigo_liberado(request) is None:
        return _porta()
    return _pagina(request, "codigo.html")

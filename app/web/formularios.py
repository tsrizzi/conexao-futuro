"""Utilitários das áreas com login (painel do professor e admin): páginas sem cache,
formulários urlencoded sem o python-multipart e redirecionamentos."""

from typing import Any
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import AppEnv
from app.routes.paginas import templates

TAMANHO_MAXIMO_FORM = 4_000
MENSAGEM_TENTATIVAS = "Muitas tentativas. Espere uns minutos antes de tentar de novo."
MENSAGEM_EXPIROU = "A página expirou. Recarregue e tente de novo."


async def ler_formulario(request: Request) -> dict[str, str]:
    corpo = await request.body()
    if len(corpo) > TAMANHO_MAXIMO_FORM:
        raise HTTPException(status_code=413, detail="Formulário grande demais.")
    campos = parse_qs(corpo.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {chave: valores[0].strip() for chave, valores in campos.items()}


def cookie_seguro(request: Request) -> bool:
    return bool(request.app.state.settings.app_env == AppEnv.PRODUCTION)


def pagina_privada(
    request: Request, nome: str, status_code: int = 200, **contexto: Any
) -> HTMLResponse:
    resposta = templates.TemplateResponse(
        request,
        nome,
        {"edicao_cnct": request.app.state.edicao_cnct, **contexto},
        status_code=status_code,
    )
    resposta.headers["Cache-Control"] = "no-store"
    resposta.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resposta


def ir(caminho: str) -> RedirectResponse:
    return RedirectResponse(caminho, status_code=303)

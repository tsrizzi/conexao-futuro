"""API do chat: respostas em streaming (Server-Sent Events) sobre POST.

Cada resposta termina com o evento `sessao`, que traz a sessão assinada para o navegador
guardar e reenviar na próxima mensagem (ver app/web/token.py).
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.exportar import para_exibicao, texto_resultado
from app.agent.orchestrator import (
    Evento,
    EventoAviso,
    EventoErro,
    EventoEstado,
    EventoOpcoes,
    EventoProgresso,
    EventoRecomendacao,
    EventoTexto,
    Orquestrador,
    Sessao,
)
from app.agent.prompts import carregar
from app.llm.base import MENSAGEM_SOBRECARGA
from app.web.token import TAMANHO_MAXIMO, TokenInvalido, assinar, ler

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

MENSAGEM_EXPIROU = "Sua conversa expirou ou não foi encontrada. Comece uma nova."


class PedidoToken(BaseModel):
    token: str = Field(max_length=TAMANHO_MAXIMO)


class PedidoMensagem(PedidoToken):
    texto: str = Field(max_length=2000)  # o limite de 500 é tratado com mensagem gentil


def serializar(evento: Evento) -> tuple[str, dict[str, Any]]:
    match evento:
        case EventoTexto(texto):
            return "texto", {"texto": texto}
        case EventoOpcoes(opcoes):
            return "opcoes", {"opcoes": opcoes}
        case EventoProgresso():
            return "progresso", {"percentual": evento.percentual}
        case EventoRecomendacao(rec):
            return "recomendacao", para_exibicao(rec)
        case EventoAviso(texto):
            return "aviso", {"texto": texto}
        case EventoErro(texto):
            return "erro", {"texto": texto}
        case EventoEstado(estado):
            return "estado", {"estado": estado.value}
    raise TypeError(f"Evento desconhecido: {evento!r}")


def _sse(tipo: str, dados: dict[str, Any]) -> str:
    return f"event: {tipo}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


async def _transmitir(
    request: Request, sessao: Sessao, fluxo: AsyncIterator[Evento]
) -> AsyncIterator[str]:
    try:
        async for evento in fluxo:
            yield _sse(*serializar(evento))
    except Exception:
        # Sem o texto da conversa no log: só o tipo do erro e o traceback do código.
        log.exception("Erro ao responder no chat")
        yield _sse("erro", {"texto": MENSAGEM_SOBRECARGA})
    yield _sse("sessao", {"token": assinar(sessao, request.app.state.chave)})
    yield _sse("fim", {})


def _stream(request: Request, sessao: Sessao, fluxo: AsyncIterator[Evento]) -> StreamingResponse:
    return StreamingResponse(
        _transmitir(request, sessao, fluxo),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def _sessao(request: Request, token: str) -> Sessao:
    try:
        return ler(token, request.app.state.chave)
    except TokenInvalido as erro:
        raise HTTPException(status_code=400, detail=MENSAGEM_EXPIROU) from erro


async def _apenas_aviso(texto: str) -> AsyncIterator[Evento]:
    yield EventoAviso(texto)


@router.post("/chat/iniciar")
async def iniciar(request: Request) -> StreamingResponse:
    if not request.app.state.limite_inicio.permitir(_ip(request)):
        raise HTTPException(status_code=429, detail=carregar("msg_limite"))
    orquestrador: Orquestrador = request.app.state.orquestrador
    sessao = Sessao()
    return _stream(request, sessao, orquestrador.iniciar(sessao))


@router.post("/chat/mensagem")
async def mensagem(request: Request, pedido: PedidoMensagem) -> StreamingResponse:
    sessao = _sessao(request, pedido.token)
    if not request.app.state.limite_mensagens.permitir(sessao.id):
        return _stream(request, sessao, _apenas_aviso(carregar("msg_limite")))
    orquestrador: Orquestrador = request.app.state.orquestrador
    return _stream(request, sessao, orquestrador.responder(sessao, pedido.texto))


@router.post("/resultado")
async def resultado(request: Request, pedido: PedidoToken) -> JSONResponse:
    sessao = _sessao(request, pedido.token)
    if sessao.recomendacao is None:
        raise HTTPException(status_code=404, detail="Ainda não há trilhas nesta conversa.")
    return JSONResponse(para_exibicao(sessao.recomendacao))


@router.post("/resultado.txt")
async def resultado_txt(request: Request, pedido: PedidoToken) -> PlainTextResponse:
    sessao = _sessao(request, pedido.token)
    if sessao.recomendacao is None:
        raise HTTPException(status_code=404, detail="Ainda não há trilhas nesta conversa.")
    return PlainTextResponse(
        texto_resultado(sessao.recomendacao),
        headers={"Content-Disposition": 'attachment; filename="minhas-trilhas.txt"'},
    )

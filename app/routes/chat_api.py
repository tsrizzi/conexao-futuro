"""API do chat: respostas em streaming (Server-Sent Events) sobre POST.

Cada resposta termina com o evento `sessao`, que traz a sessão assinada para o navegador
guardar e reenviar na próxima mensagem (ver app/web/token.py).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.exportar import para_exibicao, texto_de_exibicao
from app.agent.formulario import perfil_do_formulario, resumo_do_formulario
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
from app.db import repositorio
from app.llm.base import MENSAGEM_SOBRECARGA
from app.routes.acesso_api import MENSAGEM_CODIGO_INVALIDO, contar_tentativa
from app.web.token import TAMANHO_MAXIMO, TokenInvalido, assinar, ler

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

MENSAGEM_EXPIROU = "Sua conversa expirou ou não foi encontrada. Comece uma nova."


class PedidoToken(BaseModel):
    token: str = Field(max_length=TAMANHO_MAXIMO)


class PedidoMensagem(PedidoToken):
    texto: str = Field(max_length=2000)  # o limite de 500 é tratado com mensagem gentil


class PedidoInicio(BaseModel):
    codigo: str | None = Field(default=None, max_length=30)


class PedidoResultado(BaseModel):
    token: str | None = Field(default=None, max_length=TAMANHO_MAXIMO)
    codigo: str | None = Field(default=None, max_length=30)


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


def _persistir(request: Request, sessao: Sessao, exibicao: dict[str, Any] | None) -> None:
    """Para quem tem código: guarda o perfil estruturado e as trilhas. Nunca o texto."""
    assert sessao.participante_id is not None
    with request.app.state.sessoes.begin() as db:
        if sessao.estado != "acolhimento":
            repositorio.salvar_perfil(db, sessao.participante_id, sessao.perfil)
        if exibicao is not None:
            repositorio.salvar_recomendacao(db, sessao.participante_id, exibicao)


async def _transmitir(
    request: Request, sessao: Sessao, fluxo: AsyncIterator[Evento]
) -> AsyncIterator[str]:
    exibicao = None
    try:
        async for evento in fluxo:
            tipo, dados = serializar(evento)
            if tipo == "recomendacao":
                exibicao = dados
            yield _sse(tipo, dados)
    except Exception:
        # Sem o texto da conversa no log: só o tipo do erro e o traceback do código.
        log.exception("Erro ao responder no chat")
        yield _sse("erro", {"texto": MENSAGEM_SOBRECARGA})
    if sessao.participante_id:
        try:
            await asyncio.to_thread(_persistir, request, sessao, exibicao)
        except Exception:
            log.exception("Erro ao salvar perfil/trilhas do participante")
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


def _sessao_do_codigo(request: Request, codigo: str) -> tuple[Sessao, Any, str]:
    """Começa a conversa a partir do que o participante já respondeu (CARREGA_FORMULARIO)."""
    contar_tentativa(request)
    with request.app.state.sessoes.begin() as db:
        participante = repositorio.buscar_por_codigo(db, codigo, request.app.state.chave)
        if participante is None:
            raise HTTPException(status_code=404, detail=MENSAGEM_CODIGO_INVALIDO)
        respostas = repositorio.respostas_de(participante)
        perfil = repositorio.perfil_de(participante) or perfil_do_formulario(respostas)
        return Sessao(participante_id=participante.id), perfil, resumo_do_formulario(respostas)


@router.post("/chat/iniciar")
async def iniciar(request: Request, pedido: PedidoInicio | None = None) -> StreamingResponse:
    if not request.app.state.limite_inicio.permitir(_ip(request)):
        raise HTTPException(status_code=429, detail=carregar("msg_limite"))
    orquestrador: Orquestrador = request.app.state.orquestrador
    if pedido and pedido.codigo:
        sessao, perfil, resumo = await asyncio.to_thread(_sessao_do_codigo, request, pedido.codigo)
        tem_perfil = perfil.cobertura > 0 or perfil.interesses or perfil.atividades_que_gosta
        fluxo = orquestrador.iniciar(sessao, perfil if tem_perfil else None, resumo)
        return _stream(request, sessao, fluxo)
    sessao = Sessao()
    return _stream(request, sessao, orquestrador.iniciar(sessao))


@router.post("/chat/mensagem")
async def mensagem(request: Request, pedido: PedidoMensagem) -> StreamingResponse:
    sessao = _sessao(request, pedido.token)
    if not request.app.state.limite_mensagens.permitir(sessao.id):
        return _stream(request, sessao, _apenas_aviso(carregar("msg_limite")))
    orquestrador: Orquestrador = request.app.state.orquestrador
    return _stream(request, sessao, orquestrador.responder(sessao, pedido.texto))


def _exibicao(request: Request, pedido: PedidoResultado) -> dict[str, Any]:
    """Trilhas da conversa atual (token) ou, em outro dispositivo, as salvas no código."""
    if pedido.token:
        sessao = _sessao(request, pedido.token)
        if sessao.recomendacao is not None:
            return para_exibicao(sessao.recomendacao)
        if not pedido.codigo:
            raise HTTPException(status_code=404, detail="Ainda não há trilhas nesta conversa.")
    if not pedido.codigo:
        raise HTTPException(status_code=400, detail=MENSAGEM_EXPIROU)
    contar_tentativa(request)
    with request.app.state.sessoes.begin() as db:
        participante = repositorio.buscar_por_codigo(db, pedido.codigo, request.app.state.chave)
        if participante is None:
            raise HTTPException(status_code=404, detail=MENSAGEM_CODIGO_INVALIDO)
        exibicao = repositorio.ultima_recomendacao(participante)
    if exibicao is None:
        raise HTTPException(status_code=404, detail="Ainda não há trilhas para esse código.")
    return exibicao


@router.post("/resultado")
def resultado(request: Request, pedido: PedidoResultado) -> JSONResponse:
    return JSONResponse(_exibicao(request, pedido))


@router.post("/resultado.txt")
def resultado_txt(request: Request, pedido: PedidoResultado) -> PlainTextResponse:
    return PlainTextResponse(
        texto_de_exibicao(_exibicao(request, pedido)),
        headers={"Content-Disposition": 'attachment; filename="minhas-trilhas.txt"'},
    )

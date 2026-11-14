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
from app.routes.acesso_api import ip, participante_do_codigo
from app.web.entrada import exigir_entrada
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
    codigo_turma: str | None = Field(default=None, max_length=20)


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


def _persistir(
    request: Request, sessao: Sessao, exibicao: dict[str, Any] | None, uso: tuple[int, int]
) -> None:
    """Grava o que pode ser gravado. Nunca o texto da conversa.

    - Quem tem código: o perfil estruturado e as trilhas.
    - Painel do professor: eventos agregados (participação, cursos sugeridos, tokens).
    """
    with request.app.state.sessoes.begin() as db:
        if sessao.participante_id:
            if sessao.estado != "acolhimento":
                repositorio.salvar_perfil(db, sessao.participante_id, sessao.perfil)
            if exibicao is not None:
                repositorio.salvar_recomendacao(db, sessao.participante_id, exibicao)
        # A turma pode ter expirado no meio da conversa: aí o evento fica sem turma.
        turma_id = sessao.turma_id if repositorio.turma_existe(db, sessao.turma_id) else None
        contou = False
        if turma_id is not None and exibicao is not None:
            contou = True
            repositorio.registrar_trilhas(
                db, turma_id, exibicao["trilhas"], not sessao.contabilizada
            )
        repositorio.registrar_uso(db, turma_id, *uso)
    # Só depois do commit: se a gravação falhar, a próxima recomendação ainda conta.
    if contou:
        sessao.contabilizada = True


def _tokens(sessao: Sessao) -> tuple[int, int]:
    """(entrada, saída). A entrada inclui a parte lida/gravada no cache do prompt."""
    u = sessao.uso
    return u.input_tokens + u.cache_read_tokens + u.cache_write_tokens, u.output_tokens


async def _transmitir(
    request: Request, sessao: Sessao, fluxo: AsyncIterator[Evento]
) -> AsyncIterator[str]:
    exibicao = None
    antes = _tokens(sessao)
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
    depois = _tokens(sessao)
    uso = (depois[0] - antes[0], depois[1] - antes[1])
    try:
        await asyncio.to_thread(_persistir, request, sessao, exibicao, uso)
    except Exception:
        log.exception("Erro ao salvar perfil, trilhas ou eventos agregados")
    yield _sse("sessao", {"token": assinar(sessao, request.app.state.chave)})
    yield _sse("fim", {})


def _stream(request: Request, sessao: Sessao, fluxo: AsyncIterator[Evento]) -> StreamingResponse:
    return StreamingResponse(
        _transmitir(request, sessao, fluxo),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sessao(request: Request, token: str) -> Sessao:
    try:
        return ler(token, request.app.state.chave)
    except TokenInvalido as erro:
        raise HTTPException(status_code=400, detail=MENSAGEM_EXPIROU) from erro


async def _apenas_aviso(texto: str) -> AsyncIterator[Evento]:
    yield EventoAviso(texto)


def _turma_id(request: Request, codigo_turma: str | None) -> str | None:
    if not codigo_turma:
        return None
    with request.app.state.sessoes.begin() as db:
        turma = repositorio.buscar_turma(db, codigo_turma)
        return turma.id if turma else None


def _sessao_do_codigo(
    request: Request, codigo: str, codigo_turma: str | None
) -> tuple[Sessao, Any, str]:
    """Começa a conversa a partir do que o participante já respondeu (CARREGA_FORMULARIO)."""
    with request.app.state.sessoes.begin() as db:
        participante = participante_do_codigo(request, db, codigo)
        respostas = repositorio.respostas_de(participante)
        perfil = repositorio.perfil_de(participante) or perfil_do_formulario(respostas)
        turma = repositorio.buscar_turma(db, codigo_turma)
        sessao = Sessao(
            participante_id=participante.id,
            turma_id=turma.id if turma else participante.turma_id,
        )
        return sessao, perfil, resumo_do_formulario(respostas)


@router.post("/chat/iniciar")
async def iniciar(request: Request, pedido: PedidoInicio | None = None) -> StreamingResponse:
    await asyncio.to_thread(exigir_entrada, request)
    if not request.app.state.limite_inicio.permitir(ip(request)):
        raise HTTPException(status_code=429, detail=carregar("msg_limite"))
    orquestrador: Orquestrador = request.app.state.orquestrador
    codigo_turma = pedido.codigo_turma if pedido else None
    if pedido and pedido.codigo:
        sessao, perfil, resumo = await asyncio.to_thread(
            _sessao_do_codigo, request, pedido.codigo, codigo_turma
        )
        tem_perfil = perfil.cobertura > 0 or perfil.interesses or perfil.atividades_que_gosta
        fluxo = orquestrador.iniciar(sessao, perfil if tem_perfil else None, resumo)
        return _stream(request, sessao, fluxo)
    sessao = Sessao(turma_id=await asyncio.to_thread(_turma_id, request, codigo_turma))
    return _stream(request, sessao, orquestrador.iniciar(sessao))


@router.post("/chat/mensagem")
async def mensagem(request: Request, pedido: PedidoMensagem) -> StreamingResponse:
    await asyncio.to_thread(exigir_entrada, request)
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
    with request.app.state.sessoes.begin() as db:
        participante = participante_do_codigo(request, db, pedido.codigo)
        exibicao = repositorio.ultima_recomendacao(participante)
    if exibicao is None:
        raise HTTPException(status_code=404, detail="Ainda não há trilhas para esse código.")
    return exibicao


@router.post("/resultado")
def resultado(request: Request, pedido: PedidoResultado) -> JSONResponse:
    exigir_entrada(request)
    return JSONResponse(_exibicao(request, pedido))


@router.post("/resultado.txt")
def resultado_txt(request: Request, pedido: PedidoResultado) -> PlainTextResponse:
    exigir_entrada(request)
    return PlainTextResponse(
        texto_de_exibicao(_exibicao(request, pedido)),
        headers={"Content-Disposition": 'attachment; filename="minhas-trilhas.txt"'},
    )

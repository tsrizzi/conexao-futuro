"""API da porta de entrada: trocar o código por uma liberação, e sair."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import AppEnv
from app.db import repositorio
from app.db.codigo import normalizar_codigo_entrada
from app.routes.acesso_api import MENSAGEM_TENTATIVAS, ip
from app.web.entrada import (
    COOKIE_ENTRADA,
    MENSAGEM_INVALIDO,
    MENSAGENS_SITUACAO,
    criar_cookie_entrada,
)

router = APIRouter(prefix="/api")


class PedidoEntrada(BaseModel):
    codigo: str = Field(max_length=20)


@router.post("/entrada")
def entrar(request: Request, pedido: PedidoEntrada) -> JSONResponse:
    """Só palpites contam no limite: código bem formado que não existe (50 por IP a cada 10
    min, uma sala inteira sai pelo mesmo IP). Erro de digitação (formato inválido) não pode
    acertar nada, então não conta."""
    estado = request.app.state
    chave_ip = ip(request)
    if estado.limite_entrada.bloqueado(chave_ip):
        raise HTTPException(status_code=429, detail=MENSAGEM_TENTATIVAS)
    with estado.sessoes.begin() as db:
        registro = repositorio.buscar_codigo_entrada(db, pedido.codigo, estado.chave)
        if registro is None:
            if normalizar_codigo_entrada(pedido.codigo) is not None:
                estado.limite_entrada.registrar(chave_ip)
            raise HTTPException(status_code=404, detail=MENSAGEM_INVALIDO)
        situacao = repositorio.situacao_codigo(registro)
        if situacao != "ativo":
            raise HTTPException(status_code=403, detail=MENSAGENS_SITUACAO[situacao])
        registro.usos += 1
        valor, max_age = criar_cookie_entrada(estado.chave, registro.id, registro.fim_em)
        fim_em = registro.fim_em.isoformat()
    corpo: dict[str, Any] = {"ok": True, "fim_em": fim_em}
    resposta = JSONResponse(corpo)
    resposta.set_cookie(
        COOKIE_ENTRADA,
        valor,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=estado.settings.app_env == AppEnv.PRODUCTION,
        samesite="lax",
    )
    return resposta


@router.post("/saida")
def sair() -> JSONResponse:
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(COOKIE_ENTRADA, path="/")
    return resposta

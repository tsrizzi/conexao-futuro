"""Porta de entrada: só quem digitou um código válido usa o site.

O código (gerado pelo admin) vira um cookie assinado de liberação: id do código + validade,
com HMAC. A validade é o fim do código ou 12 h, o que vier antes. Toda chamada que grava ou
gasta (chat, formulário, resultado) confere o cookie E o código no banco, para que revogar
ou terminar o período corte o acesso na hora.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from app.db import repositorio
from app.db.models import CodigoEntrada

COOKIE_ENTRADA = "cf_entrada"
VALIDADE_MAX_S = 12 * 3600

MENSAGEM_PRECISA_CODIGO = "Para usar o Conexão Futuro, digite o código de entrada."
MENSAGEM_AGENDADO = "Este código ainda não começou a valer. Confira a data com quem te passou."
MENSAGEM_ENCERRADO = "O período deste código terminou."
MENSAGEM_REVOGADO = "Este código foi desativado."
MENSAGEM_INVALIDO = "Código não encontrado. Confira se digitou certinho."
MENSAGENS_SITUACAO = {
    "agendado": MENSAGEM_AGENDADO,
    "encerrado": MENSAGEM_ENCERRADO,
    "revogado": MENSAGEM_REVOGADO,
}


def _assinar(chave: bytes, codigo_id: str, expira: str) -> str:
    return hmac.new(chave, f"entrada|{codigo_id}|{expira}".encode(), hashlib.sha256).hexdigest()


def criar_cookie_entrada(
    chave: bytes, codigo_id: int, fim: datetime, agora: float | None = None
) -> tuple[str, int]:
    """(valor do cookie, max_age em segundos)."""
    agora = agora if agora is not None else time.time()
    fim = fim if fim.tzinfo else fim.replace(tzinfo=UTC)  # o SQLite devolve sem fuso
    expira = int(min(fim.timestamp(), agora + VALIDADE_MAX_S))
    valor = f"{codigo_id}.{expira}.{_assinar(chave, str(codigo_id), str(expira))}"
    return valor, max(0, int(expira - agora))


def ler_cookie_entrada(valor: str | None, chave: bytes, agora: float | None = None) -> int | None:
    if not valor or valor.count(".") != 2:
        return None
    codigo_id, expira, assinatura = valor.split(".")
    if not (codigo_id.isdigit() and expira.isdigit()):
        return None
    if not hmac.compare_digest(assinatura, _assinar(chave, codigo_id, expira)):
        return None
    if int(expira) <= (agora if agora is not None else time.time()):
        return None
    return int(codigo_id)


def codigo_liberado(request: Request) -> CodigoEntrada | None:
    """O código desta liberação, se o cookie é válido e o código segue ativo."""
    codigo_id = ler_cookie_entrada(request.cookies.get(COOKIE_ENTRADA), request.app.state.chave)
    if codigo_id is None:
        return None
    with request.app.state.sessoes() as db:
        registro = db.get(CodigoEntrada, codigo_id)
        if registro is None or repositorio.situacao_codigo(registro) != "ativo":
            return None
        return registro


def exigir_entrada(request: Request) -> None:
    if codigo_liberado(request) is None:
        raise HTTPException(
            status_code=403,
            detail=MENSAGEM_PRECISA_CODIGO,
            headers={"X-Entrada": "necessaria"},
        )

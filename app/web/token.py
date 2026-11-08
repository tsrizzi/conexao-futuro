"""Sessão do chat assinada, guardada no navegador (sessionStorage).

A especificação manda o histórico ficar "na sessão do navegador" e o servidor não gravar o
texto das conversas. Então o servidor não guarda nada: a cada resposta devolve a sessão
inteira (já com dados pessoais mascarados), comprimida e assinada com HMAC. O navegador a
reenvia na próxima mensagem. Fechou a aba, acabou. Isso também sobrevive a reinícios do
servidor (o Render gratuito dorme e reinicia).

A assinatura impede que alguém altere o estado (ex.: forjar mensagens do agente ou trocar
as trilhas). O conteúdo não é cifrado, mas só a própria pessoa tem o token.
"""

import base64
import hashlib
import hmac
import json
import time
import zlib

from pydantic import ValidationError

from app.agent.orchestrator import Sessao

VERSAO = 1
VALIDADE_S = 12 * 3600  # uma aula, ou o resto do dia em casa
TAMANHO_MAXIMO = 300_000  # caracteres; muito acima de uma conversa real


class TokenInvalido(ValueError):
    pass


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _assinatura(chave: bytes, corpo: str) -> str:
    return _b64(hmac.new(chave, corpo.encode(), hashlib.sha256).digest())


def assinar(sessao: Sessao, chave: bytes, agora: float | None = None) -> str:
    pacote = {
        "v": VERSAO,
        "exp": int((agora or time.time()) + VALIDADE_S),
        "s": sessao.model_dump(mode="json"),
    }
    corpo = _b64(zlib.compress(json.dumps(pacote, separators=(",", ":")).encode(), 9))
    return f"{corpo}.{_assinatura(chave, corpo)}"


def ler(token: str, chave: bytes, agora: float | None = None) -> Sessao:
    if not token or len(token) > TAMANHO_MAXIMO or token.count(".") != 1:
        raise TokenInvalido("formato")
    corpo, assinatura = token.split(".")
    if not hmac.compare_digest(assinatura, _assinatura(chave, corpo)):
        raise TokenInvalido("assinatura")
    try:
        pacote = json.loads(zlib.decompress(_de_b64(corpo)))
    except (ValueError, zlib.error) as erro:
        raise TokenInvalido("conteúdo") from erro
    if pacote.get("v") != VERSAO:
        raise TokenInvalido("versão")
    if pacote.get("exp", 0) < (agora or time.time()):
        raise TokenInvalido("expirado")
    try:
        return Sessao.model_validate(pacote["s"])
    except (KeyError, ValidationError) as erro:
        raise TokenInvalido("sessão") from erro

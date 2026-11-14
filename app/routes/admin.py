"""Área do admin (/admin): só o autor. Gera os códigos de entrada no site, com período.

Login com ADMIN_USUARIO + ADMIN_PASSWORD_HASH (argon2). Cookie assinado com escopo "admin"
(um cookie do painel do professor nunca vale aqui), CSRF em todo POST, 5 tentativas de
login por IP a cada 10 min. Páginas feitas no servidor; o único JS é o botão de copiar.

Os códigos aparecem por inteiro só na resposta que os cria; no banco fica o HMAC e o final.
"""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.db import repositorio
from app.db.models import CodigoEntrada, agora
from app.routes.acesso_api import ip
from app.web import professor_sessao as auth
from app.web.formularios import (
    MENSAGEM_EXPIROU,
    MENSAGEM_TENTATIVAS,
    cookie_seguro,
    ir,
    ler_formulario,
    pagina_privada,
)

router = APIRouter()

# Horário de Brasília: UTC−3 fixo (sem horário de verão desde 2019). Sem zoneinfo, que
# precisa do pacote tzdata no Windows e na imagem slim.
BRT = timezone(timedelta(hours=-3), "BRT")
FORMATO_CAMPO = "%Y-%m-%dT%H:%M"
COOKIE_ADMIN = "cf_admin"
COOKIE_CSRF_ADMIN = "cf_admin_login"
ESCOPO = "admin"
DIAS_PADRAO = 30
TAMANHO_ROTULO = 80

MENSAGEM_LOGIN = "Usuário ou senha incorretos."
MENSAGEM_SEM_CONFIG = (
    "A área do admin ainda não foi configurada. Defina ADMIN_USUARIO e ADMIN_PASSWORD_HASH "
    "(gere o hash com `python scripts/gerar_hash_professor.py`)."
)
MENSAGEM_DATAS = "O término precisa ser depois do início. Confira as duas datas."
MENSAGEM_DATA_INVALIDA = "Preencha a data e a hora de início e de término."


@dataclass(frozen=True)
class LinhaCodigo:
    id: int
    rotulo: str
    final: str
    inicio: str
    fim: str
    situacao: str
    usos: int


def _hash(request: Request) -> str | None:
    segredo = request.app.state.settings.admin_password_hash
    return segredo.get_secret_value() if segredo else None


def _configurado(request: Request) -> bool:
    return bool(request.app.state.settings.admin_usuario) and _hash(request) is not None


def _sessao(request: Request) -> auth.SessaoProfessor | None:
    if not _configurado(request):
        return None
    return auth.ler_cookie(
        request.cookies.get(COOKIE_ADMIN), request.app.state.chave, _hash(request), escopo=ESCOPO
    )


async def _exigir_sessao(request: Request) -> tuple[auth.SessaoProfessor, dict[str, str]]:
    sessao = _sessao(request)
    if sessao is None:
        raise HTTPException(status_code=401, detail="Entre na área do admin de novo.")
    campos = await ler_formulario(request)
    if not sessao.csrf_confere(request.app.state.chave, campos.get("csrf")):
        raise HTTPException(status_code=403, detail=MENSAGEM_EXPIROU)
    return sessao, campos


def _para_brt(momento: datetime) -> datetime:
    return (momento if momento.tzinfo else momento.replace(tzinfo=UTC)).astimezone(BRT)


def ler_data_brt(texto: str) -> datetime | None:
    """Campo datetime-local (horário de Brasília) → datetime em UTC; None se inválido."""
    try:
        local = datetime.strptime(texto, FORMATO_CAMPO)
    except ValueError:
        return None
    return local.replace(tzinfo=BRT).astimezone(UTC)


# ---- Login -----------------------------------------------------------------------------


def _tela_login(request: Request, erro: str | None = None, status_code: int = 200) -> Response:
    csrf = auth.novo_csrf_login()
    configurado = _configurado(request)
    resposta = pagina_privada(
        request,
        "admin_login.html",
        status_code=status_code,
        csrf=csrf,
        erro=erro or (None if configurado else MENSAGEM_SEM_CONFIG),
        fechado=not configurado,
    )
    resposta.set_cookie(
        COOKIE_CSRF_ADMIN,
        csrf,
        max_age=3600,
        path="/admin",
        httponly=True,
        secure=cookie_seguro(request),
        samesite="lax",
    )
    return resposta


@router.post("/admin/entrar")
async def entrar(request: Request) -> Response:
    campos = await ler_formulario(request)
    esperado = request.cookies.get(COOKIE_CSRF_ADMIN) or ""
    if not esperado or campos.get("csrf") != esperado:
        return _tela_login(request, MENSAGEM_EXPIROU, status_code=403)
    if not request.app.state.limite_admin.permitir(ip(request)):
        return _tela_login(request, MENSAGEM_TENTATIVAS, status_code=429)
    usuario = request.app.state.settings.admin_usuario or ""
    hash_senha = _hash(request)
    usuario_ok = bool(usuario) and hmac.compare_digest(
        campos.get("usuario", "").encode(), usuario.encode()
    )
    # Confere a senha mesmo com usuário errado: o tempo de resposta não entrega qual errou.
    senha_ok = auth.senha_confere(campos.get("senha", ""), hash_senha)
    if not (usuario_ok and senha_ok) or hash_senha is None:
        return _tela_login(request, MENSAGEM_LOGIN, status_code=401)
    resposta = ir("/admin")
    resposta.set_cookie(
        COOKIE_ADMIN,
        auth.criar_cookie(request.app.state.chave, hash_senha, escopo=ESCOPO),
        max_age=auth.VALIDADE_S,
        path="/admin",
        httponly=True,
        secure=cookie_seguro(request),
        samesite="lax",
    )
    resposta.delete_cookie(COOKIE_CSRF_ADMIN, path="/admin")
    return resposta


@router.post("/admin/sair")
async def sair(request: Request) -> Response:
    await _exigir_sessao(request)
    resposta = ir("/admin")
    resposta.delete_cookie(COOKIE_ADMIN, path="/admin")
    return resposta


# ---- Códigos ---------------------------------------------------------------------------


def _linha(c: CodigoEntrada) -> LinhaCodigo:
    formato = "%d/%m/%Y %H:%M"
    return LinhaCodigo(
        id=c.id,
        rotulo=c.rotulo or "Sem rótulo",
        final=c.final,
        inicio=_para_brt(c.inicio_em).strftime(formato),
        fim=_para_brt(c.fim_em).strftime(formato),
        situacao=repositorio.situacao_codigo(c),
        usos=c.usos,
    )


def _tela_codigos(
    request: Request,
    sessao: auth.SessaoProfessor,
    *,
    novo: str | None = None,
    erro: str | None = None,
    campos: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    with request.app.state.sessoes() as db:
        linhas = [_linha(c) for c in repositorio.listar_codigos_entrada(db)]
    agora_brt = agora().astimezone(BRT)
    campos = campos or {
        "rotulo": "",
        "inicio": agora_brt.strftime(FORMATO_CAMPO),
        "fim": (agora_brt + timedelta(days=DIAS_PADRAO)).strftime(FORMATO_CAMPO),
    }
    return pagina_privada(
        request,
        "admin_codigos.html",
        status_code=status_code,
        csrf=sessao.csrf(request.app.state.chave),
        linhas=linhas,
        novo=novo,
        erro=erro,
        campos=campos,
    )


@router.get("/admin", response_class=HTMLResponse)
def painel(request: Request) -> Response:
    sessao = _sessao(request)
    if sessao is None:
        return _tela_login(request)
    return _tela_codigos(request, sessao)


@router.post("/admin/codigos")
async def criar_codigo(request: Request) -> Response:
    sessao, campos = await _exigir_sessao(request)
    rotulo = campos.get("rotulo", "")[:TAMANHO_ROTULO]
    inicio = ler_data_brt(campos.get("inicio", ""))
    fim = ler_data_brt(campos.get("fim", ""))
    preenchido = {
        "rotulo": rotulo,
        "inicio": campos.get("inicio", ""),
        "fim": campos.get("fim", ""),
    }
    if inicio is None or fim is None:
        return _tela_codigos(
            request, sessao, erro=MENSAGEM_DATA_INVALIDA, campos=preenchido, status_code=400
        )
    if fim <= inicio:
        return _tela_codigos(
            request, sessao, erro=MENSAGEM_DATAS, campos=preenchido, status_code=400
        )
    estado = request.app.state
    with estado.sessoes.begin() as db:
        _, codigo = repositorio.criar_codigo_entrada(db, estado.chave, rotulo, inicio, fim)
    # Resposta direta (sem redirecionar): o código não vai para a URL nem para o histórico.
    return _tela_codigos(request, sessao, novo=codigo)


@router.post("/admin/codigos/{codigo_id}/revogar")
async def revogar(request: Request, codigo_id: int) -> Response:
    sessao, _ = await _exigir_sessao(request)
    with request.app.state.sessoes.begin() as db:
        if not repositorio.revogar_codigo_entrada(db, codigo_id):
            raise HTTPException(status_code=404, detail="Código não encontrado.")
    return _tela_codigos(request, sessao)

"""Painel do professor (seção 5.5): login, turmas e números agregados.

Nenhuma tela mostra dado individual: só contagens por turma, e turmas com menos de
PAINEL_MINIMO_DETALHE participantes mostram apenas a contagem (seção 8.8).

Páginas feitas no servidor, sem JavaScript; os gráficos são barras em HTML e CSS.
Rotas síncronas: o FastAPI as roda num pool de threads, sem travar o chat.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.db import repositorio
from app.db.codigo import nome_da_turma
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

RAIZ = Path(__file__).resolve().parents[2]
router = APIRouter()

MENSAGEM_SENHA = "Senha incorreta."
MENSAGEM_SEM_SENHA = (
    "O painel ainda não tem senha. Gere o hash com `python scripts/gerar_hash_professor.py` "
    "e coloque em PROFESSOR_PASSWORD_HASH no .env."
)


# ---- Perguntas da roda de conversa -----------------------------------------------------


@dataclass(frozen=True)
class RodaDeConversa:
    gerais: list[str]
    por_eixo: dict[str, list[str]]

    def para(self, eixos: list[str], quantos_eixos: int = 3) -> list[tuple[str, list[str]]]:
        blocos = [(e, self.por_eixo[e]) for e in eixos[:quantos_eixos] if e in self.por_eixo]
        return [("Para qualquer turma", self.gerais), *blocos]


@lru_cache
def carregar_roda(caminho: Path = RAIZ / "data" / "roda_conversa.yaml") -> RodaDeConversa:
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    return RodaDeConversa(list(dados["gerais"]), dict(dados.get("por_eixo") or {}))


# ---- Sessão, CSRF e formulários ---------------------------------------------------------


def _hash_senha(request: Request) -> str | None:
    segredo = request.app.state.settings.professor_password_hash
    return segredo.get_secret_value() if segredo else None


def _sessao(request: Request) -> auth.SessaoProfessor | None:
    return auth.ler_cookie(
        request.cookies.get(auth.COOKIE_SESSAO), request.app.state.chave, _hash_senha(request)
    )


_formulario = ler_formulario
_cookie_seguro = cookie_seguro
_pagina = pagina_privada
_ir = ir


async def _exigir_sessao(request: Request) -> tuple[auth.SessaoProfessor, dict[str, str]]:
    """POST do painel: sessão válida e CSRF conferido."""
    sessao = _sessao(request)
    if sessao is None:
        raise HTTPException(status_code=401, detail="Entre no painel de novo.")
    campos = await _formulario(request)
    if not sessao.csrf_confere(request.app.state.chave, campos.get("csrf")):
        raise HTTPException(status_code=403, detail=MENSAGEM_EXPIROU)
    return sessao, campos


def padlet_valido(url: str) -> bool:
    if not url:
        return True  # opcional
    partes = urlsplit(url)
    return len(url) <= 300 and partes.scheme == "https" and bool(partes.netloc)


# ---- Login -----------------------------------------------------------------------------


def _tela_login(request: Request, erro: str | None = None, status_code: int = 200) -> Response:
    csrf = auth.novo_csrf_login()
    sem_senha = _hash_senha(request) is None
    resposta = _pagina(
        request,
        "professor_login.html",
        status_code=status_code,
        csrf=csrf,
        erro=erro or (MENSAGEM_SEM_SENHA if sem_senha else None),
        sem_senha=sem_senha,
    )
    resposta.set_cookie(
        auth.COOKIE_CSRF_LOGIN,
        csrf,
        max_age=3600,
        path="/professor",
        httponly=True,
        secure=_cookie_seguro(request),
        samesite="lax",
    )
    return resposta


@router.post("/professor/entrar")
async def entrar(request: Request) -> Response:
    campos = await _formulario(request)
    esperado = request.cookies.get(auth.COOKIE_CSRF_LOGIN) or ""
    if not esperado or campos.get("csrf") != esperado:
        return _tela_login(request, MENSAGEM_EXPIROU, status_code=403)
    if not request.app.state.limite_login.permitir(ip(request)):
        return _tela_login(request, MENSAGEM_TENTATIVAS, status_code=429)
    hash_senha = _hash_senha(request)
    if not auth.senha_confere(campos.get("senha", ""), hash_senha):
        return _tela_login(request, MENSAGEM_SENHA, status_code=401)
    assert hash_senha is not None
    resposta = _ir("/professor")
    resposta.set_cookie(
        auth.COOKIE_SESSAO,
        auth.criar_cookie(request.app.state.chave, hash_senha),
        max_age=auth.VALIDADE_S,
        path="/professor",
        httponly=True,
        secure=_cookie_seguro(request),
        samesite="lax",
    )
    resposta.delete_cookie(auth.COOKIE_CSRF_LOGIN, path="/professor")
    return resposta


@router.post("/professor/sair")
async def sair(request: Request) -> Response:
    await _exigir_sessao(request)
    resposta = _ir("/professor")
    resposta.delete_cookie(auth.COOKIE_SESSAO, path="/professor")
    return resposta


# ---- Turmas ----------------------------------------------------------------------------


def _custo(request: Request, entrada: int, saida: int) -> float:
    s = request.app.state.settings
    return (entrada * s.llm_preco_entrada_mtok + saida * s.llm_preco_saida_mtok) / 1_000_000


def _lista(request: Request, sessao: auth.SessaoProfessor, erro: str | None = None) -> Response:
    with request.app.state.sessoes.begin() as db:
        turmas = repositorio.turmas_ativas(db)
        contagens = repositorio.participacoes(db, [t.id for t in turmas])
        entrada, saida = repositorio.tokens(db, todas=True)
    return _pagina(
        request,
        "professor_turmas.html",
        status_code=400 if erro else 200,
        csrf=sessao.csrf(request.app.state.chave),
        turmas=[(t, contagens.get(t.id, 0)) for t in turmas],
        tokens_entrada=entrada,
        tokens_saida=saida,
        custo=_custo(request, entrada, saida),
        erro=erro,
    )


@router.get("/professor", response_class=HTMLResponse)
def painel(request: Request) -> Response:
    sessao = _sessao(request)
    if sessao is None:
        return _tela_login(request)
    return _lista(request, sessao)


@router.post("/professor/turmas")
async def criar_turma(request: Request) -> Response:
    sessao, campos = await _exigir_sessao(request)
    escola = campos.get("escola", "")[:120]
    nome = campos.get("turma", "")
    padlet = campos.get("padlet_url", "")
    if nome_da_turma(nome) is None:
        return _lista(request, sessao, "Diga qual é a turma (ex.: 2B).")
    if not padlet_valido(padlet):
        return _lista(request, sessao, "O link do mural precisa começar com https://")
    estado = request.app.state
    with estado.sessoes.begin() as db:
        turma = repositorio.criar_turma(db, escola, nome, padlet, estado.settings.retencao_dias)
        codigo = turma.codigo_turma
    return _ir(f"/professor/turmas/{codigo}")


def _turma_ou_404(db: Any, codigo: str) -> Any:
    turma = repositorio.buscar_turma(db, codigo)
    if turma is None:
        raise HTTPException(status_code=404, detail="Turma não encontrada ou já expirou.")
    return turma


def _nome_curso(request: Request, curso_id: str) -> str:
    curso = request.app.state.orquestrador.catalogo.get(curso_id)
    return curso.nome if curso else curso_id


@router.get("/professor/turmas/{codigo}", response_class=HTMLResponse)
def ver_turma(request: Request, codigo: str) -> Response:
    sessao = _sessao(request)
    if sessao is None:
        return _ir("/professor")
    return _tela_turma(request, sessao, codigo)


def _tela_turma(
    request: Request, sessao: auth.SessaoProfessor, codigo: str, erro: str | None = None
) -> Response:
    estado = request.app.state
    minimo = estado.settings.painel_minimo_detalhe
    with estado.sessoes.begin() as db:
        turma = _turma_ou_404(db, codigo)
        dados = repositorio.painel_da_turma(db, turma.id, minimo)
    eixos = [e for e, _ in dados.eixos]
    return _pagina(
        request,
        "professor_turma.html",
        status_code=400 if erro else 200,
        csrf=sessao.csrf(estado.chave),
        turma=turma,
        painel=dados,
        minimo=minimo,
        maximo_eixo=max((n for _, n in dados.eixos), default=1),
        maximo_curso=max((n for _, n in dados.cursos), default=1),
        cursos=[(_nome_curso(request, c), n) for c, n in dados.cursos],
        perguntas=carregar_roda().para(eixos) if dados.detalhado else [],
        custo=_custo(request, dados.tokens_entrada, dados.tokens_saida),
        erro=erro,
    )


@router.post("/professor/turmas/{codigo}/padlet")
async def atualizar_padlet(request: Request, codigo: str) -> Response:
    sessao, campos = await _exigir_sessao(request)
    padlet = campos.get("padlet_url", "")
    if not padlet_valido(padlet):
        return _tela_turma(
            request, sessao, codigo, erro="O link do mural precisa começar com https://"
        )
    with request.app.state.sessoes.begin() as db:
        _turma_ou_404(db, codigo).padlet_url = padlet or None
    return _ir(f"/professor/turmas/{codigo}")


# ---- Lado do estudante -----------------------------------------------------------------


@router.get("/api/turma/{codigo}")
def turma_publica(request: Request, codigo: str) -> dict[str, Any]:
    """Confere o código da turma e devolve o link do mural. Nada além disso."""
    # Limite próprio e folgado: a turma inteira costuma sair pelo mesmo IP da escola.
    if not request.app.state.limite_turma.permitir(ip(request)):
        raise HTTPException(status_code=429, detail=MENSAGEM_TENTATIVAS)
    with request.app.state.sessoes.begin() as db:
        turma = repositorio.buscar_turma(db, codigo[:20])
        if turma is None:
            raise HTTPException(status_code=404, detail="Não achei essa turma. Confira o código.")
        return {"codigo_turma": turma.codigo_turma, "padlet_url": turma.padlet_url}

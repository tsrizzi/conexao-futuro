"""Código de entrada: só quem tem um código válido usa o site (formulário e chat)."""

import re
from datetime import timedelta

import pytest

from app.db import codigo as codigos
from app.db import repositorio
from app.db.models import CodigoEntrada, agora
from tests.conftest import banco_em_memoria

CHAVE = b"x" * 32


@pytest.fixture
def sessoes():  # type: ignore[no-untyped-def]
    return banco_em_memoria()


def criar(sessoes, inicio_h: float = -1, fim_h: float = 24, revogado: bool = False):  # type: ignore[no-untyped-def]
    """Cria um código com período relativo a agora (em horas). Devolve (id, código)."""
    with sessoes.begin() as db:
        c, codigo = repositorio.criar_codigo_entrada(
            db,
            CHAVE,
            "Teste",
            agora() + timedelta(hours=inicio_h),
            agora() + timedelta(hours=fim_h),
        )
        c.revogado = revogado
        db.flush()
        return c.id, codigo


# ---- Formato -------------------------------------------------------------------------


def test_codigo_entrada_formato() -> None:
    for _ in range(200):
        codigo = codigos.gerar_codigo_entrada()
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", codigo)
        assert not set(codigo) & set("0O1IL5S")


@pytest.mark.parametrize(
    ("digitado", "esperado"),
    [
        ("k7mq-3xpt", "K7MQ-3XPT"),
        (" K7MQ 3XPT ", "K7MQ-3XPT"),
        ("k7mq3xpt", "K7MQ-3XPT"),
        ("K7MQ.3XPT", "K7MQ-3XPT"),
        ("", None),
        ("K7MQ-3XP", None),
        ("K7MQ-3XP0", None),
        ("K7MQ-3XPTA", None),
    ],
)
def test_normalizar_codigo_entrada(digitado: str, esperado: str | None) -> None:
    assert codigos.normalizar_codigo_entrada(digitado) == esperado


# ---- Banco ---------------------------------------------------------------------------


def test_banco_guarda_so_hash_e_final(sessoes) -> None:  # type: ignore[no-untyped-def]
    codigo_id, codigo = criar(sessoes)
    with sessoes() as db:
        c = db.get(CodigoEntrada, codigo_id)
        assert c is not None
        assert codigo not in c.codigo_hash and codigo.replace("-", "") not in c.codigo_hash
        assert c.final == codigo[-4:]
        assert repositorio.buscar_codigo_entrada(db, codigo.lower(), CHAVE) is c
        assert repositorio.buscar_codigo_entrada(db, codigo, b"y" * 32) is None
        assert repositorio.buscar_codigo_entrada(db, "não é código", CHAVE) is None


def test_codigo_de_entrada_nao_colide_com_hash_do_estudante() -> None:
    # Domínios separados: o mesmo texto não gera o mesmo HMAC nos dois tipos de código.
    assert codigos.hash_do_codigo("entrada:K7MQ-3XPT", CHAVE) != codigos.hash_do_codigo(
        "K7MQ-3XPT", CHAVE
    )


@pytest.mark.parametrize(
    ("inicio_h", "fim_h", "revogado", "situacao"),
    [
        (1, 24, False, "agendado"),
        (-1, 24, False, "ativo"),
        (-48, -1 / 60, False, "encerrado"),
        (-1, 24, True, "revogado"),
        (1, 24, True, "revogado"),
    ],
)
def test_situacao_codigo(sessoes, inicio_h, fim_h, revogado, situacao) -> None:  # type: ignore[no-untyped-def]
    codigo_id, _ = criar(sessoes, inicio_h, fim_h, revogado)
    with sessoes() as db:
        c = db.get(CodigoEntrada, codigo_id)
        assert repositorio.situacao_codigo(c) == situacao


def test_listar_e_revogar(sessoes) -> None:  # type: ignore[no-untyped-def]
    primeiro, _ = criar(sessoes)
    segundo, _ = criar(sessoes)
    with sessoes.begin() as db:
        assert [c.id for c in repositorio.listar_codigos_entrada(db)] == [segundo, primeiro]
        assert repositorio.revogar_codigo_entrada(db, primeiro) is True
        assert repositorio.revogar_codigo_entrada(db, 999_999) is False
    with sessoes() as db:
        assert repositorio.situacao_codigo(db.get(CodigoEntrada, primeiro)) == "revogado"


def test_retencao_apaga_codigo_antigo(sessoes) -> None:  # type: ignore[no-untyped-def]
    antigo, _ = criar(sessoes, -24 * 70, -24 * 61)
    recente, _ = criar(sessoes, -24 * 20, -24 * 10)
    with sessoes.begin() as db:
        repositorio.limpar_expirados(db, 60)
    with sessoes() as db:
        assert db.get(CodigoEntrada, antigo) is None
        assert db.get(CodigoEntrada, recente) is not None

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


# ---- A porta no servidor ---------------------------------------------------------------

ROTAS_FECHADAS = [
    ("/api/chat/iniciar", {}),
    ("/api/chat/mensagem", {"token": "x", "texto": "oi"}),
    ("/api/resultado", {"codigo": "ONDA-0000"}),
    ("/api/resultado.txt", {"codigo": "ONDA-0000"}),
    ("/api/formulario", {"respostas": {"f01": "4"}}),
    ("/api/codigo", {"codigo": "ONDA-0000"}),
]


def cliente(catalogo, sessoes, llm=None, liberado=False):  # type: ignore[no-untyped-def]
    from app.llm.fake import FakeProvider
    from tests.conftest import criar_cliente

    return criar_cliente(llm or FakeProvider(), catalogo, sessoes, liberado=liberado)


@pytest.mark.parametrize(("rota", "corpo"), ROTAS_FECHADAS)
def test_apis_fechadas_sem_codigo(catalogo_real, sessoes, rota, corpo) -> None:  # type: ignore[no-untyped-def]
    from app.llm.fake import FakeProvider

    llm = FakeProvider()
    with cliente(catalogo_real, sessoes, llm) as client:
        resposta = client.post(rota, json=corpo)
    assert resposta.status_code == 403
    assert resposta.headers["x-entrada"] == "necessaria"
    assert llm.chamadas == []


def test_entrar_com_codigo_libera(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    codigo_id, codigo = criar(sessoes)
    with cliente(catalogo_real, sessoes) as client:
        resposta = client.post("/api/entrada", json={"codigo": codigo.lower().replace("-", " ")})
        assert resposta.status_code == 200 and resposta.json()["ok"] is True
        cookie = next(c for c in resposta.headers.get_list("set-cookie") if "cf_entrada=" in c)
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert client.post("/api/chat/iniciar", json={}).status_code == 200
    with sessoes() as db:
        assert db.get(CodigoEntrada, codigo_id).usos == 1


@pytest.mark.parametrize(
    ("inicio_h", "fim_h", "revogado", "status", "mensagem"),
    [
        (1, 24, False, 403, "MENSAGEM_AGENDADO"),
        (-48, -1, False, 403, "MENSAGEM_ENCERRADO"),
        (-1, 24, True, 403, "MENSAGEM_REVOGADO"),
    ],
)
def test_mensagens_por_situacao(
    catalogo_real, sessoes, inicio_h, fim_h, revogado, status, mensagem
) -> None:  # type: ignore[no-untyped-def]
    from app.web import entrada

    _, codigo = criar(sessoes, inicio_h, fim_h, revogado)
    with cliente(catalogo_real, sessoes) as client:
        resposta = client.post("/api/entrada", json={"codigo": codigo})
        assert resposta.status_code == status
        assert resposta.json()["detail"] == getattr(entrada, mensagem)
        assert "cf_entrada" not in client.cookies
        assert client.post("/api/chat/iniciar", json={}).status_code == 403


def test_codigo_inexistente(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    from app.web.entrada import MENSAGEM_INVALIDO

    with cliente(catalogo_real, sessoes) as client:
        for texto in ("ZZZZ-ZZZZ", "qualquer coisa"):
            resposta = client.post("/api/entrada", json={"codigo": texto})
            assert resposta.status_code == 404
            assert resposta.json()["detail"] == MENSAGEM_INVALIDO


def test_revogar_corta_conversa_em_andamento(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    from tests.test_web import eventos_sse, token_de

    codigo_id, codigo = criar(sessoes)
    with cliente(catalogo_real, sessoes) as client:
        client.post("/api/entrada", json={"codigo": codigo})
        token = token_de(eventos_sse(client.post("/api/chat/iniciar", json={}).text))
        with sessoes.begin() as db:
            repositorio.revogar_codigo_entrada(db, codigo_id)
        resposta = client.post("/api/chat/mensagem", json={"token": token, "texto": "oi"})
        assert resposta.status_code == 403
        assert resposta.headers["x-entrada"] == "necessaria"


def test_cookie_adulterado_ou_de_outra_chave(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    from app.web.entrada import COOKIE_ENTRADA, criar_cookie_entrada, ler_cookie_entrada

    fim = agora() + timedelta(hours=5)
    valor, _ = criar_cookie_entrada(CHAVE, 7, fim, agora=1_000_000.0)
    assert ler_cookie_entrada(valor, CHAVE, agora=1_000_000.0) == 7
    codigo_id, exp, assinatura = valor.split(".")
    assert ler_cookie_entrada(f"8.{exp}.{assinatura}", CHAVE, agora=1_000_000.0) is None
    assert ler_cookie_entrada(f"{codigo_id}.{int(exp) + 9}.{assinatura}", CHAVE) is None
    assert ler_cookie_entrada(valor, b"y" * 32, agora=1_000_000.0) is None
    assert ler_cookie_entrada(valor, CHAVE, agora=float(exp) + 1) is None
    assert ler_cookie_entrada(None, CHAVE) is None
    assert ler_cookie_entrada("lixo", CHAVE) is None

    # Cookie bem assinado, mas o código foi apagado do banco: não vale.
    apagado, _ = criar(sessoes)
    with sessoes.begin() as db:
        db.delete(db.get(CodigoEntrada, apagado))
    with cliente(catalogo_real, sessoes) as client:
        client.cookies.set(
            COOKIE_ENTRADA, criar_cookie_entrada(client.app.state.chave, apagado, fim)[0]
        )
        assert client.post("/api/chat/iniciar", json={}).status_code == 403


def test_forca_bruta_so_conta_erros(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    _, codigo = criar(sessoes)
    with cliente(catalogo_real, sessoes) as client:
        for _ in range(30):
            assert client.post("/api/entrada", json={"codigo": codigo}).status_code == 200
        for _ in range(10):
            assert client.post("/api/entrada", json={"codigo": "ZZZZ-ZZZZ"}).status_code == 404
        assert client.post("/api/entrada", json={"codigo": codigo}).status_code == 429


def test_liberacao_dura_no_maximo_12h() -> None:
    from app.web.entrada import criar_cookie_entrada

    _, longo = criar_cookie_entrada(CHAVE, 1, agora() + timedelta(days=30))
    assert 12 * 3600 - 5 <= longo <= 12 * 3600
    _, curto = criar_cookie_entrada(CHAVE, 1, agora() + timedelta(hours=1))
    assert 3600 - 5 <= curto <= 3600


def test_sair_apaga_a_liberacao(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    _, codigo = criar(sessoes)
    with cliente(catalogo_real, sessoes) as client:
        client.post("/api/entrada", json={"codigo": codigo})
        assert client.post("/api/saida").status_code == 200
        assert client.post("/api/chat/iniciar", json={}).status_code == 403


# ---- A porta na interface --------------------------------------------------------------


def test_pagina_inicial_vira_porta(catalogo_real, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as fechado:
        html = fechado.get("/").text
        assert "Digite o código para usar" in html
        assert 'id="codigo-entrada"' in html
        assert "inteligência artificial" in html  # rodapé (regra 4)
        assert "/privacidade" in html
        assert "Começar conversa" not in html
    with cliente(catalogo_real, sessoes, liberado=True) as aberto:
        assert "Começar conversa" in aberto.get("/").text


@pytest.mark.parametrize("rota", ["/chat", "/formulario", "/codigo", "/resultado"])
def test_paginas_redirecionam_sem_codigo(catalogo_real, sessoes, rota) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        resposta = client.get(rota, follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/"


@pytest.mark.parametrize("rota", ["/privacidade", "/health", "/professor"])
def test_paginas_abertas_sem_codigo(catalogo_real, sessoes, rota) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        assert client.get(rota).status_code == 200


def test_js_trata_saida(client) -> None:  # type: ignore[no-untyped-def]
    js = client.get("/static/js/comum.js").text
    assert "X-Entrada" in js and "/?saiu=1" in js
    entrada = client.get("/static/js/entrada.js").text
    assert "/api/entrada" in entrada and "saiu" in entrada

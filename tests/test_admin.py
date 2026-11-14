"""Área do admin: login e geração de códigos de entrada com período."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import CodigoEntrada, agora
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from app.routes.admin import BRT
from app.web import professor_sessao as auth
from tests.conftest import banco_em_memoria, criar_cliente

USUARIO = "autor"
SENHA = "senha-forte-do-admin"
HASH = auth.gerar_hash(SENHA)
RE_CODIGO = re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}\b")


@pytest.fixture
def sessoes():  # type: ignore[no-untyped-def]
    return banco_em_memoria()


def cliente(catalogo: Catalogo, sessoes, **cfg) -> TestClient:  # type: ignore[no-untyped-def]
    cfg.setdefault("admin_usuario", USUARIO)
    cfg.setdefault("admin_password_hash", HASH)
    return criar_cliente(FakeProvider(), catalogo, sessoes, liberado=False, **cfg)


def csrf_de(html: str) -> str:
    achado = re.search(r'name="csrf" value="([^"]+)"', html)
    assert achado, "formulário sem CSRF"
    return achado[1]


def entrar(client: TestClient) -> str:
    csrf = csrf_de(client.get("/admin").text)
    resposta = client.post("/admin/entrar", data={"csrf": csrf, "usuario": USUARIO, "senha": SENHA})
    assert resposta.status_code == 200, resposta.text
    return csrf_de(resposta.text)


def datas(inicio_h: float = 0, fim_h: float = 24 * 30) -> dict[str, str]:
    """Campos datetime-local no horário de Brasília."""
    agora_brt = agora().astimezone(BRT)
    fmt = "%Y-%m-%dT%H:%M"
    return {
        "inicio": (agora_brt + timedelta(hours=inicio_h)).strftime(fmt),
        "fim": (agora_brt + timedelta(hours=fim_h)).strftime(fmt),
    }


def test_admin_pede_login(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        resposta = client.get("/admin")
        assert resposta.status_code == 200
        assert 'type="password"' in resposta.text and 'name="usuario"' in resposta.text
        assert "Códigos de entrada" not in resposta.text
        assert resposta.headers["cache-control"] == "no-store"
        assert "noindex" in resposta.headers["x-robots-tag"]


def test_login_admin_errado_e_forca_bruta(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = csrf_de(client.get("/admin").text)
        r = client.post("/admin/entrar", data={"csrf": csrf, "usuario": "outro", "senha": SENHA})
        assert r.status_code == 401
        for _ in range(4):
            csrf = csrf_de(r.text)
            r = client.post(
                "/admin/entrar", data={"csrf": csrf, "usuario": USUARIO, "senha": "chute"}
            )
            assert r.status_code == 401
        r = client.post(
            "/admin/entrar", data={"csrf": csrf_de(r.text), "usuario": USUARIO, "senha": SENHA}
        )
        assert r.status_code == 429  # nem a senha certa passa
        assert "Códigos de entrada" not in client.get("/admin").text


def test_admin_fechado_sem_configuracao(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes, admin_usuario=None, admin_password_hash=None) as client:
        html = client.get("/admin").text
        assert "ADMIN_PASSWORD_HASH" in html
        assert re.search(r'<button[^>]*type="submit"[^>]*disabled', html)
        r = client.post("/admin/entrar", data={"csrf": csrf_de(html), "usuario": "", "senha": ""})
        assert r.status_code == 401


def test_cookie_professor_nao_vale_como_admin() -> None:
    chave = b"x" * 32
    do_professor = auth.criar_cookie(chave, HASH)  # escopo padrão "prof"
    assert auth.ler_cookie(do_professor, chave, HASH) is not None
    assert auth.ler_cookie(do_professor, chave, HASH, escopo="admin") is None
    do_admin = auth.criar_cookie(chave, HASH, escopo="admin")
    assert auth.ler_cookie(do_admin, chave, HASH, escopo="admin") is not None
    assert auth.ler_cookie(do_admin, chave, HASH) is None


def test_cria_codigo_mostra_uma_vez_e_funciona_na_porta(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        resposta = client.post("/admin/codigos", data={"csrf": csrf, "rotulo": "Piloto", **datas()})
        assert resposta.status_code == 200
        codigo = RE_CODIGO.search(resposta.text)
        assert codigo, "o código novo precisa aparecer inteiro"
        assert "não aparece de novo" in resposta.text
        lista = client.get("/admin").text
        assert codigo[0] not in lista  # só uma vez
        assert codigo[0][-4:] in lista and "Piloto" in lista and "ativo" in lista

    with criar_cliente(FakeProvider(), catalogo_real, sessoes, liberado=False) as estudante:
        assert estudante.post("/api/entrada", json={"codigo": codigo[0]}).status_code == 200


def test_codigo_agendado_e_horario_de_brasilia(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        campos = datas(inicio_h=48, fim_h=72)
        client.post("/admin/codigos", data={"csrf": csrf, "rotulo": "", **campos})
        assert "agendado" in client.get("/admin").text
    with sessoes() as db:
        registro = db.scalars(select(CodigoEntrada)).one()
        # Digitado em Brasília, gravado em UTC (3 h a mais).
        gravado = registro.inicio_em.strftime("%Y-%m-%dT%H:%M")
        assert gravado == _brt_para_utc(campos["inicio"])


def _brt_para_utc(texto: str) -> str:
    from datetime import UTC, datetime

    return (
        datetime.fromisoformat(texto).replace(tzinfo=BRT).astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
    )


@pytest.mark.parametrize(
    "campos",
    [
        {"inicio": "2026-12-10T10:00", "fim": "2026-12-01T10:00"},
        {"inicio": "2026-12-10T10:00", "fim": "2026-12-10T10:00"},
        {"inicio": "", "fim": "2026-12-01T10:00"},
        {"inicio": "amanhã", "fim": "2026-12-01T10:00"},
    ],
)
def test_datas_invalidas(catalogo_real: Catalogo, sessoes, campos) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        resposta = client.post("/admin/codigos", data={"csrf": csrf, "rotulo": "x", **campos})
        assert resposta.status_code == 400
        assert "término precisa ser depois do início" in resposta.text or "data" in resposta.text
    with sessoes() as db:
        assert db.scalars(select(CodigoEntrada)).all() == []


def test_revogar(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    from app.web.entrada import MENSAGEM_REVOGADO

    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        codigo = RE_CODIGO.search(
            client.post("/admin/codigos", data={"csrf": csrf, "rotulo": "x", **datas()}).text
        )[0]
        with sessoes() as db:
            codigo_id = db.scalars(select(CodigoEntrada.id)).one()
        resposta = client.post(f"/admin/codigos/{codigo_id}/revogar", data={"csrf": csrf})
        assert resposta.status_code == 200
        assert "revogado" in resposta.text
        r = client.post("/api/entrada", json={"codigo": codigo})
        assert r.status_code == 403 and r.json()["detail"] == MENSAGEM_REVOGADO


def test_post_admin_exige_csrf(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        entrar(client)
        r = client.post("/admin/codigos", data={"csrf": "forjado", "rotulo": "x", **datas()})
        assert r.status_code == 403
        assert client.post("/admin/sair", data={}).status_code == 403
    with sessoes() as db:
        assert db.scalars(select(CodigoEntrada)).all() == []


def test_admin_sem_login_nao_cria(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        r = client.post("/admin/codigos", data={"csrf": "x", "rotulo": "x", **datas()})
        assert r.status_code == 401

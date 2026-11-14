"""Fase 6: deploy atrás de proxy e uma turma inteira saindo pelo mesmo IP da escola."""

from pathlib import Path

import pytest

from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from tests.conftest import banco_em_memoria, criar_cliente

RAIZ = Path(__file__).resolve().parents[1]
TURMA = 40  # estudantes atrás do mesmo IP


@pytest.fixture
def sessoes():  # type: ignore[no-untyped-def]
    return banco_em_memoria()


def _ip_visto(catalogo: Catalogo, sessoes, xff: str | None, saltos: int) -> str:  # type: ignore[no-untyped-def]
    """Esgota o limite de códigos errados do IP e devolve qual IP ficou bloqueado."""
    with criar_cliente(FakeProvider(), catalogo, sessoes, proxy_saltos=saltos) as client:
        cabecalhos = {"X-Forwarded-For": xff} if xff else {}
        for _ in range(10):
            client.post("/api/codigo", json={"codigo": "ONDA-0000"}, headers=cabecalhos)
        return client.app.state.limite_codigo._eventos.keys()  # type: ignore[attr-defined, no-any-return]


def test_ip_real_atras_do_proxy(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    # Cloud Run: 1 proxy confiável, que acrescenta o IP real no fim do cabeçalho.
    assert set(_ip_visto(catalogo_real, sessoes, "200.1.2.3", 1)) == {"200.1.2.3"}
    # O que vem antes pode ser inventado pelo cliente e é ignorado.
    assert set(_ip_visto(catalogo_real, sessoes, "6.6.6.6, 200.1.2.3", 1)) == {"200.1.2.3"}
    # Sem proxy configurado (local), o cabeçalho não vale nada.
    assert set(_ip_visto(catalogo_real, sessoes, "200.1.2.3", 0)) == {"testclient"}
    # Cabeçalho ausente com proxy configurado: cai no IP da conexão.
    assert set(_ip_visto(catalogo_real, sessoes, None, 1)) == {"testclient"}


def test_turma_com_codigo_certo_nao_gasta_o_limite(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        codigos = [
            client.post("/api/formulario", json={"respostas": {"f01": "4"}}).json()["codigo"]
            for _ in range(TURMA)
        ]
        assert all(codigos)  # 40 códigos novos pelo mesmo IP
        for codigo in codigos:
            assert client.post("/api/codigo", json={"codigo": codigo}).status_code == 200

        # Quem tenta adivinhar é bloqueado depois de 10 erros...
        for _ in range(10):
            assert client.post("/api/codigo", json={"codigo": "ONDA-0000"}).status_code == 404
        assert client.post("/api/codigo", json={"codigo": "ONDA-0000"}).status_code == 429
        # ...e aí nem o código certo passa (não dá para testar acertos durante o bloqueio).
        assert client.post("/api/codigo", json={"codigo": codigos[0]}).status_code == 429


def test_turma_inteira_comeca_a_conversa_pelo_mesmo_ip(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        for _ in range(TURMA):
            assert client.post("/api/chat/iniciar", json={}).status_code == 200


def test_dockerfile_baixa_o_modelo_no_build_e_usa_a_porta_do_ambiente() -> None:
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/baixar_modelo.py" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile  # em produção nada é baixado ao acordar
    assert "${PORT" in dockerfile
    assert "USER " in dockerfile  # não roda como root
    ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
    for item in (".env", ".venv", "*.db", ".git"):
        assert item in ignorados

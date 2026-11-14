"""Fase 5: painel do professor (login, turmas, números agregados, Padlet)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import codigo as codigos
from app.db import repositorio
from app.db.models import EventoAgregado, Turma
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from app.routes.professor import carregar_roda, padlet_valido
from app.web import professor_sessao as auth
from tests.agente_fake import analise, recomendacao
from tests.conftest import banco_em_memoria, criar_cliente
from tests.test_web import eventos_sse, token_de

SENHA = "senha-forte-do-painel"
HASH = auth.gerar_hash(SENHA)
CHAVE = b"x" * 32
TRILHAS = ("tecnico-em-informatica", "tecnico-em-design-grafico", "tecnico-em-enfermagem")
OUTRAS = ("tecnico-em-marketing", "tecnico-em-eletronica", "tecnico-em-multimidia")


@pytest.fixture
def sessoes():  # type: ignore[no-untyped-def]
    return banco_em_memoria()


def cliente(catalogo: Catalogo, sessoes, llm: FakeProvider | None = None, **cfg) -> TestClient:  # type: ignore[no-untyped-def]
    cfg.setdefault("professor_password_hash", HASH)
    return criar_cliente(llm or FakeProvider(), catalogo, sessoes, **cfg)


def csrf_de(html: str) -> str:
    achado = re.search(r'name="csrf" value="([^"]+)"', html)
    assert achado, "formulário sem CSRF"
    return achado[1]


def entrar(client: TestClient, senha: str = SENHA) -> str:
    """Faz login e devolve o CSRF da sessão (para os POST seguintes)."""
    csrf = csrf_de(client.get("/professor").text)
    resposta = client.post("/professor/entrar", data={"csrf": csrf, "senha": senha})
    assert resposta.status_code == 200, resposta.text  # 303 -> /professor
    return csrf_de(resposta.text)


def criar_turma(client: TestClient, csrf: str, **campos: str) -> str:
    dados = {"csrf": csrf, "escola": "EEB Guaramirim", "turma": "2º B", **campos}
    resposta = client.post("/professor/turmas", data=dados, follow_redirects=False)
    assert resposta.status_code == 303
    return resposta.headers["location"].rsplit("/", 1)[1]


def conversa_ate_trilhas(client: TestClient, codigo_turma: str | None, *, mais: bool) -> None:
    corpo = {"codigo_turma": codigo_turma} if codigo_turma else {}
    token = token_de(eventos_sse(client.post("/api/chat/iniciar", json=corpo).text))
    falas = ["curto games", "quero ver as sugestões"] + (["quero outras"] if mais else [])
    for fala in falas:
        eventos = eventos_sse(
            client.post("/api/chat/mensagem", json={"token": token, "texto": fala}).text
        )
        token = token_de(eventos)
    assert any(t == "recomendacao" for t, _ in eventos)


def llm_da_conversa(mais: bool = False) -> FakeProvider:
    estruturados = [
        analise(interesses=["games"]),
        analise({"quer_sugestoes": True}, interesses=["games"]),
        recomendacao(*TRILHAS),
    ]
    if mais:
        estruturados += [
            analise({"quer_mais_sugestoes": True}, interesses=["games"]),
            recomendacao(*OUTRAS),
        ]
    return FakeProvider(estruturados=estruturados)


# ---- Código da turma -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("escola", "turma", "inicio"),
    [
        ("EEB Guaramirim", "2º B", "GUA-2B-"),
        ("Escola de Educação Básica Água Verde", "3ªA", "AGU-3A-"),
        (None, "2b", "CF-2B-"),
        ("EEB Prof. Fulano", "201", "FUL-201-"),
    ],
)
def test_codigo_da_turma(escola: str | None, turma: str, inicio: str) -> None:
    codigo = codigos.gerar_codigo_turma(escola, turma)
    assert codigo.startswith(inicio) and codigos.codigo_turma_valido(codigo)
    assert not set(codigo[-2:]) & set("0O1IL5S")


def test_turma_sem_nome_e_padlet_sem_https() -> None:
    assert codigos.nome_da_turma("  º ") is None
    assert padlet_valido("") and padlet_valido("https://padlet.com/prof/turma2b")
    assert not padlet_valido("http://padlet.com/x") and not padlet_valido("javascript:alert(1)")


# ---- Login -----------------------------------------------------------------------------


def test_painel_pede_login(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        resposta = client.get("/professor")
        assert resposta.status_code == 200
        assert 'type="password"' in resposta.text and "Suas turmas" not in resposta.text
        assert resposta.headers["cache-control"] == "no-store"
        assert "noindex" in resposta.headers["x-robots-tag"]
        assert "inteligência artificial" in resposta.text  # rodapé (regra 4)
        assert client.get("/professor/turmas/GUA-2B-XX", follow_redirects=False).status_code == 303


def test_login_e_cookie_seguro(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = csrf_de(client.get("/professor").text)
        resposta = client.post(
            "/professor/entrar", data={"csrf": csrf, "senha": SENHA}, follow_redirects=False
        )
        assert resposta.status_code == 303
        cookie = next(
            c for c in resposta.headers.get_list("set-cookie") if c.startswith("cf_prof=")
        )
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/professor" in cookie
        assert "Suas turmas" in client.get("/professor").text


def test_senha_errada_csrf_ausente_e_forca_bruta(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        sem_csrf = client.post("/professor/entrar", data={"senha": SENHA})
        assert sem_csrf.status_code == 403 and "Suas turmas" not in sem_csrf.text
        csrf = csrf_de(sem_csrf.text)  # cada tela de login traz um CSRF novo
        for _ in range(5):
            errada = client.post("/professor/entrar", data={"csrf": csrf, "senha": "chute"})
            assert errada.status_code == 401
            csrf = csrf_de(errada.text)
        bloqueado = client.post("/professor/entrar", data={"csrf": csrf, "senha": SENHA})
        assert bloqueado.status_code == 429  # nem a senha certa passa
        assert "Suas turmas" not in client.get("/professor").text


def test_sem_hash_configurado_o_painel_fica_fechado(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes, professor_password_hash=None) as client:
        html = client.get("/professor").text
        assert "gerar_hash_professor" in html
        resposta = client.post("/professor/entrar", data={"csrf": csrf_de(html), "senha": ""})
        assert resposta.status_code == 401


def test_cookie_forjado_ou_de_outra_senha_nao_vale() -> None:
    valor = auth.criar_cookie(CHAVE, HASH)
    assert auth.ler_cookie(valor, CHAVE, HASH) is not None
    assert auth.ler_cookie(valor, b"y" * 32, HASH) is None
    assert auth.ler_cookie(valor, CHAVE, auth.gerar_hash("outra-senha-qualquer")) is None
    assert auth.ler_cookie(valor, CHAVE, HASH, agora=10**12) is None  # expirado
    expira, nonce, assinatura = valor.split(".")
    assert auth.ler_cookie(f"{int(expira) + 999}.{nonce}.{assinatura}", CHAVE, HASH) is None


def test_post_do_painel_exige_csrf(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        entrar(client)
        resposta = client.post("/professor/turmas", data={"turma": "2B", "csrf": "forjado"})
        assert resposta.status_code == 403
        assert client.post("/professor/sair", data={}).status_code == 403
    with sessoes() as db:
        assert db.scalars(select(Turma)).all() == []


def test_sair_encerra_a_sessao(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        assert client.post("/professor/sair", data={"csrf": csrf}).status_code == 200
        assert 'type="password"' in client.get("/professor").text


# ---- Turmas e Padlet -------------------------------------------------------------------


def test_cria_turma_e_estudante_ve_o_mural(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        codigo = criar_turma(client, csrf, padlet_url="https://padlet.com/prof/2b")
        assert codigo.startswith("GUA-2B-")
        assert codigo in client.get("/professor").text

        publica = client.get(f"/api/turma/{codigo.lower()}").json()
        assert publica == {"codigo_turma": codigo, "padlet_url": "https://padlet.com/prof/2b"}
        assert client.get("/api/turma/XYZ-9Z-99").status_code == 404

        # Trocar e tirar o link.
        rota = f"/professor/turmas/{codigo}/padlet"
        ruim = client.post(rota, data={"csrf": csrf, "padlet_url": "http://inseguro.com"})
        assert ruim.status_code == 400 and "https://" in ruim.text
        client.post(rota, data={"csrf": csrf, "padlet_url": ""})
        assert client.get(f"/api/turma/{codigo}").json()["padlet_url"] is None


def test_turma_precisa_de_nome(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        resposta = client.post("/professor/turmas", data={"csrf": csrf, "turma": " "})
        assert resposta.status_code == 400 and "Diga qual é a turma" in resposta.text


def test_resultado_tem_botao_do_mural_ligado_ao_js(client: TestClient) -> None:
    assert "data-padlet" in client.get("/resultado").text
    js = client.get("/static/js/resultado.js").text
    assert "turmaDoCodigo" in js and "padlet_url" in js


# ---- Eventos agregados -----------------------------------------------------------------


def test_participacao_conta_uma_vez_e_cursos_e_tokens_vao_para_a_turma(
    catalogo_real: Catalogo, sessoes
) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        codigo = criar_turma(client, entrar(client))
    with cliente(catalogo_real, sessoes, llm_da_conversa(mais=True)) as client:
        conversa_ate_trilhas(client, codigo, mais=True)  # pediu outras trilhas

    with sessoes() as db:
        turma = repositorio.buscar_turma(db, codigo)
        eventos = db.scalars(select(EventoAgregado)).all()
        assert sum(e.tipo == "participacao" for e in eventos) == 1
        cursos = [e.curso_id for e in eventos if e.tipo == "trilha"]
        assert sorted(cursos) == sorted(TRILHAS + OUTRAS)
        assert all(e.turma_id == turma.id for e in eventos)
        entrada, saida = repositorio.tokens(db, turma.id)
        assert entrada > 0 and saida > 0
        # Nenhuma coluna liga o evento a um participante ou a uma conversa.
        colunas = set(EventoAgregado.__table__.columns.keys())
        assert colunas == {
            "id", "turma_id", "eixo", "curso_id", "tipo", "tokens_entrada", "tokens_saida",
            "criado_em",
        }  # fmt: skip


def test_conversa_sem_turma_so_registra_tokens(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes, llm_da_conversa()) as client:
        conversa_ate_trilhas(client, None, mais=False)
    with sessoes() as db:
        eventos = db.scalars(select(EventoAgregado)).all()
        assert eventos and {e.tipo for e in eventos} == {"uso"}
        assert all(e.turma_id is None for e in eventos)
        assert repositorio.tokens(db, todas=True)[0] > 0


def test_codigo_de_turma_invalido_no_chat_nao_quebra(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes, llm_da_conversa()) as client:
        conversa_ate_trilhas(client, "NAO-EXISTE-00", mais=False)
    with sessoes() as db:
        assert not any(e.tipo == "participacao" for e in db.scalars(select(EventoAgregado)))


def test_participante_com_codigo_herda_a_turma(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        codigo_turma = criar_turma(client, entrar(client))
        codigo = client.post(
            "/api/formulario",
            json={"respostas": {"f01": "5"}, "codigo_turma": codigo_turma},
        ).json()["codigo"]
    llm = FakeProvider(
        estruturados=[
            analise(interesses=["games"]),
            analise({"quer_sugestoes": True}, interesses=["games"]),
            recomendacao(*TRILHAS),
        ]
    )
    with cliente(catalogo_real, sessoes, llm) as outro:  # outro aparelho, sem código de turma
        token = token_de(eventos_sse(outro.post("/api/chat/iniciar", json={"codigo": codigo}).text))
        for fala in ["curto games", "quero ver as sugestões"]:
            token = token_de(
                eventos_sse(
                    outro.post("/api/chat/mensagem", json={"token": token, "texto": fala}).text
                )
            )
    with sessoes() as db:
        turma = repositorio.buscar_turma(db, codigo_turma)
        assert repositorio.participacoes(db, [turma.id]) == {turma.id: 1}


# ---- Painel: só agregado, e nada abaixo de 5 -------------------------------------------


def _simular_participantes(sessoes, turma_id: str, quantos: int) -> None:  # type: ignore[no-untyped-def]
    trilhas = [
        {"id": "tecnico-em-informatica", "eixo": "Informação e Comunicação"},
        {"id": "tecnico-em-enfermagem", "eixo": "Ambiente e Saúde"},
        {"id": "tecnico-em-design-grafico", "eixo": "Produção Cultural e Design"},
    ]
    with sessoes.begin() as db:
        for _ in range(quantos):
            repositorio.registrar_trilhas(db, turma_id, trilhas, nova_participacao=True)


def _turma_id(sessoes, codigo: str) -> str:  # type: ignore[no-untyped-def]
    with sessoes() as db:
        return repositorio.buscar_turma(db, codigo).id


def test_turma_com_menos_de_5_mostra_so_a_contagem(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        codigo = criar_turma(client, entrar(client))
        _simular_participantes(sessoes, _turma_id(sessoes, codigo), 4)
        html = client.get(f"/professor/turmas/{codigo}").text
        assert "<strong>4</strong>" in html
        assert "pelo menos 5 estudantes" in html
        for detalhe in ("Eixos mais sugeridos", "Informática", "Enfermagem", "roda de conversa"):
            assert detalhe not in html


def test_turma_com_5_mostra_eixos_cursos_e_roda(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes) as client:
        codigo = criar_turma(client, entrar(client))
        _simular_participantes(sessoes, _turma_id(sessoes, codigo), 5)
        html = client.get(f"/professor/turmas/{codigo}").text
        assert "<strong>5</strong>" in html
        assert "Eixos mais sugeridos" in html and "Ambiente e Saúde" in html
        assert "Técnico em Informática" in html  # nome do curso, não o id
        assert "w-100" in html  # barra cheia para o mais sugerido
        roda = carregar_roda()
        assert roda.gerais[0] in html.replace("&#39;", "'")
        assert 'style="' not in html  # CSP: nada inline


def test_minimo_de_detalhe_configuravel(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with cliente(catalogo_real, sessoes, painel_minimo_detalhe=2) as client:
        codigo = criar_turma(client, entrar(client))
        _simular_participantes(sessoes, _turma_id(sessoes, codigo), 2)
        assert "Eixos mais sugeridos" in client.get(f"/professor/turmas/{codigo}").text


def test_painel_nunca_mostra_dado_individual(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    """Estudantes com código, respostas e trilhas: nada disso aparece no painel."""
    frase = "desenho dragoes no caderno"
    with cliente(catalogo_real, sessoes) as client:
        csrf = entrar(client)
        codigo_turma = criar_turma(client, csrf)
        codigos_estudantes = [
            client.post(
                "/api/formulario",
                json={
                    "respostas": {"f01": "5", "aberta_tempo_livre": frase},
                    "codigo_turma": codigo_turma,
                },
            ).json()["codigo"]
            for _ in range(6)
        ]
        turma_id = _turma_id(sessoes, codigo_turma)
        _simular_participantes(sessoes, turma_id, 6)

        paginas = [
            client.get("/professor").text,
            client.get(f"/professor/turmas/{codigo_turma}").text,
        ]
        for html in paginas:
            assert frase not in html and "dragoes" not in html
            for codigo in codigos_estudantes:
                assert codigo not in html
            assert "Você disse que curte isso" not in html  # porque_combina individual
            assert "participante_id" not in html


def test_roda_de_conversa_usa_eixos_do_catalogo(catalogo_real: Catalogo) -> None:
    roda = carregar_roda()
    eixos = {c.eixo for c in catalogo_real.cursos}
    assert set(roda.por_eixo) <= eixos
    assert len(roda.gerais) >= 3
    blocos = roda.para(["Ambiente e Saúde", "Eixo inexistente", "Segurança", "Infraestrutura"])
    assert [t for t, _ in blocos] == ["Para qualquer turma", "Ambiente e Saúde", "Segurança"]

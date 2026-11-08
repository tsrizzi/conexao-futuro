"""Fase 4: formulário, código de acesso, banco, retenção e privacidade."""

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app.agent.formulario import carregar_formulario, perfil_do_formulario, validar_respostas
from app.db import codigo as codigos
from app.db import repositorio
from app.db.models import Base, Participante, Turma, agora
from app.db.session import migrar, url_normalizada
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from tests.agente_fake import analise, recomendacao
from tests.conftest import banco_em_memoria, criar_cliente
from tests.test_web import eventos_sse, textos, token_de

CHAVE = b"x" * 32
RESPOSTAS = {
    "f01": "5", "f07": "4", "f13": "5",  # artístico alto
    "f05": "2", "f11": "1",  # técnico baixo
    "aberta_tempo_livre": "desenho personagens e jogo",
    "aberta_materias": "curto artes, odeio química",
}  # fmt: skip


@pytest.fixture
def sessoes():  # type: ignore[no-untyped-def]
    return banco_em_memoria()


# ---- Código de acesso ------------------------------------------------------------------


def test_codigo_tem_formato_e_palavras_curadas() -> None:
    for _ in range(200):
        codigo = codigos.gerar()
        palavra, numero = codigo.split("-")
        assert palavra in codigos.PALAVRAS and len(numero) == 4 and numero.isdigit()
    assert len(set(codigos.PALAVRAS)) == len(codigos.PALAVRAS) >= 100
    assert all(p.isascii() and p.isalpha() and p.isupper() for p in codigos.PALAVRAS)


@pytest.mark.parametrize(
    "digitado", ["ONDA-4827", "onda-4827", " Onda 4827 ", "onda4827", "onda_4827"]
)
def test_codigo_sem_diferenca_de_maiusculas_e_separador(digitado: str) -> None:
    assert codigos.normalizar(digitado) == "ONDA-4827"


@pytest.mark.parametrize("digitado", ["", "ONDA", "XPTO-1234", "ONDA-48", "ONDA-48271", "1234"])
def test_codigo_invalido(digitado: str) -> None:
    assert codigos.normalizar(digitado) is None


def test_banco_guarda_so_o_hash_do_codigo(sessoes) -> None:  # type: ignore[no-untyped-def]
    with sessoes.begin() as db:
        participante, codigo = repositorio.criar_participante(db, CHAVE, 60)
        assert codigo not in participante.codigo_hash
        assert participante.codigo_hash == codigos.hash_do_codigo(codigo, CHAVE)
        assert repositorio.buscar_por_codigo(db, codigo.lower(), CHAVE) is participante
        assert repositorio.buscar_por_codigo(db, codigo, b"y" * 32) is None  # outra chave


# ---- Formulário ------------------------------------------------------------------------


def test_formulario_tem_18_itens_3_por_dimensao_e_3_abertas() -> None:
    formulario = carregar_formulario()
    escala = [p for p in formulario.perguntas if p.tipo == "escala"]
    assert len(escala) == 18
    for dim in "RIASEC":
        assert sum(p.dim == dim for p in escala) == 3
    assert sum(p.tipo == "aberta" for p in formulario.perguntas) == 3
    assert all(p.origem_soutec for p in escala)
    assert [o.valor for o in formulario.escala] == [1, 2, 3, 4, 5]


def test_validacao_descarta_lixo() -> None:
    validas = validar_respostas(
        {"f01": "5", "f02": "9", "f03": "", "inventada": "5", "aberta_planos": "x" * 900}
    )
    assert validas == {"f01": "5", "aberta_planos": "x" * 500}


def test_perfil_do_formulario_media_normalizada() -> None:
    perfil = perfil_do_formulario(RESPOSTAS)
    assert perfil.dimensoes.A.nota == pytest.approx((14 / 3 - 1) / 4, abs=0.01)
    assert perfil.dimensoes.R.nota == pytest.approx((1.5 - 1) / 4, abs=0.01)
    assert any("não curte" in e for e in perfil.dimensoes.R.evidencias)
    assert perfil.dimensoes.S.evidencias == []  # sem resposta, sem sinal
    assert perfil.materias_favoritas == ["curto artes, odeio química"]
    assert perfil.cobertura == pytest.approx(3 / 9, abs=0.01)  # A, R + matérias


# ---- API: formulário e código em outro dispositivo -------------------------------------


def test_formulario_gera_codigo_e_outro_dispositivo_recupera(
    catalogo_real: Catalogo, sessoes
) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as celular:
        resposta = celular.post("/api/formulario", json={"respostas": RESPOSTAS, "completo": True})
        assert resposta.status_code == 200
        dados = resposta.json()
        assert codigos.normalizar(dados["codigo"]) == dados["codigo"]
        assert dados["formulario_completo"] is True

    # "Outro dispositivo": outro cliente, sem nada guardado no navegador, só o código.
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as computador:
        recuperado = computador.post("/api/codigo", json={"codigo": dados["codigo"].lower()})
        assert recuperado.status_code == 200
        assert recuperado.json()["respostas"] == RESPOSTAS
        assert recuperado.json()["respondidas"] == len(RESPOSTAS)


def test_parar_e_continuar_depois(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        parcial = client.post("/api/formulario", json={"respostas": {"f01": "5"}}).json()
        assert parcial["formulario_completo"] is False
        continuacao = client.post(
            "/api/formulario",
            json={"respostas": {"f02": "3"}, "completo": True, "codigo": parcial["codigo"]},
        ).json()
        assert continuacao["codigo"] is None  # mesmo participante, nenhum código novo
        assert continuacao["respondidas"] == 2 and continuacao["formulario_completo"]


def test_resposta_aberta_mascara_dados_e_crise_nao_e_gravada(
    catalogo_real: Catalogo, sessoes
) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        dados = client.post(
            "/api/formulario",
            json={
                "respostas": {
                    "aberta_tempo_livre": "jogo, meu zap é (47) 99988-7766",
                    "aberta_planos": "às vezes penso em sumir",
                }
            },
        ).json()
        assert "188" in dados["acolhimento"]
        respostas = client.post("/api/codigo", json={"codigo": dados["codigo"]}).json()["respostas"]
    assert respostas == {"aberta_tempo_livre": "jogo, meu zap é [TELEFONE]"}


def test_forca_bruta_e_bloqueada(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        codigo = client.post("/api/formulario", json={"respostas": RESPOSTAS}).json()["codigo"]
        status = [
            client.post("/api/codigo", json={"codigo": f"ONDA-{n:04d}"}).status_code
            for n in range(10)
        ]
        assert set(status) == {404}
        # 11ª tentativa no mesmo IP: bloqueada, mesmo com o código certo.
        bloqueado = client.post("/api/codigo", json={"codigo": codigo})
        assert bloqueado.status_code == 429
        assert "Muitas tentativas" in bloqueado.json()["detail"]
        assert client.post("/api/chat/iniciar", json={"codigo": codigo}).status_code == 429


# ---- Chat com código: perfil e trilhas salvos, texto do chat NUNCA ---------------------


def test_chat_com_codigo_salva_perfil_e_trilhas_mas_nao_o_texto(
    catalogo_real: Catalogo, sessoes
) -> None:  # type: ignore[no-untyped-def]
    trilhas = ("tecnico-em-design-grafico", "tecnico-em-multimidia", "tecnico-em-informatica")
    frase_secreta = "minha frase super especifica sobre desenhar dragoes"
    llm = FakeProvider(
        respostas=["Vi que você curte artes! Me conta mais?", "Resposta do agente bem unica xyz."],
        estruturados=[
            analise(interesses=["desenhar"], materias_favoritas=["artes"]),
            analise({"quer_sugestoes": True}, interesses=["desenhar"]),
            recomendacao(*trilhas),
        ],
    )
    with criar_cliente(llm, catalogo_real, sessoes) as client:
        codigo = client.post("/api/formulario", json={"respostas": RESPOSTAS}).json()["codigo"]

        eventos = eventos_sse(client.post("/api/chat/iniciar", json={"codigo": codigo}).text)
        assert "formulário" in textos(eventos)  # CARREGA_FORMULARIO
        assert "Vi que você curte artes" in textos(eventos)
        token = token_de(eventos)

        for fala in [frase_secreta, "já quero ver as sugestões"]:
            eventos = eventos_sse(
                client.post("/api/chat/mensagem", json={"token": token, "texto": fala}).text
            )
            token = token_de(eventos)
        assert any(t == "recomendacao" for t, _ in eventos)

    # Outro dispositivo: as trilhas voltam pelo código.
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as outro:
        resultado = outro.post("/api/resultado", json={"codigo": codigo}).json()
        assert [t["id"] for t in resultado["trilhas"]] == list(trilhas)
        txt = outro.post("/api/resultado.txt", json={"codigo": codigo}).text
        assert "Técnico em Design Gráfico" in txt
        assert outro.post("/api/codigo", json={"codigo": codigo}).json()["tem_trilhas"]

    # O banco inteiro, tabela por tabela: nada do texto da conversa.
    with sessoes() as db:
        conteudo = ""
        for tabela in Base.metadata.sorted_tables:
            for linha in db.execute(select(tabela)):
                conteudo += repr(tuple(linha))
    assert "desenhar" in conteudo  # o perfil estruturado está lá
    assert "dragoes" not in conteudo and frase_secreta not in conteudo
    assert "bem unica xyz" not in conteudo and "Vi que você curte" not in conteudo


def test_codigo_invalido_no_chat_e_no_resultado(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    with criar_cliente(FakeProvider(), catalogo_real, sessoes) as client:
        assert client.post("/api/chat/iniciar", json={"codigo": "ONDA-0000"}).status_code == 404
        assert client.post("/api/resultado", json={"codigo": "ONDA-0000"}).status_code == 404


# ---- Retenção e migrações --------------------------------------------------------------


def test_limpeza_apaga_vencidos_em_cascata(sessoes) -> None:  # type: ignore[no-untyped-def]
    with sessoes.begin() as db:
        vencido, _ = repositorio.criar_participante(db, CHAVE, 60)
        valido, _ = repositorio.criar_participante(db, CHAVE, 60)
        repositorio.salvar_respostas(db, vencido, {"f01": "5"})
        repositorio.salvar_perfil(db, vencido.id, perfil_do_formulario({"f01": "5"}))
        repositorio.salvar_recomendacao(db, vencido.id, {"trilhas": [{"id": "x"}]})
        vencido.expira_em = agora() - timedelta(days=1)
        db.add(Turma(codigo_turma="GUA-2B-7K", expira_em=agora() - timedelta(days=1)))
        valido_id, vencido_id = valido.id, vencido.id

    with sessoes.begin() as db:
        assert repositorio.limpar_expirados(db, 60) >= 2

    with sessoes() as db:
        assert db.get(Participante, vencido_id) is None
        assert db.get(Participante, valido_id) is not None
        for tabela in ("formulario_resposta", "perfil", "recomendacao", "turma"):
            assert db.execute(text(f"select count(*) from {tabela}")).scalar() == 0


def test_codigo_expirado_nao_entra(sessoes) -> None:  # type: ignore[no-untyped-def]
    with sessoes.begin() as db:
        participante, codigo = repositorio.criar_participante(db, CHAVE, 60)
        participante.expira_em = agora() - timedelta(seconds=1)
    with sessoes() as db:
        assert repositorio.buscar_por_codigo(db, codigo, CHAVE) is None


def test_migracoes_criam_o_mesmo_esquema_dos_modelos(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'teste.db').as_posix()}"
    migrar(url)
    migrar(url)  # idempotente
    inspetor = inspect(create_engine(url))
    tabelas = set(inspetor.get_table_names()) - {"alembic_version"}
    assert tabelas == set(Base.metadata.tables)
    for nome, tabela in Base.metadata.tables.items():
        assert {c["name"] for c in inspetor.get_columns(nome)} == set(tabela.columns.keys())


def test_url_do_postgres_ganha_o_driver() -> None:
    assert url_normalizada("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert url_normalizada("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert url_normalizada("sqlite:///./dev.db") == "sqlite:///./dev.db"


def test_nenhuma_tabela_tem_campo_de_dado_pessoal() -> None:
    proibidos = {"nome", "cpf", "email", "e_mail", "telefone", "celular", "genero", "idade",
                 "nascimento", "endereco", "escola_aluno"}  # fmt: skip
    for tabela in Base.metadata.tables.values():
        assert not proibidos & set(tabela.columns.keys()), tabela.name


def test_paginas_do_formulario_e_do_codigo(client: TestClient) -> None:
    html = client.get("/formulario").text
    assert html.count('class="cartao-pergunta"') == 21
    assert "Pular" in html and "Parar e continuar depois" in html
    assert "Anote seu código!" in html and "A gente não pede seu nome." in html
    assert "Voltar com o seu código" in client.get("/codigo").text


def test_resumo_quando_nada_empolga_traz_o_que_chegou_mais_perto() -> None:
    from app.agent.formulario import resumo_do_formulario

    respostas = {"f01": "1", "f02": "3", "f03": "3", "f05": "1", "f06": "2"}
    resumo = resumo_do_formulario(respostas)
    assert "Respondeu 5 das 18" in resumo
    assert "mais ou menos): organizar informações numa planilha; vender produtos" in resumo
    assert "Nenhuma atividade foi marcada como 'tem a ver'" in resumo
    perfil = perfil_do_formulario(respostas)
    assert (
        "no formulário, mais ou menos: vender produtos ou serviços" in perfil.dimensoes.E.evidencias
    )


def test_resumo_com_atividade_preferida_e_abertas() -> None:
    from app.agent.formulario import resumo_do_formulario

    resumo = resumo_do_formulario({"f05": "5", "f11": "4", "aberta_planos": "trabalhar logo"})
    assert "(tudo a ver): montar um circuito eletrônico ou um robô." in resumo
    assert "Nenhuma atividade" not in resumo
    assert 'Depois do EM, escreveu: "trabalhar logo"' in resumo


def test_agente_recebe_o_resumo_do_formulario(catalogo_real: Catalogo, sessoes) -> None:  # type: ignore[no-untyped-def]
    llm = FakeProvider(respostas=["Vi que nada te animou muito. O que chegou perto foi vender."])
    with criar_cliente(llm, catalogo_real, sessoes) as client:
        codigo = client.post(
            "/api/formulario", json={"respostas": {"f03": "3", "f01": "1"}}
        ).json()["codigo"]
        client.post("/api/chat/iniciar", json={"codigo": codigo})
    system = llm.chamadas[0].system
    assert "O que mais chegou perto (mais ou menos): vender produtos ou serviços" in system
    assert 'Nunca diga que o estudante "olhou áreas"' in system

"""Fase 3: páginas, segurança, API de chat em streaming, sessão assinada e exportação."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.exportar import duracao, o_que_faz, para_exibicao, texto_resultado
from app.agent.ofertas import Oferta, OfertasDoCurso
from app.agent.orchestrator import Sessao
from app.agent.prompts import carregar
from app.agent.recommender import Recomendacao, Trilha
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from app.web.markdown import para_html
from app.web.token import TokenInvalido, assinar, ler
from tests.agente_fake import analise, recomendacao
from tests.conftest import criar_cliente

RAIZ = Path(__file__).resolve().parents[1]
CHAVE = b"x" * 32


def eventos_sse(texto: str) -> list[tuple[str, dict]]:
    saida = []
    for bloco in texto.strip().split("\n\n"):
        tipo = re.search(r"^event: (.+)$", bloco, re.M)
        dados = re.search(r"^data: (.+)$", bloco, re.M)
        saida.append((tipo[1] if tipo else "", json.loads(dados[1]) if dados else {}))
    return saida


def token_de(eventos: list[tuple[str, dict]]) -> str:
    return next(d["token"] for t, d in eventos if t == "sessao")


def textos(eventos: list[tuple[str, dict]]) -> str:
    return "".join(d["texto"] for t, d in eventos if t == "texto")


# ---- Páginas ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rota", ["/", "/chat", "/resultado", "/privacidade", "/formulario", "/codigo"]
)
def test_paginas_abrem_com_aviso_de_ia_e_cabecalhos(client: TestClient, rota: str) -> None:
    resposta = client.get(rota)
    assert resposta.status_code == 200
    html = resposta.text
    assert '<html lang="pt-BR"' in html
    assert "inteligência artificial" in html  # rodapé ou compositor (regra 4)
    assert "/privacidade" in html
    cabecalhos = resposta.headers
    assert "default-src 'self'" in cabecalhos["content-security-policy"]
    assert "script-src 'self'" in cabecalhos["content-security-policy"]
    assert cabecalhos["x-frame-options"] == "DENY"
    assert cabecalhos["x-content-type-options"] == "nosniff"
    # CSP estrita: nada de script, estilo ou evento inline.
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\sstyle=|\son[a-z]+=", html)


def test_inicio_mostra_tamanho_do_catalogo_e_os_tres_caminhos(
    client: TestClient, catalogo_real: Catalogo
) -> None:
    html = client.get("/").text
    sugeriveis = sum(1 for c in catalogo_real.cursos if c.eixo != "Militar")
    assert f"{sugeriveis} cursos técnicos" in html
    for texto in ["Começar conversa", "Responder o formulário antes", "Já tenho um código"]:
        assert texto in html
    assert catalogo_real.edicao in html


def test_privacidade_explica_o_que_e_guardado(client: TestClient) -> None:
    html = client.get("/privacidade").text
    assert "<h1>Aviso de privacidade</h1>" in html
    assert "não é guardado no servidor" in html
    assert "188" in html


def test_arquivos_estaticos(client: TestClient) -> None:
    for caminho in ["/static/css/app.css", "/static/js/chat.js", "/static/img/icone.svg"]:
        assert client.get(caminho).status_code == 200


def test_bootstrap_com_integridade(client: TestClient) -> None:
    html = client.get("/").text
    assert 'integrity="sha384-' in html and "bootstrap@5.3.3" in html


# ---- API de chat -----------------------------------------------------------------------


def test_iniciar_traz_boas_vindas_e_token(client: TestClient) -> None:
    resposta = client.post("/api/chat/iniciar")
    assert resposta.headers["content-type"].startswith("text/event-stream")
    eventos = eventos_sse(resposta.text)
    assert "inteligência artificial" in textos(eventos)
    assert eventos[-1][0] == "fim"
    sessao = ler(token_de(eventos), CHAVE)
    assert sessao.estado == "descoberta"


def test_conversa_ate_as_trilhas_pela_api(catalogo_real: Catalogo) -> None:
    trilhas = ("tecnico-em-informatica", "tecnico-em-design-grafico", "tecnico-em-enfermagem")
    llm = FakeProvider(
        respostas=["Que legal! E na escola? [[opcoes: Matemática | Artes]]"],
        estruturados=[
            analise(interesses=["games"]),
            analise({"quer_sugestoes": True}, interesses=["games"]),
            recomendacao(*trilhas),
        ],
    )
    with criar_cliente(llm, catalogo_real) as client:
        token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))

        eventos = eventos_sse(
            client.post("/api/chat/mensagem", json={"token": token, "texto": "gosto de games"}).text
        )
        assert textos(eventos) == "Que legal! E na escola? "
        assert ("opcoes", {"opcoes": ["Matemática", "Artes"]}) in eventos
        assert any(t == "progresso" for t, _ in eventos)
        token = token_de(eventos)

        eventos = eventos_sse(
            client.post("/api/chat/mensagem", json={"token": token, "texto": "já quero ver"}).text
        )
        [rec] = [d for t, d in eventos if t == "recomendacao"]
        assert [t["id"] for t in rec["trilhas"]] == list(trilhas)
        assert rec["trilhas"][0]["aviso_ofertas"] == carregar("msg_sem_ofertas")
        token = token_de(eventos)

        resultado = client.post("/api/resultado", json={"token": token}).json()
        assert [t["nome"] for t in resultado["trilhas"]][0] == "Técnico em Informática"

        txt = client.post("/api/resultado.txt", json={"token": token})
        assert txt.headers["content-disposition"].startswith("attachment")
        assert "Técnico em Design Gráfico" in txt.text
        assert "inteligência artificial" in txt.text


def test_token_alterado_ou_invalido_e_recusado(client: TestClient) -> None:
    token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
    corpo, assinatura = token.split(".")
    forjado = f"{corpo[:-2]}AA.{assinatura}"
    for ruim in [forjado, "lixo", ""]:
        resposta = client.post("/api/chat/mensagem", json={"token": ruim, "texto": "oi"})
        assert resposta.status_code == 400
        assert "Comece uma nova" in resposta.json()["detail"]


def test_resultado_sem_trilhas_e_404(client: TestClient) -> None:
    token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
    assert client.post("/api/resultado", json={"token": token}).status_code == 404
    assert client.post("/api/resultado.txt", json={"token": token}).status_code == 404


def test_limite_de_mensagens_por_sessao(catalogo_real: Catalogo) -> None:
    llm = FakeProvider(estruturados=[analise()] * 5)
    with criar_cliente(llm, catalogo_real) as client:
        client.app.state.limite_mensagens.maximo = 2
        token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
        for _ in range(2):
            eventos = eventos_sse(
                client.post("/api/chat/mensagem", json={"token": token, "texto": "oi"}).text
            )
            token = token_de(eventos)
        eventos = eventos_sse(
            client.post("/api/chat/mensagem", json={"token": token, "texto": "oi"}).text
        )
        assert ("aviso", {"texto": carregar("msg_limite")}) in eventos


def test_mensagem_longa_e_dados_pessoais_pela_api(client: TestClient, fake_llm) -> None:
    token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
    eventos = eventos_sse(
        client.post("/api/chat/mensagem", json={"token": token, "texto": "a" * 501}).text
    )
    assert eventos[0] == ("aviso", {"texto": carregar("msg_mensagem_longa", limite=500)})
    assert fake_llm.chamadas == []

    texto = "meu cpf é 123.456.789-09"
    eventos = eventos_sse(
        client.post("/api/chat/mensagem", json={"token": token, "texto": texto}).text
    )
    assert eventos[0][0] == "aviso" and "CPF" in eventos[0][1]["texto"]
    sessao = ler(token_de(eventos), CHAVE)
    assert "123.456" not in sessao.model_dump_json()


def test_crise_pela_api(client: TestClient) -> None:
    token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
    eventos = eventos_sse(
        client.post("/api/chat/mensagem", json={"token": token, "texto": "quero morrer"}).text
    )
    assert "188" in textos(eventos)
    assert ("estado", {"estado": "acolhimento"}) in eventos


def test_erro_inesperado_vira_mensagem_amigavel(client: TestClient) -> None:
    async def quebra(*args, **kwargs):
        raise RuntimeError("bug")
        yield  # pragma: no cover

    client.app.state.orquestrador.responder = quebra
    token = token_de(eventos_sse(client.post("/api/chat/iniciar").text))
    eventos = eventos_sse(
        client.post("/api/chat/mensagem", json={"token": token, "texto": "oi"}).text
    )
    assert eventos[0][0] == "erro"
    assert eventos[-2][0] == "sessao"  # o navegador continua com uma sessão válida


# ---- Token -----------------------------------------------------------------------------


def test_token_ida_e_volta() -> None:
    sessao = Sessao(sugeridos=["tecnico-em-informatica"])
    assert ler(assinar(sessao, CHAVE), CHAVE) == sessao


def test_token_com_outra_chave_ou_expirado_e_recusado() -> None:
    token = assinar(Sessao(), CHAVE, agora=1_000_000)
    with pytest.raises(TokenInvalido, match="assinatura"):
        ler(token, b"y" * 32, agora=1_000_000)
    with pytest.raises(TokenInvalido, match="expirado"):
        ler(token, CHAVE, agora=1_000_000 + 13 * 3600)


# ---- Exportação ------------------------------------------------------------------------


def _recomendacao(catalogo: Catalogo) -> Recomendacao:
    curso = catalogo.get("tecnico-em-eletromecanica")
    assert curso is not None
    oferta = Oferta(
        curso_id=curso.id,
        instituicao="SENAI SC - Guaramirim",
        tipo="Sistema S",
        cidade="Guaramirim",
        distancia_km=1.4,
        forma="concomitante",
        requisito="cursando EM",
    )
    trilha = Trilha(
        curso=curso,
        porque_combina="Você disse que monta PCs.",
        atencao="Tem bastante física.",
        ofertas=OfertasDoCurso(agora=[oferta], conferir=["SENAI SC - Guaramirim"]),
    )
    return Recomendacao(
        trilhas=[trilha],
        resumo_perfil=["Você curte montar coisas."],
        pense_criticamente=["Você se imagina numa indústria?"],
        dimensoes_dominantes=[],
        incompleta=True,
        edicao_cnct=catalogo.edicao,
    )


def test_texto_para_notebooklm(catalogo_real: Catalogo) -> None:
    texto = texto_resultado(_recomendacao(catalogo_real))
    assert texto.startswith("MINHAS TRILHAS - Conexão Futuro")
    assert "Técnico em Eletromecânica\n========================" in texto
    assert "Por que combina comigo: Você disse que monta PCs." in texto
    assert "Dá para fazer junto com o Ensino Médio" in texto
    assert "SENAI SC - Guaramirim, Guaramirim, concomitante, 1.4 km (confira" in texto
    assert "Conversa curta" in texto
    assert f"Fonte: Catálogo Nacional de Cursos Técnicos, {catalogo_real.edicao}" in texto
    assert "**" not in texto and "<" not in texto


def test_campos_derivados_da_ficha(catalogo_real: Catalogo) -> None:
    curso = catalogo_real.get("tecnico-em-agente-comunitario-de-saude")
    assert curso is not None
    assert duracao(curso) == "1 ano e meio"
    faz = o_que_faz(curso)
    assert len(faz) == 3 and faz[0].startswith("Orientar e acompanhar")
    exibicao = para_exibicao(_recomendacao(catalogo_real))
    assert exibicao["trilhas"][0]["ofertas_agora"][0]["conferir"] is True
    assert exibicao["trilhas"][0]["onde_trabalha"]


def test_markdown_minimo_escapa_html() -> None:
    html = str(
        para_html(
            "# Título\n\nTexto **forte** <script>x</script>\n\n- item [link](https://cvv.org.br)"
        )
    )
    assert "<h1>Título</h1>" in html
    assert "<strong>forte</strong>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<li>item <a href="https://cvv.org.br">link</a></li>' in html

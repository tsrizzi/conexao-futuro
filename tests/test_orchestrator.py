"""Conversa completa e salvaguardas do orquestrador, com LLM falso (critérios da Fase 2)."""

from collections.abc import AsyncIterator

import pytest

from app.agent.ofertas import Ofertas
from app.agent.orchestrator import (
    Estado,
    Evento,
    EventoAviso,
    EventoErro,
    EventoOpcoes,
    EventoProgresso,
    EventoRecomendacao,
    Orquestrador,
    SeparadorOpcoes,
    Sessao,
)
from app.agent.profile import Perfil
from app.agent.prompts import carregar
from app.llm.base import MENSAGEM_SOBRECARGA
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from tests.agente_fake import BuscadorFixo, analise, dimensoes, recomendacao, texto

pytestmark = pytest.mark.anyio

TRILHAS = (
    "tecnico-em-programacao-de-jogos-digitais",
    "tecnico-em-design-grafico",
    "tecnico-em-mecatronica",
)


@pytest.fixture(scope="module")
def catalogo() -> Catalogo:
    return Catalogo.carregar()


def orquestrador(catalogo: Catalogo, llm: FakeProvider) -> Orquestrador:
    return Orquestrador(llm, catalogo, BuscadorFixo(catalogo), Ofertas([]), cobertura_minima=0.7)


async def coletar(fluxo: AsyncIterator[Evento]) -> list[Evento]:
    return [evento async for evento in fluxo]


def perfil_completo() -> dict:
    return {
        "interesses": ["games", "desenhar"],
        "materias_favoritas": ["artes"],
        "valores_trabalho": ["criatividade"],
        "restricoes": ["precisa ser perto"],
        "dimensoes": dimensoes(
            R="monta o próprio PC",
            I="gosta de entender como funciona",
            A="desenha personagens",
            S="ajuda os amigos",
            E="vende desenhos",
            C="organiza o canal",
        ),
    }


# ---- Conversa completa -----------------------------------------------------------------


async def test_conversa_completa_ate_a_recomendacao(catalogo: Catalogo) -> None:
    completo = perfil_completo()
    passos = [  # o perfil vai crescendo a cada troca, como faria o LLM
        {"interesses": ["games"], "dimensoes": dimensoes(A="desenha personagens")},
        {**completo, "dimensoes": dimensoes(A="desenha personagens", R="monta o próprio PC")},
        {**completo, "restricoes": [], "dimensoes": dimensoes(A="desenha", R="monta", I="entende")},
        {**completo, "restricoes": [], "dimensoes": dimensoes(A="a", R="r", I="i", S="s")},
        {**completo, "restricoes": []},
        completo,
    ]
    respostas = [
        "Massa! E na escola? [[opcoes: Matemática | Artes | Educação Física]]",
        "Legal. E o que você valoriza?",
        "Entendi. Você prefere pessoas ou máquinas?",
        "Show. E vender, negociar?",
        "Saquei. Tem alguma restrição?",
        "Deixa eu ver se entendi: você curte games e desenhar. Tá certo? "
        "[[opcoes: Isso mesmo! | Quero corrigir uma coisa]]",
        "O Técnico em Mecatrônica junta mecânica e eletrônica. Quer saber de outro?",
    ]
    estruturados = [analise(**p) for p in passos]
    estruturados += [analise({"confirmou_perfil": True}, **completo), recomendacao(*TRILHAS)]
    estruturados += [analise(**completo)]
    llm = FakeProvider(respostas=respostas, estruturados=estruturados)
    orq = orquestrador(catalogo, llm)
    sessao = Sessao()

    # Boas-vindas: aviso de IA, sem pedir dado pessoal e sem gastar LLM.
    inicio = await coletar(orq.iniciar(sessao))
    assert "inteligência artificial" in texto(inicio)
    assert "Não precisa me dizer seu nome" in texto(inicio)
    assert llm.chamadas == []

    # Descoberta: 1ª resposta traz botões, que não aparecem no texto.
    eventos = await coletar(orq.responder(sessao, "gosto de jogar e de desenhar"))
    assert "[[" not in texto(eventos)
    assert EventoOpcoes(["Matemática", "Artes", "Educação Física"]) in eventos
    assert sessao.estado == Estado.DESCOBERTA

    for fala in ["artes", "criatividade", "pessoas", "vendo meus desenhos"]:
        await coletar(orq.responder(sessao, fala))
    assert sessao.estado == Estado.DESCOBERTA
    assert sessao.trocas_descoberta == 5

    # 6ª troca com cobertura completa: confirma o perfil.
    eventos = await coletar(orq.responder(sessao, "precisa ser perto de casa"))
    assert sessao.estado == Estado.CONFIRMA_PERFIL
    assert texto(eventos).startswith("Deixa eu ver se entendi")
    progresso = [e for e in eventos if isinstance(e, EventoProgresso)]
    assert progresso[-1].percentual == 100

    # Confirmou: recomenda 3 trilhas do catálogo e passa a explorar.
    eventos = await coletar(orq.responder(sessao, "isso mesmo"))
    [rec] = [e.recomendacao for e in eventos if isinstance(e, EventoRecomendacao)]
    assert [t.curso.id for t in rec.trilhas] == list(TRILHAS)
    assert all(t.curso.id in catalogo for t in rec.trilhas)
    assert rec.incompleta is False
    assert texto(eventos) == carregar("msg_trilhas", quantidade=3)
    assert sessao.estado == Estado.EXPLORA
    assert sessao.sugeridos == list(TRILHAS)

    # Exploração: responde com as fichas do catálogo no contexto.
    eventos = await coletar(orq.responder(sessao, "o que faz um técnico em mecatrônica?"))
    assert "Mecatrônica junta mecânica" in texto(eventos)
    assert "curso_id: tecnico-em-mecatronica" in llm.chamadas[-1].system

    # Todas as respostas usaram o prompt do sistema; históricos começam pelo estudante.
    respostas_llm = [c for c in llm.chamadas if c.tipo == "stream"]
    assert all(c.system.startswith(carregar("sistema")) for c in respostas_llm)
    assert all(c.mensagens[0].papel == "user" for c in llm.chamadas)
    assert sessao.uso.output_tokens > 0

    # A sessão é serializável (a Fase 3 guarda entre requisições).
    assert Sessao.model_validate_json(sessao.model_dump_json()).sugeridos == list(TRILHAS)


async def test_ja_quero_ver_as_sugestoes_a_qualquer_momento(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        estruturados=[
            analise({"quer_sugestoes": True}, interesses=["games"]),
            recomendacao(*TRILHAS),
        ]
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao()
    await coletar(orq.iniciar(sessao))
    eventos = await coletar(orq.responder(sessao, "já quero ver as sugestões"))

    assert EventoAviso(carregar("msg_poucos_dados")) in eventos
    [rec] = [e.recomendacao for e in eventos if isinstance(e, EventoRecomendacao)]
    assert rec.incompleta is True
    assert sessao.estado == Estado.EXPLORA


async def test_corrigir_o_perfil_resume_de_novo_e_depois_recomenda(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        respostas=["Deixa eu ver se entendi: agora sim?"],
        estruturados=[
            analise({"confirmou_perfil": False}, **perfil_completo()),
            analise({"confirmou_perfil": False}, **perfil_completo()),
            recomendacao(*TRILHAS),
        ],
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.CONFIRMA_PERFIL)

    eventos = await coletar(orq.responder(sessao, "na verdade não curto desenhar"))
    assert texto(eventos).startswith("Deixa eu ver se entendi")
    assert sessao.estado == Estado.CONFIRMA_PERFIL

    eventos = await coletar(orq.responder(sessao, "ainda não"))
    assert any(isinstance(e, EventoRecomendacao) for e in eventos)  # não fica em loop


async def test_mais_sugestoes_e_refazer_na_exploracao(catalogo: Catalogo) -> None:
    outras = ("tecnico-em-enfermagem", "tecnico-em-marketing", "tecnico-em-eletronica")
    llm = FakeProvider(
        estruturados=[
            analise({"quer_mais_sugestoes": True}, **perfil_completo()),
            recomendacao(*outras),
            analise({"quer_refazer": True}, **perfil_completo()),
        ]
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.EXPLORA, sugeridos=list(TRILHAS))

    eventos = await coletar(orq.responder(sessao, "tem mais opções?"))
    [rec] = [e.recomendacao for e in eventos if isinstance(e, EventoRecomendacao)]
    assert [t.curso.id for t in rec.trilhas] == list(outras)
    assert sessao.sugeridos == [*TRILHAS, *outras]

    await coletar(orq.responder(sessao, "quero recomeçar, mudei de ideia"))
    assert sessao.estado == Estado.DESCOBERTA
    assert sessao.trocas_descoberta == 4


async def test_inicio_a_partir_do_formulario(catalogo: Catalogo) -> None:
    llm = FakeProvider(respostas=["Vi que você curte games e artes. O que mais?"])
    orq = orquestrador(catalogo, llm)
    sessao = Sessao()
    formulario = Perfil.model_validate(perfil_completo() | {"valores_trabalho": []})

    eventos = await coletar(orq.iniciar(sessao, perfil_formulario=formulario))
    assert "inteligência artificial" in texto(eventos)
    assert "Vi que você curte" in texto(eventos)
    assert sessao.perfil.cobertura == pytest.approx(8 / 9, abs=0.01)
    assert "formulário" in llm.chamadas[0].system


# ---- Salvaguardas ----------------------------------------------------------------------


async def test_cpf_e_telefone_sao_mascarados_antes_do_llm(catalogo: Catalogo) -> None:
    llm = FakeProvider(respostas=["Não precisa! Bora falar de você."], estruturados=[analise()])
    orq = orquestrador(catalogo, llm)
    sessao = Sessao()
    await coletar(orq.iniciar(sessao))

    eventos = await coletar(
        orq.responder(sessao, "meu cpf é 123.456.789-09 e meu zap é (47) 99988-7766")
    )
    avisos = [e.texto for e in eventos if isinstance(e, EventoAviso)]
    assert avisos == [carregar("msg_dados_pessoais", tipos="CPF e telefone")]
    enviado_ao_llm = " ".join(m.conteudo for c in llm.chamadas for m in c.mensagens)
    assert "[CPF]" in enviado_ao_llm and "[TELEFONE]" in enviado_ao_llm
    assert "123.456" not in enviado_ao_llm and "99988" not in enviado_ao_llm
    assert "123.456" not in sessao.model_dump_json()


async def test_mensagem_de_crise_dispara_acolhimento_sem_llm(catalogo: Catalogo) -> None:
    llm = FakeProvider()
    orq = orquestrador(catalogo, llm)
    sessao = Sessao()
    await coletar(orq.iniciar(sessao))
    perfil_antes = sessao.perfil.model_copy()

    eventos = await coletar(orq.responder(sessao, "às vezes penso em sumir"))

    assert sessao.estado == Estado.ACOLHIMENTO
    assert "ligue 188" in texto(eventos)
    assert "cvv.org.br" in texto(eventos)
    assert llm.chamadas == []  # resposta imediata, sem depender do LLM
    assert sessao.perfil == perfil_antes


async def test_crise_detectada_pelo_llm_e_volta_a_orientacao(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        respostas=["Tô aqui com você. Quer falar mais sobre isso?", "Bora! Do que você gosta?"],
        estruturados=[
            analise({"sofrimento_emocional": True}, interesses=["NÃO DEVE SER GRAVADO"]),
            analise(interesses=["NEM ISSO"]),
            analise({"quer_voltar_orientacao": True}),
        ],
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)

    eventos = await coletar(orq.responder(sessao, "ando meio pra baixo com tudo"))
    assert sessao.estado == Estado.ACOLHIMENTO
    assert "188" in texto(eventos)

    await coletar(orq.responder(sessao, "é que tá difícil em casa"))
    assert "acolhimento" in llm.chamadas[-1].system.lower()
    assert "orientação vocacional está PAUSADA" in llm.chamadas[-1].system

    await coletar(orq.responder(sessao, "valeu, agora quero voltar a falar dos cursos"))
    assert sessao.estado == Estado.DESCOBERTA
    assert "descoberta" in llm.chamadas[-1].system
    # Nada do momento difícil entrou no perfil.
    assert "GRAVADO" not in sessao.perfil.model_dump_json()
    assert "NEM ISSO" not in sessao.perfil.model_dump_json()


async def test_conteudo_improprio_e_mensagem_longa_nao_chegam_ao_llm(catalogo: Catalogo) -> None:
    llm = FakeProvider()
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)

    eventos = await coletar(orq.responder(sessao, "me manda nudes"))
    assert texto(eventos) == carregar("msg_improprio")

    eventos = await coletar(orq.responder(sessao, "a" * 501))
    assert eventos == [EventoAviso(carregar("msg_mensagem_longa", limite=500))]
    assert llm.chamadas == []
    assert sessao.historico == []


async def test_saida_com_curso_inventado_e_filtrada(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        respostas=["Você ia curtir o Técnico em Astronauta. E o que mais você gosta?"],
        estruturados=[analise(interesses=["espaço"])],
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)
    eventos = await coletar(orq.responder(sessao, "gosto do espaço"))
    assert texto(eventos) == "E o que mais você gosta?"
    assert "Astronauta" not in sessao.model_dump_json()


async def test_llm_fora_do_ar_mostra_mensagem_amigavel(catalogo: Catalogo) -> None:
    llm = FakeProvider(falhas_transitorias=10, max_retries=1)
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)
    eventos = await coletar(orq.responder(sessao, "gosto de robótica"))
    assert eventos == [EventoErro(MENSAGEM_SOBRECARGA)]
    assert sessao.historico == []  # pode tentar de novo sem duplicar a mensagem


async def test_extracao_invalida_nao_derruba_a_conversa(catalogo: Catalogo) -> None:
    llm = FakeProvider(respostas=["Conta mais!"], estruturados=[{"perfil": "isso não é perfil"}])
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)
    eventos = await coletar(orq.responder(sessao, "gosto de robótica"))
    assert texto(eventos) == "Conta mais!"


# ---- Botões ----------------------------------------------------------------------------


def test_separador_de_opcoes_com_marcador_quebrado_em_pedacos() -> None:
    separador = SeparadorOpcoes()
    saida = "".join(
        separador.alimentar(p) for p in ["Qual? [", "[opç", "ões: Sim | Não ", "]] fim"]
    )
    saida += separador.finalizar()
    assert saida == "Qual?  fim"
    assert separador.opcoes == ["Sim", "Não"]


def test_marcador_incompleto_e_descartado() -> None:
    separador = SeparadorOpcoes()
    assert separador.alimentar("Texto [[opcoes: a | b") == "Texto "
    assert separador.finalizar() == ""


async def test_pergunta_sobre_curso_na_descoberta_usa_a_ficha(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        respostas=["Segundo a ficha, cria peças visuais. E você, curte isso?"],
        estruturados=[analise({"pergunta_sobre_curso": True}, interesses=["desenhar"])],
    )
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA, trocas_descoberta=2)

    await coletar(orq.responder(sessao, "o que faz um técnico em design gráfico?"))

    assert "curso_id: tecnico-em-design-grafico" in llm.chamadas[-1].system
    assert "SOMENTE com o que está nas fichas" in llm.chamadas[-1].system
    assert sessao.estado == Estado.DESCOBERTA  # continua conhecendo o estudante
    assert sessao.trocas_descoberta == 2
    assert sessao.perfil.interesses == ["desenhar"]


async def test_descoberta_manda_um_foco_e_exemplos_do_soutec(catalogo: Catalogo) -> None:
    llm = FakeProvider(respostas=["Pergunta?"], estruturados=[analise(interesses=["games"])])
    orq = orquestrador(catalogo, llm)
    sessao = Sessao(estado=Estado.DESCOBERTA)
    await coletar(orq.responder(sessao, "jogo videogame"))
    system = llm.chamadas[-1].system
    assert "Foco da próxima pergunta" in system
    assert "Técnico-Operacional" in system  # trocas=1 -> foco numa dimensão sem sinal
    assert "teste SouTec" in system

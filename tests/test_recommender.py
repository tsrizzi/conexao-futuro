"""Recomendação: 100% dos curso_id devolvidos existem no catálogo (critério da Fase 2)."""

import random

import pytest

from app.agent.ofertas import Ofertas
from app.agent.profile import EixoProvavel, Perfil
from app.agent.prompts import carregar
from app.agent.recommender import Recomendador
from app.llm.fake import FakeProvider
from app.rag.catalog import Catalogo
from tests.agente_fake import IDS_CANDIDATOS, BuscadorFixo, escolha, recomendacao

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def catalogo() -> Catalogo:
    return Catalogo.carregar()


def _recomendador(catalogo: Catalogo, llm: FakeProvider) -> tuple[Recomendador, BuscadorFixo]:
    buscador = BuscadorFixo(catalogo)
    return (
        Recomendador(llm, buscador, catalogo, Ofertas([]), sistema="sistema", cobertura_minima=0.7),
        buscador,
    )


def _perfil() -> Perfil:
    perfil = Perfil(interesses=["games", "desenhar"], valores_trabalho=["criatividade"])
    perfil.dimensoes.A.nota = 0.9
    perfil.dimensoes.A.evidencias = ["desenha personagens"]
    perfil.calcular_cobertura()
    return perfil


async def test_recomendacao_valida(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        estruturados=[
            recomendacao(
                "tecnico-em-programacao-de-jogos-digitais",
                "tecnico-em-design-grafico",
                "tecnico-em-mecatronica",
            )
        ]
    )
    recomendador, buscador = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil())

    assert rec is not None
    assert [t.curso.id for t in rec.trilhas] == [
        "tecnico-em-programacao-de-jogos-digitais",
        "tecnico-em-design-grafico",
        "tecnico-em-mecatronica",
    ]
    assert rec.trilhas[0].ofertas.aviso == carregar("msg_sem_ofertas")
    assert rec.dimensoes_dominantes[0].evidencias == ["desenha personagens"]
    assert rec.incompleta is True  # cobertura baixa
    assert rec.edicao_cnct == catalogo.edicao
    assert "games" in buscador.consultas[0]
    # As fichas dos candidatos foram enviadas ao LLM.
    assert "curso_id: tecnico-em-enfermagem" in llm.chamadas[0].system


async def test_id_inventado_faz_refazer_uma_vez(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        estruturados=[
            recomendacao(
                "tecnico-em-astronauta", "tecnico-em-informatica", "tecnico-em-enfermagem"
            ),
            recomendacao(
                "tecnico-em-design-grafico", "tecnico-em-informatica", "tecnico-em-enfermagem"
            ),
        ]
    )
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil())

    assert rec is not None
    assert [t.curso.id for t in rec.trilhas][0] == "tecnico-em-design-grafico"
    assert len(llm.chamadas) == 2
    assert "'tecnico-em-astronauta' não é um curso_id das fichas" in llm.chamadas[1].system


async def test_curso_real_mas_fora_dos_candidatos_e_rejeitado(catalogo: Catalogo) -> None:
    fora = "tecnico-em-agrimensura"
    assert fora in catalogo and fora not in IDS_CANDIDATOS
    llm = FakeProvider(
        estruturados=[
            recomendacao(fora, "tecnico-em-informatica", "tecnico-em-enfermagem"),
            recomendacao(fora, "tecnico-em-informatica", "tecnico-em-enfermagem"),
        ]
    )
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil())
    assert rec is not None
    assert [t.curso.id for t in rec.trilhas] == ["tecnico-em-informatica", "tecnico-em-enfermagem"]


async def test_nada_valido_mesmo_depois_de_refazer(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        estruturados=[recomendacao("tecnico-em-x"), recomendacao("tecnico-em-y", "tecnico-em-z")]
    )
    recomendador, _ = _recomendador(catalogo, llm)
    assert await recomendador.recomendar(_perfil()) is None
    assert len(llm.chamadas) == 2  # refez só uma vez


async def test_diversidade_de_eixos(catalogo: Catalogo) -> None:
    mesmo_eixo = (
        "tecnico-em-informatica",
        "tecnico-em-desenvolvimento-de-sistemas",
        "tecnico-em-redes-de-computadores",
    )
    llm = FakeProvider(estruturados=[recomendacao(*mesmo_eixo), recomendacao(*mesmo_eixo)])
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil())

    assert rec is not None
    assert "mesmo eixo" in llm.chamadas[1].system
    eixos = {t.curso.eixo for t in rec.trilhas}
    assert len(eixos) == 2  # o código trocou a última por uma de outro eixo
    assert rec.trilhas[-1].porque_combina.startswith("Uma opção de outra área")


async def test_mesmo_eixo_permitido_quando_perfil_e_muito_claro(catalogo: Catalogo) -> None:
    mesmo_eixo = (
        "tecnico-em-informatica",
        "tecnico-em-desenvolvimento-de-sistemas",
        "tecnico-em-redes-de-computadores",
    )
    perfil = _perfil()
    perfil.eixos_provaveis = [EixoProvavel(eixo="Informação e Comunicação", confianca=0.9)]
    llm = FakeProvider(estruturados=[recomendacao(*mesmo_eixo)])
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(perfil)
    assert rec is not None and [t.curso.id for t in rec.trilhas] == list(mesmo_eixo)
    assert len(llm.chamadas) == 1


async def test_mais_sugestoes_nao_repete(catalogo: Catalogo) -> None:
    llm = FakeProvider(
        estruturados=[
            recomendacao("tecnico-em-informatica", "tecnico-em-marketing", "tecnico-em-quimica"),
            recomendacao("tecnico-em-enfermagem", "tecnico-em-marketing", "tecnico-em-quimica"),
        ]
    )
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil(), ja_sugeridos=["tecnico-em-informatica"])
    assert rec is not None
    assert "tecnico-em-informatica" not in [t.curso.id for t in rec.trilhas]
    assert "curso_id: tecnico-em-informatica\n" not in llm.chamadas[-1].system


async def test_propriedade_nenhum_id_fora_do_catalogo(catalogo: Catalogo) -> None:
    """Saídas aleatórias, com ids reais e inventados: nada fora do catálogo passa."""
    sorteio = random.Random(7)
    inventados = ["tecnico-em-astronauta", "tecnico-em-influencer", "", "TECNICO-EM-INFORMATICA"]
    for _ in range(40):
        ids = sorteio.sample(IDS_CANDIDATOS + inventados, 3)
        llm = FakeProvider(estruturados=[recomendacao(*ids), recomendacao(*ids)])
        recomendador, _ = _recomendador(catalogo, llm)
        rec = await recomendador.recomendar(_perfil())
        for trilha in rec.trilhas if rec else []:
            assert trilha.curso.id in catalogo
            assert catalogo.get(trilha.curso.id) == trilha.curso


async def test_porque_vazio_e_rejeitado(catalogo: Catalogo) -> None:
    saida = recomendacao("tecnico-em-informatica", "tecnico-em-marketing", "tecnico-em-enfermagem")
    saida["sugestoes"][0] = escolha("tecnico-em-informatica", porque=" ")
    llm = FakeProvider(estruturados=[saida, saida])
    recomendador, _ = _recomendador(catalogo, llm)
    rec = await recomendador.recomendar(_perfil())
    assert rec is not None
    assert [t.curso.id for t in rec.trilhas] == ["tecnico-em-marketing", "tecnico-em-enfermagem"]

import json
from datetime import date

import pytest

from app.agent.ofertas import Oferta, Ofertas
from app.agent.profile import (
    EixoProvavel,
    Perfil,
    eixos_validados,
    exemplos_soutec,
    mapa_dimensoes_eixos,
)
from app.agent.prompts import carregar

# ---- Perfil ----------------------------------------------------------------------------


def test_perfil_tolera_saida_baguncada_do_llm() -> None:
    perfil = Perfil.model_validate(
        {
            "interesses": ["games", "Games", "  desenhar  ", ""],
            "ambiente_preferido": "na lua",
            "dimensoes": {
                "R": {"nota": 1.7, "evidencias": ["conserta a bike"]},
                "A": {"nota": "x"},
            },
            "eixos_provaveis": [{"eixo": "Gestão e Negócios", "confianca": -3}],
        }
    )
    assert perfil.interesses == ["games", "desenhar"]
    assert perfil.ambiente_preferido is None
    assert perfil.dimensoes.R.nota == 1.0
    assert perfil.dimensoes.A.nota == 0.0
    assert perfil.eixos_provaveis[0].confianca == 0.0


def test_cobertura_conta_6_dimensoes_materias_valores_e_restricoes() -> None:
    perfil = Perfil()
    assert perfil.calcular_cobertura() == 0.0
    for codigo in "RIASEC":
        perfil.dimensoes.get(codigo).evidencias = ["algo"]
    perfil.materias_favoritas = ["matemática"]
    assert perfil.calcular_cobertura() == pytest.approx(7 / 9, abs=0.01)
    perfil.valores_trabalho = ["ajudar pessoas"]
    perfil.restricoes = ["nenhuma em especial"]
    assert perfil.calcular_cobertura() == 1.0


def test_eixos_inventados_sao_descartados_e_deduzidos_das_dimensoes() -> None:
    validos = {"Controle e Processos Industriais", "Produção Industrial", "Gestão e Negócios"}
    perfil = Perfil(eixos_provaveis=[EixoProvavel(eixo="Eixo Inventado", confianca=0.9)])
    perfil.dimensoes.R.nota = 0.8
    perfil.dimensoes.R.evidencias = ["conserta a bike"]
    eixos = eixos_validados(perfil, validos)
    assert {e.eixo for e in eixos} <= validos
    assert eixos[0].eixo == "Controle e Processos Industriais"

    perfil.eixos_provaveis = [EixoProvavel(eixo="Gestão e Negócios", confianca=0.7)]
    assert eixos_validados(perfil, validos) == perfil.eixos_provaveis


def test_exemplos_soutec_vem_da_dimensao_certa_e_variam() -> None:
    exemplos = exemplos_soutec("S", semente=1)
    assert len(exemplos) == 3
    assert exemplos != exemplos_soutec("S", semente=2)
    assert "Ensinar crianças a ler e escrever." in exemplos_soutec("S", quantidade=12)


def test_mapa_de_dimensoes_cobre_as_6() -> None:
    assert set(mapa_dimensoes_eixos()) == set("RIASEC")


def test_consulta_de_busca_usa_palavras_do_estudante() -> None:
    perfil = Perfil(interesses=["robótica"], valores_trabalho=["criatividade"])
    perfil.dimensoes.R.nota = 0.9
    perfil.dimensoes.R.evidencias = ["monta circuitos com o tio"]
    assert perfil.consulta_busca() == "robótica. criatividade. monta circuitos com o tio"


# ---- Prompts ---------------------------------------------------------------------------


def test_prompts_existem_e_variaveis_sao_substituidas() -> None:
    assert "inteligência artificial" in carregar("msg_boas_vindas")
    assert "188" in carregar("msg_acolhimento")
    assert "CPF e telefone" in carregar("msg_dados_pessoais", tipos="CPF e telefone")
    with pytest.raises(KeyError):
        carregar("msg_dados_pessoais", outra="x")


# ---- Ofertas ---------------------------------------------------------------------------


def _oferta(**extra: object) -> dict:
    base = {
        "curso_id": "tecnico-em-eletromecanica",
        "instituicao": "SENAI SC - Guaramirim",
        "tipo": "Sistema S",
        "cidade": "Guaramirim",
        "distancia_km": 1.4,
        "forma": "concomitante",
        "requisito": "cursando EM",
        "verificado_em": "2026-10-24",
    }
    return base | extra


def test_ofertas_separam_agora_e_depois(tmp_path) -> None:
    arquivo = tmp_path / "ofertas.json"
    arquivo.write_text(
        json.dumps(
            [
                _oferta(),
                _oferta(instituicao="IFSC Jaraguá", forma="subsequente", requisito="EM concluído"),
                _oferta(instituicao="Escola X", forma="integrado", requisito=None),
                _oferta(curso_id="tecnico-em-curso-inventado"),
                {"curso_id": "incompleta"},
            ]
        ),
        encoding="utf-8",
    )
    ofertas = Ofertas.carregar({"tecnico-em-eletromecanica"}, arquivo)
    do_curso = ofertas.do_curso("tecnico-em-eletromecanica", hoje=date(2026, 11, 1))
    assert [o.instituicao for o in do_curso.agora] == ["SENAI SC - Guaramirim"]
    assert [o.instituicao for o in do_curso.depois] == ["IFSC Jaraguá"]
    assert do_curso.aviso is None
    assert do_curso.conferir == []


def test_sem_ofertas_cadastradas_nunca_inventa_instituicao() -> None:
    do_curso = Ofertas([]).do_curso("tecnico-em-informatica")
    assert do_curso.agora == [] and do_curso.depois == []
    assert do_curso.aviso == carregar("msg_sem_ofertas")
    assert "IFSC" in do_curso.aviso


def test_oferta_verificada_ha_mais_de_12_meses_pede_conferencia() -> None:
    oferta = Oferta.model_validate(_oferta(verificado_em="2025-01-10"))
    do_curso = Ofertas([oferta]).do_curso(oferta.curso_id, hoje=date(2026, 10, 24))
    assert do_curso.conferir == ["SENAI SC - Guaramirim"]
    assert oferta.gratuito is None  # sem informação, não se afirma gratuidade


def test_arquivo_de_ofertas_versionado_e_valido() -> None:
    from app.agent.ofertas import CAMINHO_OFERTAS
    from app.rag.catalog import Catalogo

    catalogo = Catalogo.carregar()
    brutas = json.loads(CAMINHO_OFERTAS.read_text(encoding="utf-8"))
    ofertas = Ofertas.carregar({c.id for c in catalogo.cursos})
    total = sum(len(v) for v in ofertas._por_curso.values())
    assert total == len(brutas), "há ofertas inválidas ou de cursos fora do catálogo"


def test_proximo_foco_alterna_dimensoes_e_outros_itens() -> None:
    perfil = Perfil()
    perfil.dimensoes.A.evidencias = ["desenha personagens"]
    focos = [perfil.proximo_foco(rodada) for rodada in range(4)]
    assert focos[0] == ("matérias da escola de que gosta e de que não gosta", None)
    assert focos[1][1] == "R"  # 1ª dimensão sem sinal
    assert focos[3][1] == "I"  # muda de dimensão, não insiste na mesma
    assert all(codigo != "A" for _, codigo in focos)  # A já tem evidência

    for codigo in "RIASEC":
        perfil.dimensoes.get(codigo).evidencias = ["x"]
    perfil.materias_favoritas, perfil.valores_trabalho, perfil.restricoes = ["a"], ["b"], ["c"]
    assert perfil.proximo_foco(0)[1] is None
    assert "aprofundar" in perfil.proximo_foco(0)[0]

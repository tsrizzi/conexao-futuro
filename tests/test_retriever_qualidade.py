"""Critério de aceite da Fase 1, com o modelo de embeddings e o índice reais.

Para cada consulta no jeito de falar de um estudante, o curso esperado precisa
aparecer no top 15. Pulado se o modelo ainda não foi baixado (rode build_index.py).
"""

from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.rag.embeddings import CAMINHO_INDICE, OnnxEmbedder
from app.rag.retriever import Retriever, criar_retriever

RAIZ = Path(__file__).resolve().parents[1]
CACHE = RAIZ / ".cache" / "fastembed"

pytestmark = [
    pytest.mark.modelo,
    pytest.mark.skipif(
        not (CACHE.exists() and CAMINHO_INDICE.exists()),
        reason="modelo/índice ausentes: rode scripts/build_index.py",
    ),
]

CONSULTAS = [
    ("gosto de computador e de programar joguinhos", "tecnico-em-informatica"),
    ("quero cuidar de pessoas no hospital", "tecnico-em-enfermagem"),
    ("curto desenhar e fazer arte digital", "tecnico-em-design-grafico"),
    ("gosto de consertar carro e moto", "tecnico-em-manutencao-automotiva"),
    ("quero trabalhar com animais na fazenda", "tecnico-em-zootecnia"),
    ("amo cozinhar", "tecnico-em-gastronomia"),
    ("adoro fazer bolos e doces", "tecnico-em-confeitaria"),
    ("mexer com eletricidade e instalação elétrica", "tecnico-em-eletrotecnica"),
    ("quero ter meu próprio negócio e vender", "tecnico-em-vendas"),
    ("gosto da natureza e quero proteger o meio ambiente", "tecnico-em-meio-ambiente"),
    (
        "prevenir acidentes e cuidar da segurança dos trabalhadores",
        "tecnico-em-seguranca-do-trabalho",
    ),
    ("montar robôs e circuitos eletrônicos", "tecnico-em-mecatronica"),
    ("configurar redes e internet", "tecnico-em-redes-de-computadores"),
    ("gosto de química e de fazer experiências no laboratório", "tecnico-em-quimica"),
    ("trabalhar numa fábrica com máquinas", "tecnico-em-manutencao-de-maquinas-industriais"),
    ("fazer roupas e costurar", "tecnico-em-vestuario"),
    ("organizar papéis, planilhas e números de uma empresa", "tecnico-em-administracao"),
]


@pytest.fixture(scope="module")
def retriever() -> Retriever:
    return criar_retriever(Settings(_env_file=None))  # type: ignore[call-arg]


@pytest.mark.parametrize(("consulta", "esperado"), CONSULTAS)
def test_curso_esperado_no_top_15(retriever: Retriever, consulta: str, esperado: str) -> None:
    assert esperado in retriever.catalogo, f"{esperado} não está no catálogo"
    ids = [r.curso.id for r in retriever.buscar(consulta, k=15)]
    assert esperado in ids, f"'{consulta}': {esperado} fora do top 15 ({ids})"


def test_pelo_menos_10_consultas_no_top_3(retriever: Retriever) -> None:
    """Mais exigente que o aceite: mostra se a ordenação está boa, não só o recall."""
    acertos = sum(
        esperado in [r.curso.id for r in retriever.buscar(consulta, k=3)]
        for consulta, esperado in CONSULTAS
    )
    assert acertos >= 10, f"só {acertos}/{len(CONSULTAS)} no top 3"


def test_embedder_enxuto_equivale_ao_fastembed() -> None:
    fastembed = pytest.importorskip("fastembed")
    textos = ["gosto de computador", "Técnico em Enfermagem (Ambiente e Saúde). Cuidar 🚑"]
    modelo = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    meu = OnnxEmbedder(modelo, str(CACHE)).embed(textos)
    ref = np.array(list(fastembed.TextEmbedding(modelo, cache_dir=str(CACHE)).embed(textos)))
    ref /= np.linalg.norm(ref, axis=1, keepdims=True)
    np.testing.assert_allclose((meu * ref).sum(axis=1), 1.0, atol=1e-4)

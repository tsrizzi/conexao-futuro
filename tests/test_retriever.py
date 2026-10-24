"""Lógica da busca híbrida com um embedder falso (rápido, sem modelo)."""

import hashlib
from collections.abc import Sequence

import numpy as np
import pytest

from app.rag.catalog import Catalogo, Curso
from app.rag.embeddings import IndiceDesatualizado, IndiceVetorial, normalizar, trechos_do_curso
from app.rag.retriever import Retriever, tokenizar


class EmbedderDePalavras:
    """Saco de palavras com hashing: textos com radicais em comum ficam próximos."""

    nome = "fake-bow"

    def embed(self, textos: Sequence[str]) -> np.ndarray:
        matriz = np.zeros((len(textos), 64), dtype=np.float32)
        for i, texto in enumerate(textos):
            for token in tokenizar(texto):
                matriz[i, int(hashlib.md5(token.encode()).hexdigest(), 16) % 64] += 1
        return normalizar(matriz + 1e-6)


def curso(curso_id: str, eixo: str, texto: str) -> Curso:
    return Curso(
        id=curso_id,
        nome=f"Técnico {curso_id}",
        eixo=eixo,
        carga_horaria_min=1200,
        perfil_profissional=texto,
        texto_busca=texto,
        edicao="teste",
    )


@pytest.fixture
def catalogo() -> Catalogo:
    return Catalogo(
        [
            curso("informatica", "Informação e Comunicação", "computador programação sistemas"),
            curso("enfermagem", "Ambiente e Saúde", "cuidar pacientes hospital saúde"),
            curso("eletrotecnica", "Controle e Processos Industriais", "eletricidade instalações"),
            curso("administracao", "Gestão e Negócios", "empresa gestão negócios vendas"),
            curso("material-belico", "Militar", "computador armamento sistemas"),
        ]
    )


@pytest.fixture
def retriever(catalogo: Catalogo) -> Retriever:
    embedder = EmbedderDePalavras()
    return Retriever(catalogo, IndiceVetorial.construir(catalogo, embedder), embedder, expansao={})


def test_encontra_o_curso_pelo_assunto(retriever: Retriever) -> None:
    assert retriever.buscar("gosto de computador")[0].curso.id == "informatica"
    assert retriever.buscar("quero cuidar de pacientes")[0].curso.id == "enfermagem"


def test_respeita_k(retriever: Retriever) -> None:
    assert len(retriever.buscar("qualquer coisa", k=2)) == 2


def test_eixo_militar_fica_fora_da_busca(retriever: Retriever) -> None:
    ids = [r.curso.id for r in retriever.buscar("computador armamento sistemas", k=15)]
    assert "material-belico" not in ids
    assert len(ids) == 4


def test_bonus_de_eixo_provavel_desempata() -> None:
    gemeos = Catalogo(
        [curso("a", "Eixo A", "robótica automação"), curso("b", "Eixo B", "robótica automação")]
    )
    embedder = EmbedderDePalavras()
    r = Retriever(gemeos, IndiceVetorial.construir(gemeos, embedder), embedder, expansao={})

    assert r.buscar("robótica", eixos_provaveis={"Eixo A": 1.0})[0].curso.id == "a"
    assert r.buscar("robótica", eixos_provaveis={"Eixo B": 0.5})[0].curso.id == "b"
    resultado = r.buscar("robótica", eixos_provaveis={"Eixo B": 0.5})[0]
    assert resultado.bonus_eixo == pytest.approx(0.05)  # bônus proporcional à confiança
    assert all(x.bonus_eixo == 0 for x in r.buscar("robótica"))


def test_pesos_configuraveis(catalogo: Catalogo) -> None:
    embedder = EmbedderDePalavras()
    indice = IndiceVetorial.construir(catalogo, embedder)
    so_bm25 = Retriever(catalogo, indice, embedder, peso_embeddings=0.0, peso_bm25=1.0)
    resultado = so_bm25.buscar("eletricidade")[0]
    assert resultado.curso.id == "eletrotecnica"
    assert resultado.score == pytest.approx(resultado.score_bm25)


def test_indice_de_outro_catalogo_e_recusado(catalogo: Catalogo) -> None:
    embedder = EmbedderDePalavras()
    indice = IndiceVetorial.construir(catalogo, embedder)
    outro = Catalogo(catalogo.cursos[:3])
    with pytest.raises(IndiceDesatualizado, match="build_index"):
        Retriever(outro, indice, embedder)


def test_indice_de_outro_modelo_e_recusado(catalogo: Catalogo) -> None:
    embedder = EmbedderDePalavras()
    indice = IndiceVetorial.construir(catalogo, embedder)
    indice.modelo = "outro-modelo"
    with pytest.raises(IndiceDesatualizado, match="build_index"):
        Retriever(catalogo, indice, embedder)


def test_indice_salva_e_carrega(tmp_path, catalogo: Catalogo) -> None:
    embedder = EmbedderDePalavras()
    indice = IndiceVetorial.construir(catalogo, embedder)
    caminho = tmp_path / "indice.npz"
    indice.salvar(caminho)
    lido = IndiceVetorial.carregar(caminho)
    assert lido.ids == indice.ids
    assert lido.modelo == "fake-bow"
    np.testing.assert_allclose(lido.vetores, indice.vetores)


def test_trechos_longos_viram_janelas_com_prefixo() -> None:
    longo = curso("x", "Eixo", " ".join(f"palavra{i}" for i in range(200)))
    trechos = trechos_do_curso(longo)
    assert len(trechos) == 5  # janelas de 60 palavras, passo de 45
    assert all(t.startswith("Técnico x (Eixo).") for t in trechos)
    assert "palavra199" in trechos[-1]


def test_tokenizar_remove_stopwords_e_reduz_ao_radical() -> None:
    assert tokenizar("Eu gosto de computadores e de computação!") == ["comput", "comput"]


def test_expansao_de_consulta_liga_palavra_do_dia_a_dia_ao_catalogo(catalogo: Catalogo) -> None:
    embedder = EmbedderDePalavras()
    indice = IndiceVetorial.construir(catalogo, embedder)
    expansao = {"pc": "computador programação"}
    com = Retriever(catalogo, indice, embedder, expansao=expansao)
    assert com.buscar("curto mexer no PC")[0].curso.id == "informatica"


def test_carregar_expansao_usa_radical(tmp_path) -> None:
    from app.rag.retriever import carregar_expansao, expandir_consulta

    arquivo = tmp_path / "exp.yaml"
    arquivo.write_text("bolo: confeitaria\ndoce: confeitaria\n", encoding="utf-8")
    mapa = carregar_expansao(arquivo)
    assert expandir_consulta("adoro fazer Bolos e doces", mapa) == (
        "adoro fazer Bolos e doces (confeitaria)"
    )
    assert expandir_consulta("gosto de música", mapa) == "gosto de música"

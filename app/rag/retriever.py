"""Busca híbrida sobre o CNCT: embeddings locais + BM25, com bônus leve por eixo provável.

score = peso_embeddings * cos_normalizado + peso_bm25 * bm25_normalizado
        + bonus_eixo * confiança do eixo (se o curso for de um eixo provável)

As notas ficam entre 0 e 1 antes da soma ponderada: a de embeddings por mín-máx entre
os cursos elegíveis; a do BM25 pelo teto da consulta (ver `_normalizar_bm25`).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import snowballstemmer
import yaml
from rank_bm25 import BM25Okapi

from app.config import Settings
from app.rag.catalog import Catalogo, Curso
from app.rag.embeddings import Embedder, IndiceVetorial, OnnxEmbedder
from app.rag.ingestao import sem_acentos

# Palavras sem valor de busca, incluindo as típicas de conversa ("gosto de", "quero").
STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de da do das dos em na no nas nos por para pra pro com sem
    e ou que se como mais menos muito muita muitos muitas ja nao sim eu me mim meu minha
    voce ele ela eles elas isso isto esse essa aquilo ao aos sua seu suas seus tambem
    ser estar ter fazer gosto gostar gosta curto curtir quero queria acho coisa coisas
    tipo bem mal la aqui ali sobre entre ate quando onde qual quais quem tudo nada algo
    trabalhar trabalho area curso cursos tecnico tecnica tecnicos
    """.split()  # noqa: SIM905 - lista fácil de editar
)
_STEMMER = snowballstemmer.stemmer("portuguese")


def tokenizar(texto: str) -> list[str]:
    """Minúsculas, sem stopwords, reduzidas ao radical ("computadores" -> "comput")."""
    palavras = re.findall(r"\w+", texto.lower())
    uteis = [p for p in palavras if len(p) > 1 and sem_acentos(p) not in STOPWORDS]
    return [sem_acentos(r) for r in _STEMMER.stemWords(uteis)]


CAMINHO_EXPANSAO = Path(__file__).resolve().parents[2] / "data" / "expansao_consulta.yaml"


def carregar_expansao(caminho: Path = CAMINHO_EXPANSAO) -> dict[str, str]:
    """Mapa radical da palavra do estudante -> termos do catálogo (ver o YAML)."""
    if not caminho.exists():
        return {}
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    mapa: dict[str, str] = {}
    for palavra, termos in bruto.items():
        for radical in tokenizar(str(palavra)):
            mapa[radical] = f"{mapa[radical]} {termos}" if radical in mapa else str(termos)
    return mapa


def expandir_consulta(consulta: str, expansao: Mapping[str, str]) -> str:
    extras = dict.fromkeys(expansao[t] for t in tokenizar(consulta) if t in expansao)
    return f"{consulta} ({' '.join(extras)})" if extras else consulta


def _normalizar(notas: np.ndarray) -> np.ndarray:
    minimo, maximo = float(notas.min()), float(notas.max())
    if maximo - minimo < 1e-9:
        return np.zeros_like(notas)
    return (notas - minimo) / (maximo - minimo)


@dataclass(frozen=True)
class ResultadoBusca:
    curso: Curso
    score: float
    score_embeddings: float
    score_bm25: float
    bonus_eixo: float


class Retriever:
    def __init__(
        self,
        catalogo: Catalogo,
        indice: IndiceVetorial,
        embedder: Embedder,
        *,
        peso_embeddings: float = 0.6,
        peso_bm25: float = 0.4,
        bonus_eixo: float = 0.1,
        eixos_fora_da_busca: Iterable[str] = ("Militar",),
        expansao: Mapping[str, str] | None = None,
    ) -> None:
        indice.conferir(catalogo, embedder)
        self.catalogo = catalogo
        self._indice = indice
        self._embedder = embedder
        self.peso_embeddings = peso_embeddings
        self.peso_bm25 = peso_bm25
        self.bonus_eixo = bonus_eixo
        fora = set(eixos_fora_da_busca)
        self._elegiveis = np.array([c.eixo not in fora for c in catalogo.cursos])
        self._bm25 = BM25Okapi([tokenizar(c.texto_busca) for c in catalogo.cursos])
        self._idf_max = max(self._bm25.idf.values())
        self._expansao = carregar_expansao() if expansao is None else dict(expansao)

    def buscar(
        self,
        consulta: str,
        *,
        eixos_provaveis: Mapping[str, float] | Sequence[tuple[str, float]] = (),
        k: int = 15,
    ) -> list[ResultadoBusca]:
        eixos = dict(eixos_provaveis)
        cursos = self.catalogo.cursos
        consulta = expandir_consulta(consulta, self._expansao)
        elegiveis = np.flatnonzero(self._elegiveis)

        # Embeddings: nota do curso = melhor trecho.
        vetor = self._embedder.embed([consulta])[0]
        sim_trechos = self._indice.vetores @ vetor
        sim_cursos = np.full(len(cursos), -1.0, dtype=np.float32)
        np.maximum.at(sim_cursos, self._indice.curso_por_trecho, sim_trechos)

        tokens = tokenizar(consulta)
        bm25 = self._bm25.get_scores(tokens) if tokens else np.zeros(len(cursos))

        emb_norm = _normalizar(sim_cursos[elegiveis])
        bm25_norm = self._normalizar_bm25(np.asarray(bm25, dtype=np.float32)[elegiveis], tokens)
        bonus = np.array([self.bonus_eixo * eixos.get(cursos[i].eixo, 0.0) for i in elegiveis])
        total = self.peso_embeddings * emb_norm + self.peso_bm25 * bm25_norm + bonus

        ordem = np.argsort(-total, kind="stable")[:k]
        return [
            ResultadoBusca(
                curso=cursos[elegiveis[j]],
                score=float(total[j]),
                score_embeddings=float(emb_norm[j]),
                score_bm25=float(bm25_norm[j]),
                bonus_eixo=float(bonus[j]),
            )
            for j in ordem
        ]

    def _normalizar_bm25(self, notas: np.ndarray, tokens: list[str]) -> np.ndarray:
        """Divide pelo teto da consulta: a nota de um curso que contém cada termo uma vez.

        Diferente do mín-máx, um único acerto acidental (ex.: "doce" em "flauta doce")
        não vira nota 1,0. Termos que o catálogo não conhece entram no teto com o IDF
        máximo: quanto mais palavras desconhecidas, menos o BM25 pesa e mais os
        embeddings decidem.
        """
        teto = sum(self._bm25.idf.get(t, self._idf_max) for t in tokens)
        if teto <= 0:
            return np.zeros_like(notas)
        return np.clip(notas / teto, 0.0, 1.0)


def criar_retriever(settings: Settings, catalogo: Catalogo | None = None) -> Retriever:
    """Carrega catálogo, índice e modelo uma única vez (chamar na inicialização do app)."""
    catalogo = catalogo or Catalogo.carregar()
    embedder = OnnxEmbedder(settings.embeddings_modelo, settings.embeddings_cache_dir)
    return Retriever(
        catalogo,
        IndiceVetorial.carregar(),
        embedder,
        peso_embeddings=settings.rag_peso_embeddings,
        peso_bm25=settings.rag_peso_bm25,
        bonus_eixo=settings.rag_bonus_eixo,
        eixos_fora_da_busca=settings.rag_eixos_fora_da_busca,
    )

"""Embeddings locais (ONNX) e o índice vetorial salvo em arquivo .npz."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.rag.catalog import Catalogo, Curso

CAMINHO_INDICE = Path(__file__).resolve().parents[2] / "data" / "cnct_index.npz"

# O modelo lê no máximo ~128 tokens por texto; fichas longas viram vários trechos.
PALAVRAS_POR_TRECHO = 60
SOBREPOSICAO = 15


class Embedder(Protocol):
    nome: str

    def embed(self, textos: Sequence[str]) -> np.ndarray:
        """Matriz (n, d) float32 com linhas de norma 1."""
        ...


def normalizar(matriz: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    return (matriz / np.maximum(normas, 1e-12)).astype(np.float32)


@dataclass(frozen=True)
class ArquivosModelo:
    repo_onnx: str
    arquivo_onnx: str
    revisao_onnx: str
    repo_tokenizador: str
    arquivo_tokenizador: str
    revisao_tokenizador: str
    max_tokens: int


# Mesmo artefato ONNX (quantizado) que o fastembed usa, com revisões fixadas.
MODELOS = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": ArquivosModelo(
        repo_onnx="Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        arquivo_onnx="model_optimized.onnx",
        revisao_onnx="faf4aa4225822f3bc6376869cb1164e8e3feedd0",
        repo_tokenizador="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        arquivo_tokenizador="sentencepiece.bpe.model",
        revisao_tokenizador="e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        max_tokens=128,
    ),
}

# Tokens especiais do XLM-R; os ids do SentencePiece são deslocados em +1 (padrão fairseq).
_BOS, _PAD, _EOS, _UNK = 0, 1, 2, 3


class OnnxEmbedder:
    """Embeddings com ONNX Runtime + SentencePiece, sem o fastembed em produção.

    Produz os mesmos vetores do fastembed (há teste de equivalência), mas usa cerca de
    metade da RAM: o fastembed carrega o tokenizer.json de 250 mil termos (~250 MB) e
    otimiza o grafo duplicando pesos. Isso é o que cabe no plano gratuito do Render.
    """

    def __init__(self, modelo: str, cache_dir: str | None = None, threads: int = 1) -> None:
        import onnxruntime as ort
        import sentencepiece as spm
        from huggingface_hub import hf_hub_download

        if modelo not in MODELOS:
            raise ValueError(f"Modelo de embeddings não suportado: {modelo}")
        arquivos = MODELOS[modelo]
        caminho_onnx = hf_hub_download(
            arquivos.repo_onnx,
            arquivos.arquivo_onnx,
            revision=arquivos.revisao_onnx,
            cache_dir=cache_dir,
        )
        caminho_spm = hf_hub_download(
            arquivos.repo_tokenizador,
            arquivos.arquivo_tokenizador,
            revision=arquivos.revisao_tokenizador,
            cache_dir=cache_dir,
        )
        opcoes = ort.SessionOptions()
        opcoes.enable_cpu_mem_arena = False
        opcoes.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        # 1 thread no servidor (pouca CPU no plano gratuito); o build do índice usa mais.
        opcoes.intra_op_num_threads = threads
        opcoes.inter_op_num_threads = 1

        self.nome = modelo
        self._max_tokens = arquivos.max_tokens
        self._sp = spm.SentencePieceProcessor(model_file=caminho_spm)
        self._sessao = ort.InferenceSession(
            caminho_onnx, opcoes, providers=["CPUExecutionProvider"]
        )
        self._entradas = {e.name for e in self._sessao.get_inputs()}

    def _ids(self, texto: str) -> list[int]:
        unk = self._sp.unk_id()
        pecas = [i + 1 if i != unk else _UNK for i in self._sp.encode(texto)]
        return [_BOS, *pecas[: self._max_tokens - 2], _EOS]

    def embed(self, textos: Sequence[str], tamanho_lote: int = 32) -> np.ndarray:
        saidas = [
            self._embed_lote(textos[i : i + tamanho_lote])
            for i in range(0, len(textos), tamanho_lote)
        ]
        return normalizar(np.concatenate(saidas)) if saidas else np.zeros((0, 384), np.float32)

    def _embed_lote(self, textos: Sequence[str]) -> np.ndarray:
        sequencias = [self._ids(t) for t in textos]
        largura = max(len(s) for s in sequencias)
        input_ids = np.full((len(sequencias), largura), _PAD, dtype=np.int64)
        mascara = np.zeros((len(sequencias), largura), dtype=np.int64)
        for i, seq in enumerate(sequencias):
            input_ids[i, : len(seq)] = seq
            mascara[i, : len(seq)] = 1
        entradas = {"input_ids": input_ids, "attention_mask": mascara}
        if "token_type_ids" in self._entradas:
            entradas["token_type_ids"] = np.zeros_like(input_ids)
        estados = self._sessao.run(None, entradas)[0]
        peso = mascara[..., None].astype(np.float32)
        media: np.ndarray = (estados * peso).sum(axis=1) / peso.sum(axis=1)  # mean pooling
        return media


def trechos_do_curso(curso: Curso) -> list[str]:
    """Janelas de ~60 palavras do texto de busca, cada uma prefixada pelo nome e eixo."""
    prefixo = f"{curso.nome} ({curso.eixo})."
    palavras = curso.texto_busca.split()
    passo = PALAVRAS_POR_TRECHO - SOBREPOSICAO
    trechos = []
    for inicio in range(0, max(len(palavras) - SOBREPOSICAO, 1), passo):
        trechos.append(f"{prefixo} {' '.join(palavras[inicio : inicio + PALAVRAS_POR_TRECHO])}")
    return trechos


class IndiceDesatualizado(RuntimeError):
    pass


@dataclass
class IndiceVetorial:
    vetores: np.ndarray  # (n_trechos, d)
    curso_por_trecho: np.ndarray  # (n_trechos,) índice do curso em `ids`
    ids: list[str]
    modelo: str
    impressao: str

    @classmethod
    def construir(cls, catalogo: Catalogo, embedder: Embedder) -> "IndiceVetorial":
        textos: list[str] = []
        donos: list[int] = []
        for i, curso in enumerate(catalogo.cursos):
            for trecho in trechos_do_curso(curso):
                textos.append(trecho)
                donos.append(i)
        return cls(
            vetores=embedder.embed(textos),
            curso_por_trecho=np.array(donos, dtype=np.int32),
            ids=[c.id for c in catalogo.cursos],
            modelo=embedder.nome,
            impressao=catalogo.impressao_digital(),
        )

    def salvar(self, caminho: Path = CAMINHO_INDICE) -> None:
        np.savez_compressed(
            caminho,
            vetores=self.vetores,
            curso_por_trecho=self.curso_por_trecho,
            ids=np.array(self.ids),
            modelo=np.array(self.modelo),
            impressao=np.array(self.impressao),
        )

    @classmethod
    def carregar(cls, caminho: Path = CAMINHO_INDICE) -> "IndiceVetorial":
        if not caminho.exists():
            raise IndiceDesatualizado(f"{caminho.name} não existe: rode scripts/build_index.py")
        with np.load(caminho) as dados:
            return cls(
                vetores=dados["vetores"].astype(np.float32),
                curso_por_trecho=dados["curso_por_trecho"],
                ids=[str(i) for i in dados["ids"]],
                modelo=str(dados["modelo"]),
                impressao=str(dados["impressao"]),
            )

    def conferir(self, catalogo: Catalogo, embedder: Embedder) -> None:
        if self.modelo != embedder.nome:
            raise IndiceDesatualizado(
                f"Índice gerado com {self.modelo}, mas o modelo configurado é {embedder.nome}. "
                "Rode scripts/build_index.py."
            )
        if self.impressao != catalogo.impressao_digital() or self.ids != [
            c.id for c in catalogo.cursos
        ]:
            raise IndiceDesatualizado(
                "O cnct.json mudou desde que o índice foi gerado. Rode scripts/build_index.py."
            )

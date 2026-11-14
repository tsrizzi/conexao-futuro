"""Baixa o modelo de embeddings para o cache local e confere se ele carrega.

Roda no build da imagem (Dockerfile): o app em produção já acorda com o modelo no disco,
sem baixar 235 MB a cada partida (HF_HUB_OFFLINE=1 garante isso).

Uso:
    uv run python scripts/baixar_modelo.py
    (EMBEDDINGS_MODELO e EMBEDDINGS_CACHE_DIR opcionais; padrão: o do app e .cache/fastembed)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.embeddings import MODELOS, OnnxEmbedder  # noqa: E402


def main() -> int:
    modelo = os.environ.get("EMBEDDINGS_MODELO") or next(iter(MODELOS))
    cache = os.environ.get("EMBEDDINGS_CACHE_DIR", ".cache/fastembed")
    embedder = OnnxEmbedder(modelo, cache)
    vetor = embedder.embed(["gosto de computador"])
    print(f"Modelo {modelo} pronto em {cache} (dimensão {vetor.shape[1]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

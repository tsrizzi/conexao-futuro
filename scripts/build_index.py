"""data/cnct.json -> data/cnct_index.npz (embeddings locais dos trechos de cada curso).

Uso:
    uv run python scripts/build_index.py

Rode de novo sempre que o cnct.json mudar (o app recusa índice desatualizado).
"""

import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.config import get_settings  # noqa: E402
from app.rag.catalog import Catalogo  # noqa: E402
from app.rag.embeddings import CAMINHO_INDICE, IndiceVetorial, OnnxEmbedder  # noqa: E402


def main() -> int:
    settings = get_settings()
    catalogo = Catalogo.carregar()
    print(f"Catálogo: {len(catalogo)} cursos ({catalogo.edicao})")
    print(f"Modelo: {settings.embeddings_modelo}")
    print(f"(baixado na 1ª vez para {settings.embeddings_cache_dir})")

    inicio = time.perf_counter()
    embedder = OnnxEmbedder(
        settings.embeddings_modelo, settings.embeddings_cache_dir, threads=os.cpu_count() or 1
    )
    indice = IndiceVetorial.construir(catalogo, embedder)
    indice.salvar()

    tamanho_kb = CAMINHO_INDICE.stat().st_size / 1024
    print(
        f"{len(indice.vetores)} trechos x {indice.vetores.shape[1]} dimensões "
        f"em {time.perf_counter() - inicio:.1f}s -> {CAMINHO_INDICE.relative_to(RAIZ)} "
        f"({tamanho_kb:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

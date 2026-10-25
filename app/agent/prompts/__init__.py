"""Prompts e mensagens fixas do agente, versionados como arquivos .md nesta pasta.

Os textos usam `$variavel` (string.Template), para não conflitar com chaves de JSON.
"""

from functools import lru_cache
from pathlib import Path
from string import Template

PASTA = Path(__file__).resolve().parent


@lru_cache
def _ler(nome: str) -> str:
    return (PASTA / f"{nome}.md").read_text(encoding="utf-8").strip()


def carregar(nome: str, **variaveis: object) -> str:
    texto = _ler(nome)
    if not variaveis:
        return texto
    return Template(texto).substitute({k: str(v) for k, v in variaveis.items()})

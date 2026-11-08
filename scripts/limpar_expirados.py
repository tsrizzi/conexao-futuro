"""Apaga dados vencidos (códigos, respostas, perfis, trilhas, turmas, eventos).

O app já faz isso sozinho ao iniciar e a cada 24 h. Este script serve para rodar à mão
ou num cron externo.

Uso:
    uv run python scripts/limpar_expirados.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.repositorio import limpar_expirados  # noqa: E402
from app.db.session import criar_engine, fabrica_de_sessoes, migrar  # noqa: E402


def main() -> int:
    settings = get_settings()
    migrar(settings.database_url)
    sessoes = fabrica_de_sessoes(criar_engine(settings.database_url))
    with sessoes.begin() as db:
        apagados = limpar_expirados(db, settings.retencao_dias)
    print(
        f"{apagados} registro(s) vencido(s) apagado(s) (retenção: {settings.retencao_dias} dias)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

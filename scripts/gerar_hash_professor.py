"""Gera o PROFESSOR_PASSWORD_HASH (argon2) para o .env. A senha nunca vai para o .env.

Uso:
    uv run python scripts/gerar_hash_professor.py
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.web.professor_sessao import gerar_hash  # noqa: E402

TAMANHO_MINIMO = 12


def main() -> int:
    senha = getpass.getpass("Senha do painel (mín. 12 caracteres): ")
    if len(senha) < TAMANHO_MINIMO:
        print(f"Senha curta demais: use pelo menos {TAMANHO_MINIMO} caracteres.", file=sys.stderr)
        return 1
    if getpass.getpass("Repita a senha: ") != senha:
        print("As senhas não conferem.", file=sys.stderr)
        return 1
    print("\nCopie a linha abaixo para o .env:\n")
    # Aspas simples: o hash tem "$", que alguns carregadores de .env tentam expandir.
    print(f"PROFESSOR_PASSWORD_HASH='{gerar_hash(senha)}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

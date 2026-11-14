"""Login do painel do professor (seções 5.5 e 8.7-8.8).

- Senha conferida com argon2 contra PROFESSOR_PASSWORD_HASH (nunca a senha em claro).
- Sessão num cookie assinado com HMAC: HttpOnly, SameSite=Lax, Secure em produção, 8 h.
  A assinatura inclui o hash da senha, então trocar a senha derruba as sessões abertas.
- CSRF: todo POST do painel leva um campo `csrf` derivado da sessão. No login (ainda sem
  sessão), o padrão "double submit": o mesmo valor aleatório no cookie e no formulário.
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

COOKIE_SESSAO = "cf_prof"
COOKIE_CSRF_LOGIN = "cf_prof_login"
VALIDADE_S = 8 * 3600
_hasher = PasswordHasher()


def gerar_hash(senha: str) -> str:
    return _hasher.hash(senha)


def senha_confere(senha: str, hash_configurado: str | None) -> bool:
    if not hash_configurado or not senha:
        return False
    try:
        return _hasher.verify(hash_configurado, senha)
    except (VerificationError, InvalidHashError):
        return False


def _assinar(chave: bytes, *partes: str) -> str:
    return hmac.new(chave, "|".join(partes).encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SessaoProfessor:
    nonce: str

    def csrf(self, chave: bytes) -> str:
        return _assinar(chave, "csrf", self.nonce)

    def csrf_confere(self, chave: bytes, enviado: str | None) -> bool:
        return bool(enviado) and hmac.compare_digest(self.csrf(chave), enviado or "")


def criar_cookie(
    chave: bytes, hash_senha: str, agora: float | None = None, escopo: str = "prof"
) -> str:
    """O escopo ("prof" ou "admin") entra na assinatura: um cookie nunca vale no outro."""
    expira = str(int((agora or time.time()) + VALIDADE_S))
    nonce = secrets.token_urlsafe(16)
    return f"{expira}.{nonce}.{_assinar(chave, escopo, expira, nonce, hash_senha)}"


def ler_cookie(
    valor: str | None,
    chave: bytes,
    hash_senha: str | None,
    agora: float | None = None,
    escopo: str = "prof",
) -> SessaoProfessor | None:
    if not valor or not hash_senha or valor.count(".") != 2:
        return None
    expira, nonce, assinatura = valor.split(".")
    esperado = _assinar(chave, escopo, expira, nonce, hash_senha)
    if not hmac.compare_digest(assinatura, esperado):
        return None
    if not expira.isdigit() or int(expira) < (agora or time.time()):
        return None
    return SessaoProfessor(nonce)


def novo_csrf_login() -> str:
    return secrets.token_urlsafe(24)

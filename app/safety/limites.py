"""Limites de uso (seção 8.5): tamanho da mensagem e taxa por sessão, em memória."""

import time
from collections import deque
from collections.abc import Callable

MAX_CARACTERES = 500


class LimitadorTaxa:
    """Janela deslizante: no máximo `maximo` eventos por `janela_s` segundos por chave."""

    def __init__(
        self,
        maximo: int = 30,
        janela_s: float = 600,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maximo = maximo
        self.janela_s = janela_s
        self._relogio = relogio
        self._eventos: dict[str, deque[float]] = {}

    def _fila(self, chave: str) -> deque[float]:
        agora = self._relogio()
        fila = self._eventos.setdefault(chave, deque())
        while fila and agora - fila[0] >= self.janela_s:
            fila.popleft()
        return fila

    def bloqueado(self, chave: str) -> bool:
        """Já chegou ao máximo? (não conta como evento)"""
        return len(self._fila(chave)) >= self.maximo

    def registrar(self, chave: str) -> None:
        self._fila(chave).append(self._relogio())

    def permitir(self, chave: str) -> bool:
        """Conta o evento, se ainda couber."""
        if self.bloqueado(chave):
            return False
        self.registrar(chave)
        return True

    def limpar_inativos(self) -> None:
        agora = self._relogio()
        for chave in [
            c for c, f in self._eventos.items() if not f or agora - f[-1] >= self.janela_s
        ]:
            del self._eventos[chave]

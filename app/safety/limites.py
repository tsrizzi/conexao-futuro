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

    def permitir(self, chave: str) -> bool:
        agora = self._relogio()
        fila = self._eventos.setdefault(chave, deque())
        while fila and agora - fila[0] >= self.janela_s:
            fila.popleft()
        if len(fila) >= self.maximo:
            return False
        fila.append(agora)
        return True

    def limpar_inativos(self) -> None:
        agora = self._relogio()
        for chave in [
            c for c, f in self._eventos.items() if not f or agora - f[-1] >= self.janela_s
        ]:
            del self._eventos[chave]

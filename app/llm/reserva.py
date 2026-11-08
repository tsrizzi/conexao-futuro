"""Provedor com modelo reserva: se o principal seguir sobrecarregado, usa o reserva.

Os provedores às vezes ficam sobrecarregados para todo mundo (Anthropic 529, Gemini 503).
Depois de esgotar as novas tentativas no modelo principal, a mesma chamada vai para o
reserva. Para o estudante, só fica um pouco mais lento, em vez de dar erro.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic import BaseModel

from app.llm.base import (
    Blocos,
    LLMIndisponivel,
    LLMProvider,
    Mensagem,
    RespostaEstruturada,
    RespostaLLM,
    Sistema,
    StreamLLM,
    Uso,
)


class ProvedorComReserva(LLMProvider):
    def __init__(self, principal: LLMProvider, reserva: LLMProvider) -> None:
        # Sem semáforo nem retry próprios: cada provedor já cuida dos seus.
        super().__init__(modelo=principal.modelo, max_concurrency=10**6, max_retries=0)
        self.principal = principal
        self.reserva = reserva
        self.nome = f"{principal.nome}+reserva"
        self.max_tokens = principal.max_tokens

    async def complete(
        self, system: Sistema, mensagens: Sequence[Mensagem], *, max_tokens: int | None = None
    ) -> RespostaLLM:
        try:
            return await self.principal.complete(system, mensagens, max_tokens=max_tokens)
        except LLMIndisponivel:
            return await self.reserva.complete(system, mensagens, max_tokens=max_tokens)

    async def extract[T: BaseModel](
        self,
        system: Sistema,
        mensagens: Sequence[Mensagem],
        schema: type[T],
        *,
        max_tokens: int | None = None,
    ) -> RespostaEstruturada[T]:
        try:
            return await self.principal.extract(system, mensagens, schema, max_tokens=max_tokens)
        except LLMIndisponivel:
            return await self.reserva.extract(system, mensagens, schema, max_tokens=max_tokens)

    def stream(
        self, system: Sistema, mensagens: Sequence[Mensagem], *, max_tokens: int | None = None
    ) -> StreamLLM:
        async def gerar(uso: Uso) -> AsyncIterator[str]:
            principal = self.principal.stream(system, mensagens, max_tokens=max_tokens)
            emitiu = False
            try:
                async for pedaco in principal:
                    emitiu = True
                    yield pedaco
                uso.somar(principal.uso)
                return
            except LLMIndisponivel:
                uso.somar(principal.uso)
                if emitiu:  # já apareceu texto na tela: não dá para trocar no meio
                    raise
            reserva = self.reserva.stream(system, mensagens, max_tokens=max_tokens)
            async for pedaco in reserva:
                yield pedaco
            uso.somar(reserva.uso)

        return StreamLLM(gerar)

    async def aclose(self) -> None:
        await self.principal.aclose()
        await self.reserva.aclose()

    # Nunca chamados: as chamadas públicas acima delegam aos provedores reais.
    async def _complete(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM:
        raise NotImplementedError

    def _stream(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def _extract(
        self,
        system: Blocos,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]:
        raise NotImplementedError

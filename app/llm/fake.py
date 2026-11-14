"""Provedor falso e determinístico para os testes. Não chama nenhuma API.

Com `atraso_s` > 0 imita a demora de um LLM de verdade (antes da 1ª palavra e entre
palavras), para o teste de carga manter as conexões abertas como no uso real.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.base import (
    Blocos,
    LLMErro,
    LLMProvider,
    LLMSobrecarregado,
    Mensagem,
    RespostaLLM,
    Uso,
    juntar_blocos,
)

RESPOSTA_PADRAO = "Oi! Eu sou uma inteligência artificial (resposta de teste)."


@dataclass(frozen=True)
class ChamadaFake:
    tipo: str
    system: str
    mensagens: tuple[Mensagem, ...]


class FakeProvider(LLMProvider):
    nome = "fake"

    def __init__(
        self,
        *,
        respostas: Iterable[str] = (),
        estruturados: Iterable[dict[str, Any]] = (),
        falhas_transitorias: int = 0,
        modelo: str = "fake",
        atraso_s: float = 0.0,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("backoff_base_s", 0.0)
        kwargs.setdefault("backoff_max_s", 0.0)
        super().__init__(modelo=modelo, **kwargs)
        self._respostas = deque(respostas)
        self._estruturados = deque(estruturados)
        self._falhas = falhas_transitorias
        self._atraso_s = atraso_s
        self.chamadas: list[ChamadaFake] = []

    def _registrar(self, tipo: str, system: Blocos, mensagens: Sequence[Mensagem]) -> None:
        self.chamadas.append(ChamadaFake(tipo, juntar_blocos(system), tuple(mensagens)))
        if self._falhas > 0:
            self._falhas -= 1
            raise LLMSobrecarregado("falha transitória simulada")

    def _proxima_resposta(self) -> str:
        return self._respostas.popleft() if self._respostas else RESPOSTA_PADRAO

    @staticmethod
    def _uso(texto: str) -> Uso:
        return Uso(input_tokens=10, output_tokens=len(texto.split()))

    async def _complete(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM:
        self._registrar("complete", system, mensagens)
        await asyncio.sleep(self._atraso_s * 5)
        texto = self._proxima_resposta()
        return RespostaLLM(texto=texto, uso=self._uso(texto))

    async def _stream(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        self._registrar("stream", system, mensagens)
        await asyncio.sleep(self._atraso_s * 10)  # ~1 s até a 1ª palavra com 0,1
        texto = self._proxima_resposta()
        palavras = texto.split(" ")
        for i, palavra in enumerate(palavras):
            if self._atraso_s:
                await asyncio.sleep(self._atraso_s)
            yield palavra if i == len(palavras) - 1 else palavra + " "
        uso.somar(self._uso(texto))

    async def _extract(
        self,
        system: Blocos,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]:
        self._registrar("extract", system, mensagens)
        await asyncio.sleep(self._atraso_s * 10)
        if not self._estruturados:
            raise LLMErro(f"FakeProvider sem resposta estruturada roteirizada para {nome_schema}")
        return self._estruturados.popleft(), Uso(input_tokens=10, output_tokens=5)

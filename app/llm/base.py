"""Interface única para provedores de LLM.

Todo acesso a LLM passa por `LLMProvider`. A classe base cuida do que é comum a todos os
adaptadores: semáforo de concorrência, retry com backoff exponencial para sobrecarga
(429/529/5xx) e validação da saída estruturada com Pydantic. Os adaptadores implementam
só `_complete`, `_stream` e `_extract`.
"""

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

MENSAGEM_SOBRECARGA = "muita gente conversando agora, tenta de novo em alguns segundos 😉"

Papel = Literal["user", "assistant"]

# O prompt do sistema pode vir em blocos: o 1º é o fixo (vai para o cache de prompt) e os
# seguintes mudam a cada etapa. Os adaptadores sem cache simplesmente juntam os blocos.
Sistema = str | Sequence[str]
Blocos = tuple[str, ...]


def blocos(system: Sistema) -> Blocos:
    return (system,) if isinstance(system, str) else tuple(b for b in system if b)


def juntar_blocos(system: Blocos) -> str:
    return "\n\n".join(system)


@dataclass(frozen=True)
class Mensagem:
    papel: Papel
    conteudo: str


@dataclass
class Uso:
    """Tokens consumidos numa chamada (seção 10: registrar custo por sessão)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def somar(self, outro: "Uso") -> None:
        self.input_tokens += outro.input_tokens
        self.output_tokens += outro.output_tokens
        self.cache_read_tokens += outro.cache_read_tokens
        self.cache_write_tokens += outro.cache_write_tokens


@dataclass
class RespostaLLM:
    texto: str
    uso: Uso = field(default_factory=Uso)


@dataclass
class RespostaEstruturada[T: BaseModel]:
    dados: T
    uso: Uso = field(default_factory=Uso)


class LLMErro(Exception):
    """Erro genérico de LLM (não deve ser repetido)."""


class LLMSobrecarregado(LLMErro):
    """Erro transitório (limite de taxa, sobrecarga, falha de rede). Vale repetir."""


class LLMIndisponivel(LLMErro):
    """O provedor seguiu falhando depois de todas as tentativas."""

    def __init__(self, causa: Exception | None = None) -> None:
        super().__init__(MENSAGEM_SOBRECARGA)
        self.causa = causa


class LLMRespostaInvalida(LLMErro):
    """A saída estruturada não passou na validação do esquema."""


class StreamLLM:
    """Iterável assíncrono de pedaços de texto. `uso` fica preenchido ao final."""

    def __init__(self, fabrica: Callable[["Uso"], AsyncIterator[str]]) -> None:
        self._fabrica = fabrica
        self.uso = Uso()

    def __aiter__(self) -> AsyncIterator[str]:
        return self._fabrica(self.uso)


class LLMProvider(ABC):
    nome: str = "base"

    def __init__(
        self,
        *,
        modelo: str,
        max_tokens: int = 1024,
        max_concurrency: int = 8,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 8.0,
    ) -> None:
        self.modelo = modelo
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._semaforo = asyncio.Semaphore(max_concurrency)

    # ---- API pública -------------------------------------------------------------------

    async def complete(
        self, system: Sistema, mensagens: Sequence[Mensagem], *, max_tokens: int | None = None
    ) -> RespostaLLM:
        limite = max_tokens or self.max_tokens
        async with self._semaforo:
            sistema = blocos(system)
            return await self._com_retry(lambda: self._complete(sistema, mensagens, limite))

    def stream(
        self, system: Sistema, mensagens: Sequence[Mensagem], *, max_tokens: int | None = None
    ) -> StreamLLM:
        limite = max_tokens or self.max_tokens
        sistema = blocos(system)

        async def gerar(uso: Uso) -> AsyncIterator[str]:
            async with self._semaforo:
                tentativa = 0
                while True:
                    emitiu = False
                    try:
                        async for pedaco in self._stream(sistema, mensagens, limite, uso):
                            emitiu = True
                            yield pedaco
                        return
                    except LLMSobrecarregado as erro:
                        # Só repete se nada foi mostrado ao estudante ainda.
                        if emitiu or tentativa >= self.max_retries:
                            raise LLMIndisponivel(erro) from erro
                        await self._esperar(tentativa)
                        tentativa += 1

        return StreamLLM(gerar)

    async def extract[T: BaseModel](
        self,
        system: Sistema,
        mensagens: Sequence[Mensagem],
        schema: type[T],
        *,
        max_tokens: int | None = None,
    ) -> RespostaEstruturada[T]:
        limite = max_tokens or self.max_tokens
        json_schema = schema.model_json_schema()
        sistema = blocos(system)
        async with self._semaforo:
            bruto, uso = await self._com_retry(
                lambda: self._extract(sistema, mensagens, json_schema, schema.__name__, limite)
            )
        try:
            return RespostaEstruturada(dados=schema.model_validate(bruto), uso=uso)
        except ValidationError as erro:
            raise LLMRespostaInvalida(str(erro)) from erro

    async def aclose(self) -> None:  # noqa: B027 - opcional para os adaptadores
        """Libera conexões. Adaptadores com cliente HTTP sobrescrevem."""

    # ---- Para os adaptadores -----------------------------------------------------------

    @abstractmethod
    async def _complete(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM: ...

    @abstractmethod
    def _stream(
        self, system: Blocos, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        """Gera os pedaços de texto e preenche `uso` ao final."""

    @abstractmethod
    async def _extract(
        self,
        system: Blocos,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]: ...

    # ---- Internos ----------------------------------------------------------------------

    async def _com_retry[R](self, chamada: Callable[[], Awaitable[R]]) -> R:
        tentativa = 0
        while True:
            try:
                return await chamada()
            except LLMSobrecarregado as erro:
                if tentativa >= self.max_retries:
                    raise LLMIndisponivel(erro) from erro
                await self._esperar(tentativa)
                tentativa += 1

    async def _esperar(self, tentativa: int) -> None:
        atraso = min(self._backoff_base_s * 2**tentativa, self._backoff_max_s)
        await asyncio.sleep(atraso + random.uniform(0, atraso * 0.25))

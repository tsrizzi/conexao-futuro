"""Adaptador da API da Anthropic (padrão em produção)."""

from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
import httpx2
from anthropic.types import (
    AssistantMessageParam,
    MessageParam,
    TextBlockParam,
    ToolChoiceToolParam,
    ToolParam,
    UserMessageParam,
)

from app.llm.base import (
    LLMErro,
    LLMProvider,
    LLMRespostaInvalida,
    LLMSobrecarregado,
    Mensagem,
    RespostaLLM,
    Uso,
)

NOME_FERRAMENTA = "registrar_resultado"
ESCOLHA_FERRAMENTA: ToolChoiceToolParam = {"type": "tool", "name": NOME_FERRAMENTA}


def _sistema_com_cache(system: str) -> list[TextBlockParam]:
    # O prompt do sistema é estável: marcar para prompt caching reduz custo e latência.
    # (Abaixo do mínimo de tokens do modelo o cache simplesmente não é criado, sem erro.)
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _mensagens(mensagens: Sequence[Mensagem]) -> list[MessageParam]:
    convertidas: list[MessageParam] = []
    for m in mensagens:
        if m.papel == "assistant":
            convertidas.append(AssistantMessageParam(role="assistant", content=m.conteudo))
        else:
            convertidas.append(UserMessageParam(role="user", content=m.conteudo))
    return convertidas


def _uso(usage: Any) -> Uso:
    return Uso(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
    )


def _traduzir_erro(erro: Exception) -> LLMErro:
    if isinstance(erro, anthropic.RateLimitError | anthropic.APIConnectionError):
        return LLMSobrecarregado(str(erro))
    if isinstance(erro, anthropic.APIStatusError) and erro.status_code >= 500:
        return LLMSobrecarregado(str(erro))  # inclui 529 (overloaded)
    return LLMErro(str(erro))


class AnthropicProvider(LLMProvider):
    nome = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_s: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # max_retries=0: o retry com backoff fica na classe base, igual para todos os provedores.
        self._cliente = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=0, http_client=http_client
        )

    async def _complete(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM:
        try:
            resposta = await self._cliente.messages.create(
                model=self.modelo,
                max_tokens=max_tokens,
                system=_sistema_com_cache(system),
                messages=_mensagens(mensagens),
            )
        except anthropic.APIError as erro:
            raise _traduzir_erro(erro) from erro
        texto = "".join(b.text for b in resposta.content if b.type == "text")
        return RespostaLLM(texto=texto, uso=_uso(resposta.usage))

    async def _stream(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        try:
            async with self._cliente.messages.stream(
                model=self.modelo,
                max_tokens=max_tokens,
                system=_sistema_com_cache(system),
                messages=_mensagens(mensagens),
            ) as stream:
                async for texto in stream.text_stream:
                    yield texto
                final = await stream.get_final_message()
        except anthropic.APIError as erro:
            raise _traduzir_erro(erro) from erro
        uso.somar(_uso(final.usage))

    async def _extract(
        self,
        system: str,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]:
        ferramenta: ToolParam = {
            "name": NOME_FERRAMENTA,
            "description": f"Registra o resultado estruturado ({nome_schema}).",
            "input_schema": json_schema,
        }
        try:
            resposta = await self._cliente.messages.create(
                model=self.modelo,
                max_tokens=max_tokens,
                system=_sistema_com_cache(system),
                messages=_mensagens(mensagens),
                tools=[ferramenta],
                tool_choice=ESCOLHA_FERRAMENTA,
            )
        except anthropic.APIError as erro:
            raise _traduzir_erro(erro) from erro
        for bloco in resposta.content:
            if bloco.type == "tool_use" and bloco.name == NOME_FERRAMENTA:
                if not isinstance(bloco.input, dict):
                    break
                return dict(bloco.input), _uso(resposta.usage)
        raise LLMRespostaInvalida(f"Resposta sem a ferramenta {NOME_FERRAMENTA}")

    async def aclose(self) -> None:
        await self._cliente.close()

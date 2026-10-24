"""Adaptador do Ollama (modelos open source rodando localmente). Custo zero de API."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx2

from app.llm._http import erro_de_rede, verificar_status
from app.llm.base import LLMProvider, LLMRespostaInvalida, Mensagem, RespostaLLM, Uso

PROVEDOR = "ollama"


def _corpo(
    modelo: str, system: str, mensagens: Sequence[Mensagem], max_tokens: int, stream: bool
) -> dict[str, Any]:
    return {
        "model": modelo,
        "messages": [{"role": "system", "content": system}]
        + [{"role": m.papel, "content": m.conteudo} for m in mensagens],
        "stream": stream,
        "options": {"num_predict": max_tokens},
    }


def _uso(dados: dict[str, Any]) -> Uso:
    return Uso(
        input_tokens=dados.get("prompt_eval_count") or 0,
        output_tokens=dados.get("eval_count") or 0,
    )


class OllamaProvider(LLMProvider):
    nome = PROVEDOR

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 120.0,
        http_client: httpx2.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._cliente = http_client or httpx2.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def _post(self, corpo: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = await self._cliente.post("/api/chat", json=corpo)
        except httpx2.TransportError as erro:
            raise erro_de_rede(erro, PROVEDOR) from erro
        verificar_status(resposta, PROVEDOR)
        dados: dict[str, Any] = resposta.json()
        return dados

    async def _complete(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM:
        dados = await self._post(_corpo(self.modelo, system, mensagens, max_tokens, False))
        return RespostaLLM(texto=dados.get("message", {}).get("content", ""), uso=_uso(dados))

    async def _stream(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        corpo = _corpo(self.modelo, system, mensagens, max_tokens, True)
        try:
            async with self._cliente.stream("POST", "/api/chat", json=corpo) as resposta:
                if resposta.is_error:
                    await resposta.aread()
                verificar_status(resposta, PROVEDOR)
                async for linha in resposta.aiter_lines():
                    if not linha.strip():
                        continue
                    dados = json.loads(linha)
                    texto = dados.get("message", {}).get("content", "")
                    if texto:
                        yield texto
                    if dados.get("done"):
                        uso.somar(_uso(dados))
        except httpx2.TransportError as erro:
            raise erro_de_rede(erro, PROVEDOR) from erro

    async def _extract(
        self,
        system: str,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]:
        corpo = _corpo(self.modelo, system, mensagens, max_tokens, False)
        corpo["format"] = json_schema  # saída estruturada nativa do Ollama
        dados = await self._post(corpo)
        conteudo = dados.get("message", {}).get("content", "")
        try:
            return json.loads(conteudo), _uso(dados)
        except json.JSONDecodeError as erro:
            raise LLMRespostaInvalida(f"ollama devolveu JSON inválido para {nome_schema}") from erro

    async def aclose(self) -> None:
        await self._cliente.aclose()

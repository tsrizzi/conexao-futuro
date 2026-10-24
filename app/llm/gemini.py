"""Adaptador do Gemini, via REST (sem SDK extra).

SÓ PARA DESENVOLVIMENTO E TESTES COM ADULTOS. Os termos da API do Gemini proíbem apps
acessados por menores de 18 anos, por isso este adaptador se recusa a existir com
APP_ENV=production (a mesma trava existe em `config.py` e em `factory.py`).
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx2

from app.config import MENSAGEM_GEMINI_BLOQUEADO, AppEnv, GeminiBloqueadoEmProducao
from app.llm._http import erro_de_rede, verificar_status
from app.llm.base import LLMProvider, LLMRespostaInvalida, Mensagem, RespostaLLM, Uso

PROVEDOR = "gemini"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _corpo(system: str, mensagens: Sequence[Mensagem], max_tokens: int) -> dict[str, Any]:
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "model" if m.papel == "assistant" else "user", "parts": [{"text": m.conteudo}]}
            for m in mensagens
        ],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }


def _texto(dados: dict[str, Any]) -> str:
    candidatos = dados.get("candidates") or []
    if not candidatos:
        return ""
    partes = candidatos[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in partes)


def _uso(dados: dict[str, Any]) -> Uso:
    meta = dados.get("usageMetadata") or {}
    return Uso(
        input_tokens=meta.get("promptTokenCount") or 0,
        output_tokens=meta.get("candidatesTokenCount") or 0,
        cache_read_tokens=meta.get("cachedContentTokenCount") or 0,
    )


class GeminiProvider(LLMProvider):
    nome = PROVEDOR

    def __init__(
        self,
        *,
        app_env: AppEnv,
        api_key: str | None,
        timeout_s: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        if app_env == AppEnv.PRODUCTION:
            raise GeminiBloqueadoEmProducao(MENSAGEM_GEMINI_BLOQUEADO)
        super().__init__(**kwargs)
        self._cliente = http_client or httpx2.AsyncClient(
            base_url=BASE_URL, timeout=timeout_s, headers={"x-goog-api-key": api_key or ""}
        )

    async def _post(self, acao: str, corpo: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = await self._cliente.post(f"/models/{self.modelo}:{acao}", json=corpo)
        except httpx2.TransportError as erro:
            raise erro_de_rede(erro, PROVEDOR) from erro
        verificar_status(resposta, PROVEDOR)
        dados: dict[str, Any] = resposta.json()
        return dados

    async def _complete(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int
    ) -> RespostaLLM:
        dados = await self._post("generateContent", _corpo(system, mensagens, max_tokens))
        return RespostaLLM(texto=_texto(dados), uso=_uso(dados))

    async def _stream(
        self, system: str, mensagens: Sequence[Mensagem], max_tokens: int, uso: Uso
    ) -> AsyncIterator[str]:
        url = f"/models/{self.modelo}:streamGenerateContent"
        corpo = _corpo(system, mensagens, max_tokens)
        ultimo: dict[str, Any] = {}
        try:
            async with self._cliente.stream(
                "POST", url, params={"alt": "sse"}, json=corpo
            ) as resposta:
                if resposta.is_error:
                    await resposta.aread()
                verificar_status(resposta, PROVEDOR)
                async for linha in resposta.aiter_lines():
                    if not linha.startswith("data:"):
                        continue
                    ultimo = json.loads(linha[5:].strip())
                    texto = _texto(ultimo)
                    if texto:
                        yield texto
        except httpx2.TransportError as erro:
            raise erro_de_rede(erro, PROVEDOR) from erro
        uso.somar(_uso(ultimo))

    async def _extract(
        self,
        system: str,
        mensagens: Sequence[Mensagem],
        json_schema: dict[str, Any],
        nome_schema: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Uso]:
        corpo = _corpo(system, mensagens, max_tokens)
        corpo["generationConfig"] |= {
            "responseMimeType": "application/json",
            "responseJsonSchema": json_schema,
        }
        dados = await self._post("generateContent", corpo)
        try:
            return json.loads(_texto(dados)), _uso(dados)
        except json.JSONDecodeError as erro:
            raise LLMRespostaInvalida(f"gemini devolveu JSON inválido para {nome_schema}") from erro

    async def aclose(self) -> None:
        await self._cliente.aclose()

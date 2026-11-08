"""Adaptadores Ollama e Gemini contra transporte HTTP falso."""

import json

import httpx2
import pytest
from pydantic import BaseModel

from app.config import AppEnv
from app.llm.base import LLMIndisponivel, Mensagem
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider

pytestmark = pytest.mark.anyio

MSGS = [Mensagem("user", "oi"), Mensagem("assistant", "e aí"), Mensagem("user", "curto música")]
RAPIDO = {"backoff_base_s": 0, "backoff_max_s": 0, "max_retries": 1}


class Saida(BaseModel):
    eixo: str


def _cliente(handler, base_url: str) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    pedidos: list[httpx2.Request] = []

    def registrar(request: httpx2.Request) -> httpx2.Response:
        pedidos.append(request)
        return handler(request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(registrar), base_url=base_url), pedidos


# ---- Ollama ----------------------------------------------------------------------------


def _ollama(handler) -> tuple[OllamaProvider, list[httpx2.Request]]:
    cliente, pedidos = _cliente(handler, "http://ollama")
    return OllamaProvider(base_url="", http_client=cliente, modelo="qwen2.5:3b", **RAPIDO), pedidos


async def test_ollama_complete() -> None:
    resposta_json = {"message": {"content": "Olá!"}, "prompt_eval_count": 9, "eval_count": 2}
    provedor, pedidos = _ollama(lambda r: httpx2.Response(200, json=resposta_json))
    resposta = await provedor.complete("sistema", MSGS)

    assert resposta.texto == "Olá!"
    assert (resposta.uso.input_tokens, resposta.uso.output_tokens) == (9, 2)
    corpo = json.loads(pedidos[0].content)
    assert pedidos[0].url.path == "/api/chat"
    assert corpo["messages"][0] == {"role": "system", "content": "sistema"}
    assert corpo["stream"] is False


async def test_ollama_stream_ndjson() -> None:
    linhas = [
        {"message": {"content": "Oi"}, "done": False},
        {"message": {"content": " você"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2},
    ]
    corpo = "\n".join(json.dumps(linha) for linha in linhas).encode()
    provedor, _ = _ollama(lambda r: httpx2.Response(200, content=corpo))
    stream = provedor.stream("s", MSGS)
    assert "".join([p async for p in stream]) == "Oi você"
    assert stream.uso.output_tokens == 2


async def test_ollama_extract_usa_format() -> None:
    resposta_json = {"message": {"content": '{"eixo": "Informação e Comunicação"}'}}
    provedor, pedidos = _ollama(lambda r: httpx2.Response(200, json=resposta_json))
    resultado = await provedor.extract("s", MSGS, Saida)
    assert resultado.dados.eixo == "Informação e Comunicação"
    assert json.loads(pedidos[0].content)["format"]["properties"]["eixo"]["type"] == "string"


async def test_ollama_fora_do_ar_vira_mensagem_amigavel() -> None:
    def cair(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusado")

    provedor, pedidos = _ollama(cair)
    with pytest.raises(LLMIndisponivel):
        await provedor.complete("s", MSGS)
    assert len(pedidos) == 2


# ---- Gemini (só desenvolvimento) -------------------------------------------------------


def _gemini(handler) -> tuple[GeminiProvider, list[httpx2.Request]]:
    cliente, pedidos = _cliente(handler, "http://gemini")
    provedor = GeminiProvider(
        app_env=AppEnv.DEVELOPMENT,
        api_key="g",
        http_client=cliente,
        modelo="gemini-x",
        **RAPIDO,
    )
    return provedor, pedidos


def _resposta_gemini(texto: str) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": texto}]}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
    }


async def test_gemini_complete_converte_papeis() -> None:
    provedor, pedidos = _gemini(lambda r: httpx2.Response(200, json=_resposta_gemini("Olá")))
    resposta = await provedor.complete("sistema", MSGS)

    assert resposta.texto == "Olá"
    assert resposta.uso.input_tokens == 7
    corpo = json.loads(pedidos[0].content)
    assert pedidos[0].url.path == "/models/gemini-x:generateContent"
    assert [c["role"] for c in corpo["contents"]] == ["user", "model", "user"]
    assert corpo["systemInstruction"]["parts"][0]["text"] == "sistema"


async def test_gemini_stream_sse() -> None:
    corpo = "".join(
        f"data: {json.dumps(_resposta_gemini(t))}\r\n\r\n" for t in ["Oi", " gente"]
    ).encode()
    provedor, pedidos = _gemini(lambda r: httpx2.Response(200, content=corpo))
    assert "".join([p async for p in provedor.stream("s", MSGS)]) == "Oi gente"
    assert pedidos[0].url.params["alt"] == "sse"


async def test_gemini_extract_pede_json() -> None:
    provedor, pedidos = _gemini(
        lambda r: httpx2.Response(200, json=_resposta_gemini('{"eixo": "Gestão e Negócios"}'))
    )
    resultado = await provedor.extract("s", MSGS, Saida)
    assert resultado.dados.eixo == "Gestão e Negócios"
    config = json.loads(pedidos[0].content)["generationConfig"]
    assert config["responseMimeType"] == "application/json"


async def test_tempo_esgotado_nao_repete_no_mesmo_modelo() -> None:
    def travar(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("demorou", request=request)

    provedor, pedidos = _gemini(travar)
    with pytest.raises(LLMIndisponivel):
        await provedor.complete("s", MSGS)
    assert len(pedidos) == 1  # vai direto para o modelo reserva, sem esperar de novo

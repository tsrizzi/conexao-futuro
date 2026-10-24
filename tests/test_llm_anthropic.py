"""Adaptador Anthropic contra um transporte HTTP falso (nenhuma chamada real, custo zero)."""

import json

import httpx2
import pytest

from app.llm.anthropic import NOME_FERRAMENTA, AnthropicProvider
from app.llm.base import LLMErro, LLMIndisponivel, Mensagem

pytestmark = pytest.mark.anyio

MSGS = [Mensagem("user", "gosto de computador"), Mensagem("assistant", "massa!")]
USO = {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 100}


def _mensagem(content: list[dict]) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": USO,
    }


def _sse(eventos: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in eventos).encode()


def _provedor(handler) -> tuple[AnthropicProvider, list[dict]]:
    pedidos: list[dict] = []

    def registrar(request: httpx2.Request) -> httpx2.Response:
        pedidos.append(json.loads(request.content))
        return handler(request)

    cliente = httpx2.AsyncClient(transport=httpx2.MockTransport(registrar))
    provedor = AnthropicProvider(
        api_key="sk-teste",
        http_client=cliente,
        modelo="claude-haiku-4-5",
        max_retries=2,
        backoff_base_s=0,
        backoff_max_s=0,
    )
    return provedor, pedidos


async def test_complete_monta_pedido_com_cache_e_le_uso() -> None:
    provedor, pedidos = _provedor(
        lambda r: httpx2.Response(200, json=_mensagem([{"type": "text", "text": "Olá!"}]))
    )
    resposta = await provedor.complete("Você é o orientador.", MSGS, max_tokens=200)

    assert resposta.texto == "Olá!"
    assert (resposta.uso.input_tokens, resposta.uso.cache_read_tokens) == (12, 100)
    corpo = pedidos[0]
    assert corpo["model"] == "claude-haiku-4-5"
    assert corpo["max_tokens"] == 200
    assert corpo["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert corpo["messages"][1] == {"role": "assistant", "content": "massa!"}


async def test_stream_entrega_texto() -> None:
    inicio = _mensagem([]) | {"usage": {"input_tokens": 12, "output_tokens": 1}}
    eventos = [
        {"type": "message_start", "message": inicio},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        *(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
            for t in ["Oi, ", "tudo bem?"]
        ),
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    provedor, pedidos = _provedor(
        lambda r: httpx2.Response(
            200, content=_sse(eventos), headers={"content-type": "text/event-stream"}
        )
    )
    stream = provedor.stream("sistema", MSGS)
    assert "".join([p async for p in stream]) == "Oi, tudo bem?"
    assert stream.uso.output_tokens == 5
    assert pedidos[0]["stream"] is True


async def test_extract_forca_ferramenta_e_devolve_input() -> None:
    from pydantic import BaseModel

    class Saida(BaseModel):
        curso_id: str

    bloco = {"type": "tool_use", "id": "t1", "name": NOME_FERRAMENTA, "input": {"curso_id": "x"}}
    provedor, pedidos = _provedor(lambda r: httpx2.Response(200, json=_mensagem([bloco])))
    resultado = await provedor.extract("sistema", MSGS, Saida)

    assert resultado.dados.curso_id == "x"
    assert pedidos[0]["tool_choice"] == {"type": "tool", "name": NOME_FERRAMENTA}
    assert pedidos[0]["tools"][0]["input_schema"]["properties"]["curso_id"]["type"] == "string"


@pytest.mark.parametrize("status", [429, 529, 500])
async def test_sobrecarga_e_repetida_e_depois_desiste(status: int) -> None:
    erro = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    provedor, pedidos = _provedor(lambda r: httpx2.Response(status, json=erro))
    with pytest.raises(LLMIndisponivel):
        await provedor.complete("s", MSGS)
    assert len(pedidos) == 3


async def test_erro_do_cliente_nao_e_repetido() -> None:
    erro = {"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
    provedor, pedidos = _provedor(lambda r: httpx2.Response(400, json=erro))
    with pytest.raises(LLMErro) as exc:
        await provedor.complete("s", MSGS)
    assert not isinstance(exc.value, LLMIndisponivel)
    assert len(pedidos) == 1

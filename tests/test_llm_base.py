"""Comportamento comum da camada LLM, exercitado pelo FakeProvider."""

import asyncio

import pytest
from pydantic import BaseModel

from app.llm.base import (
    MENSAGEM_SOBRECARGA,
    LLMIndisponivel,
    LLMRespostaInvalida,
    Mensagem,
)
from app.llm.fake import RESPOSTA_PADRAO, FakeProvider

pytestmark = pytest.mark.anyio

MSGS = [Mensagem("user", "oi")]


class Perfil(BaseModel):
    interesses: list[str]
    cobertura: float


async def test_complete_devolve_texto_e_uso() -> None:
    llm = FakeProvider(respostas=["Bora conversar!"])
    resposta = await llm.complete("sistema", MSGS)
    assert resposta.texto == "Bora conversar!"
    assert resposta.uso.output_tokens == 2
    assert llm.chamadas[0].system == "sistema"


async def test_stream_entrega_pedacos_e_uso_no_final() -> None:
    llm = FakeProvider()
    stream = llm.stream("sistema", MSGS)
    pedacos = [p async for p in stream]
    assert "".join(pedacos) == RESPOSTA_PADRAO
    assert len(pedacos) > 1
    assert stream.uso.output_tokens > 0


async def test_extract_valida_com_pydantic() -> None:
    llm = FakeProvider(estruturados=[{"interesses": ["bike"], "cobertura": 0.3}])
    resultado = await llm.extract("sistema", MSGS, Perfil)
    assert resultado.dados == Perfil(interesses=["bike"], cobertura=0.3)


async def test_extract_rejeita_saida_fora_do_esquema() -> None:
    llm = FakeProvider(estruturados=[{"interesses": "não é lista"}])
    with pytest.raises(LLMRespostaInvalida):
        await llm.extract("sistema", MSGS, Perfil)


async def test_retry_recupera_de_falha_transitoria() -> None:
    llm = FakeProvider(respostas=["ok"], falhas_transitorias=2, max_retries=3)
    resposta = await llm.complete("s", MSGS)
    assert resposta.texto == "ok"
    assert len(llm.chamadas) == 3


async def test_desiste_com_mensagem_amigavel() -> None:
    llm = FakeProvider(falhas_transitorias=10, max_retries=2)
    with pytest.raises(LLMIndisponivel) as erro:
        await llm.complete("s", MSGS)
    assert str(erro.value) == MENSAGEM_SOBRECARGA
    assert len(llm.chamadas) == 3  # 1 tentativa + 2 repetições


async def test_stream_tambem_repete_antes_do_primeiro_pedaco() -> None:
    llm = FakeProvider(respostas=["deu certo"], falhas_transitorias=1)
    assert "".join([p async for p in llm.stream("s", MSGS)]) == "deu certo"


async def test_semaforo_limita_concorrencia() -> None:
    llm = FakeProvider(max_concurrency=2)
    ativos = 0
    pico = 0
    original = llm._complete

    async def lento(*args, **kwargs):
        nonlocal ativos, pico
        ativos += 1
        pico = max(pico, ativos)
        await asyncio.sleep(0.01)
        ativos -= 1
        return await original(*args, **kwargs)

    llm._complete = lento  # type: ignore[method-assign]
    await asyncio.gather(*(llm.complete("s", MSGS) for _ in range(6)))
    assert pico == 2


# ---- Modelo reserva --------------------------------------------------------------------


async def test_reserva_assume_quando_o_principal_segue_sobrecarregado() -> None:
    from app.llm.reserva import ProvedorComReserva

    principal = FakeProvider(falhas_transitorias=99, max_retries=1)
    reserva = FakeProvider(
        respostas=["da reserva", "stream da reserva"],
        estruturados=[{"interesses": [], "cobertura": 0}],
    )
    llm = ProvedorComReserva(principal, reserva)

    assert (await llm.complete("s", MSGS)).texto == "da reserva"
    stream = llm.stream("s", MSGS)
    assert "".join([p async for p in stream]) == "stream da reserva"
    assert stream.uso.output_tokens > 0
    assert (await llm.extract("s", MSGS, Perfil)).dados.interesses == []


async def test_reserva_nao_e_usada_quando_o_principal_responde() -> None:
    from app.llm.reserva import ProvedorComReserva

    reserva = FakeProvider()
    llm = ProvedorComReserva(FakeProvider(respostas=["principal"]), reserva)
    assert (await llm.complete("s", MSGS)).texto == "principal"
    assert reserva.chamadas == []


async def test_reserva_tambem_indisponivel_vira_mensagem_amigavel() -> None:
    from app.llm.reserva import ProvedorComReserva

    llm = ProvedorComReserva(
        FakeProvider(falhas_transitorias=99, max_retries=0),
        FakeProvider(falhas_transitorias=99, max_retries=0),
    )
    with pytest.raises(LLMIndisponivel):
        await llm.complete("s", MSGS)


def test_factory_monta_reserva_so_quando_configurada() -> None:
    from app.llm.factory import criar_provedor
    from app.llm.reserva import ProvedorComReserva
    from tests.conftest import make_settings

    assert not isinstance(criar_provedor(make_settings(llm_provider="fake")), ProvedorComReserva)
    llm = criar_provedor(make_settings(llm_provider="fake", llm_model_reserva="outro"))
    assert isinstance(llm, ProvedorComReserva)
    assert (llm.principal.modelo, llm.reserva.modelo) == ("claude-haiku-4-5", "outro")

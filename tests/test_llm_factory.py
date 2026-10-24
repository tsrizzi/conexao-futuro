import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import AppEnv, GeminiBloqueadoEmProducao, LLMProviderName, Settings
from app.llm.anthropic import AnthropicProvider
from app.llm.factory import criar_provedor
from app.llm.fake import FakeProvider
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("env", "classe"),
    [
        ({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-teste"}, AnthropicProvider),
        ({"LLM_PROVIDER": "ollama", "LLM_MODEL": "qwen2.5:3b"}, OllamaProvider),
        ({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "g-teste"}, GeminiProvider),
        ({"LLM_PROVIDER": "fake"}, FakeProvider),
    ],
)
def test_trocar_provedor_muda_so_o_ambiente(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], classe: type
) -> None:
    for chave, valor in env.items():
        monkeypatch.setenv(chave, valor)
    provedor = criar_provedor(make_settings())
    assert isinstance(provedor, classe)


def test_factory_repassa_configuracao_comum() -> None:
    s = make_settings(llm_provider="fake", llm_model="m", llm_max_tokens=77, llm_max_retries=1)
    provedor = criar_provedor(s)
    assert (provedor.modelo, provedor.max_tokens, provedor.max_retries) == ("m", 77, 1)


def test_factory_bloqueia_gemini_em_producao_mesmo_sem_validacao() -> None:
    # model_construct pula os validadores: a factory precisa barrar sozinha.
    s = Settings.model_construct(
        app_env=AppEnv.PRODUCTION, llm_provider=LLMProviderName.GEMINI, llm_model="gemini-x"
    )
    with pytest.raises(GeminiBloqueadoEmProducao):
        criar_provedor(s)


def test_adaptador_gemini_se_recusa_em_producao() -> None:
    with pytest.raises(GeminiBloqueadoEmProducao):
        GeminiProvider(app_env=AppEnv.PRODUCTION, api_key="x", modelo="gemini-x")


def test_openai_compat_ainda_nao_implementado() -> None:
    with pytest.raises(NotImplementedError):
        criar_provedor(make_settings(llm_provider="openai_compat"))


def test_app_nao_inicia_com_gemini_em_producao() -> None:
    """Simula `uvicorn app.main:app` com APP_ENV=production e LLM_PROVIDER=gemini."""
    env = {**os.environ, "APP_ENV": "production", "LLM_PROVIDER": "gemini"}
    resultado = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resultado.returncode != 0
    assert "proibido com APP_ENV=production" in resultado.stderr

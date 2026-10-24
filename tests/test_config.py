import pytest
from pydantic import ValidationError

from app.config import AppEnv, LLMProviderName
from tests.conftest import make_settings


def test_padroes_sao_os_mais_baratos() -> None:
    s = make_settings(llm_provider="anthropic")
    assert s.app_env == AppEnv.DEVELOPMENT
    assert s.llm_model == "claude-haiku-4-5"
    assert s.log_conversas is False


def test_le_variaveis_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "4")
    s = make_settings()
    assert s.llm_provider == LLMProviderName.OLLAMA
    assert s.llm_model == "qwen2.5:3b"
    assert s.llm_max_concurrency == 4


def test_gemini_bloqueado_em_producao() -> None:
    with pytest.raises(ValidationError, match="proibido com APP_ENV=production"):
        make_settings(app_env="production", llm_provider="gemini")


def test_gemini_permitido_em_desenvolvimento() -> None:
    s = make_settings(app_env="development", llm_provider="gemini")
    assert s.llm_provider == LLMProviderName.GEMINI


def test_log_conversas_proibido_em_producao() -> None:
    with pytest.raises(ValidationError, match="LOG_CONVERSAS"):
        make_settings(app_env="production", llm_provider="anthropic", log_conversas=True)


def test_segredos_nao_aparecem_no_repr() -> None:
    s = make_settings(anthropic_api_key="sk-ant-secreta")
    assert "sk-ant-secreta" not in repr(s)

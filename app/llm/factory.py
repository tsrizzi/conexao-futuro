"""Cria o provedor de LLM a partir da configuração. Trocar de provedor muda só o .env."""

from typing import Any

from app.config import (
    MENSAGEM_GEMINI_BLOQUEADO,
    AppEnv,
    GeminiBloqueadoEmProducao,
    LLMProviderName,
    Settings,
)
from app.llm.base import LLMProvider


def _segredo(valor: Any) -> str | None:
    return valor.get_secret_value() if valor is not None else None


def criar_provedor(settings: Settings) -> LLMProvider:
    principal = _criar(settings, settings.llm_model)
    if not settings.llm_model_reserva or settings.llm_model_reserva == settings.llm_model:
        return principal
    from app.llm.reserva import ProvedorComReserva

    return ProvedorComReserva(principal, _criar(settings, settings.llm_model_reserva))


def _criar(settings: Settings, modelo: str) -> LLMProvider:
    comuns: dict[str, Any] = {
        "modelo": modelo,
        "max_tokens": settings.llm_max_tokens,
        "max_concurrency": settings.llm_max_concurrency,
        "max_retries": settings.llm_max_retries,
    }

    match settings.llm_provider:
        case LLMProviderName.ANTHROPIC:
            from app.llm.anthropic import AnthropicProvider

            return AnthropicProvider(
                api_key=_segredo(settings.anthropic_api_key),
                timeout_s=settings.llm_timeout_s,
                **comuns,
            )
        case LLMProviderName.OLLAMA:
            from app.llm.ollama import OllamaProvider

            return OllamaProvider(base_url=settings.ollama_base_url, **comuns)
        case LLMProviderName.GEMINI:
            # Segunda trava (a primeira está em Settings), caso a validação seja contornada.
            if settings.app_env == AppEnv.PRODUCTION:
                raise GeminiBloqueadoEmProducao(MENSAGEM_GEMINI_BLOQUEADO)
            from app.llm.gemini import GeminiProvider

            return GeminiProvider(
                app_env=settings.app_env,
                api_key=_segredo(settings.gemini_api_key),
                timeout_s=settings.llm_timeout_s,
                **comuns,
            )
        case LLMProviderName.FAKE:
            from app.llm.fake import FakeProvider

            return FakeProvider(**comuns)
        case LLMProviderName.OPENAI_COMPAT:
            raise NotImplementedError(
                "O adaptador 'openai_compat' ainda não foi implementado (fora da Fase 0)."
            )
    raise ValueError(f"Provedor de LLM desconhecido: {settings.llm_provider}")

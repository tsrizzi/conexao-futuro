"""Configuração da aplicação, lida do ambiente / .env (seção 11 da especificação)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI_COMPAT = "openai_compat"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    FAKE = "fake"


class GeminiBloqueadoEmProducao(ValueError):
    """Os termos da API do Gemini proíbem apps acessados por menores de 18 anos."""


MENSAGEM_GEMINI_BLOQUEADO = (
    "O provedor 'gemini' é proibido com APP_ENV=production: os termos da API do Gemini "
    "vedam apps acessados por menores de 18 anos. Use LLM_PROVIDER=anthropic."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.DEVELOPMENT

    llm_provider: LLMProviderName = LLMProviderName.ANTHROPIC
    llm_model: str = "claude-haiku-4-5"
    llm_max_tokens: int = 1024
    llm_max_concurrency: int = 8
    llm_max_retries: int = 3
    llm_timeout_s: float = 60.0

    anthropic_api_key: SecretStr | None = None
    openai_compat_base_url: str | None = None
    openai_compat_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"
    gemini_api_key: SecretStr | None = None

    database_url: str = "sqlite:///./dev.db"
    secret_key: SecretStr | None = None
    professor_password_hash: SecretStr | None = None
    retencao_dias: int = 60
    cobertura_minima: float = 0.7
    log_conversas: bool = False

    @model_validator(mode="after")
    def _bloqueia_gemini_em_producao(self) -> "Settings":
        if self.app_env == AppEnv.PRODUCTION and self.llm_provider == LLMProviderName.GEMINI:
            raise GeminiBloqueadoEmProducao(MENSAGEM_GEMINI_BLOQUEADO)
        if self.app_env == AppEnv.PRODUCTION and self.log_conversas:
            raise ValueError(
                "LOG_CONVERSAS=true só é permitido em testes com adultos, nunca em production."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

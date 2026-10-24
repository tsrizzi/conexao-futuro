"""Utilitários compartilhados pelos adaptadores que falam HTTP direto (ollama, gemini)."""

import httpx2

from app.llm.base import LLMErro, LLMSobrecarregado

STATUS_TRANSITORIOS = {408, 429, 500, 502, 503, 504, 529}


def verificar_status(resposta: httpx2.Response, provedor: str) -> None:
    if resposta.status_code in STATUS_TRANSITORIOS:
        raise LLMSobrecarregado(f"{provedor}: HTTP {resposta.status_code}")
    if resposta.is_error:
        raise LLMErro(f"{provedor}: HTTP {resposta.status_code}")


def erro_de_rede(erro: httpx2.TransportError, provedor: str) -> LLMSobrecarregado:
    return LLMSobrecarregado(f"{provedor}: falha de rede ({type(erro).__name__})")

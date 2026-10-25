"""Monta o agente completo a partir da configuração (carrega tudo uma única vez)."""

from app.agent.ofertas import Ofertas
from app.agent.orchestrator import Orquestrador
from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.factory import criar_provedor
from app.rag.catalog import Catalogo
from app.rag.retriever import criar_retriever


def criar_orquestrador(settings: Settings, llm: LLMProvider | None = None) -> Orquestrador:
    catalogo = Catalogo.carregar()
    return Orquestrador(
        llm or criar_provedor(settings),
        catalogo,
        criar_retriever(settings, catalogo),
        Ofertas.carregar({c.id for c in catalogo.cursos}),
        cobertura_minima=settings.cobertura_minima,
        eixos_fora_da_busca=settings.rag_eixos_fora_da_busca,
    )

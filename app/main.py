"""Ponto de entrada da aplicação FastAPI."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app.agent.orchestrator import Orquestrador
from app.config import AppEnv, Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.factory import criar_provedor
from app.routes import acesso_api, chat_api, health, paginas, professor
from app.safety.limites import LimitadorTaxa

log = logging.getLogger(__name__)
ESTATICOS = Path(__file__).resolve().parent / "static"
INTERVALO_LIMPEZA_S = 24 * 3600

# CSP estrita: nada inline; só o próprio site, o CDN do Bootstrap e as fontes do Google.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


def _chave(settings: Settings) -> bytes:
    if settings.secret_key:
        return settings.secret_key.get_secret_value().encode()
    # Só em desenvolvimento (em production a configuração exige SECRET_KEY): conversas
    # abertas deixam de valer quando o servidor reinicia.
    log.warning(
        "SECRET_KEY vazia: usando uma chave temporária. Conversas abertas e códigos de "
        "acesso deixam de valer quando o servidor reinicia. Defina SECRET_KEY no .env."
    )
    return secrets.token_bytes(32)


def _limpar(sessoes: sessionmaker[Session], retencao_dias: int) -> int:
    from app.db.repositorio import limpar_expirados

    with sessoes.begin() as db:
        return limpar_expirados(db, retencao_dias)


async def _limpeza_diaria(sessoes: sessionmaker[Session], retencao_dias: int) -> None:
    """Apaga o que passou do prazo (seção 4.3). Roda ao iniciar e depois a cada 24 h.

    O Render gratuito não tem cron; como o app dorme e acorda, rodar ao iniciar já cobre.
    """
    while True:
        try:
            apagados = await asyncio.to_thread(_limpar, sessoes, retencao_dias)
            if apagados:
                log.info("Limpeza por retenção: %d registro(s) apagado(s)", apagados)
        except Exception:
            log.exception("Falha na limpeza por retenção")
        await asyncio.sleep(INTERVALO_LIMPEZA_S)


def create_app(
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
    orquestrador: Orquestrador | None = None,
    sessoes: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    # Cria o provedor já aqui: configuração inválida (ex.: gemini em production) impede o start.
    provedor = llm or criar_provedor(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if orquestrador is None:
            from app.agent.factory import criar_orquestrador

            # Catálogo, índice e modelo de embeddings carregados UMA vez (seção 10).
            app.state.orquestrador = await asyncio.to_thread(criar_orquestrador, settings, provedor)
        if sessoes is None:
            from app.db.session import criar_engine, fabrica_de_sessoes, migrar

            await asyncio.to_thread(migrar, settings.database_url)
            app.state.sessoes = fabrica_de_sessoes(criar_engine(settings.database_url))
        limpeza = asyncio.create_task(_limpeza_diaria(app.state.sessoes, settings.retencao_dias))
        _publicar_catalogo(app)
        yield
        limpeza.cancel()
        await provedor.aclose()

    app = FastAPI(title="Conexão Futuro", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.llm = provedor
    app.state.orquestrador = orquestrador
    app.state.chave = _chave(settings)
    app.state.limite_mensagens = LimitadorTaxa(maximo=30, janela_s=600)  # por sessão
    app.state.limite_inicio = LimitadorTaxa(maximo=30, janela_s=600)  # por IP
    app.state.limite_codigo = LimitadorTaxa(maximo=10, janela_s=600)  # força bruta, por IP
    app.state.limite_criacao = LimitadorTaxa(maximo=20, janela_s=600)  # novos códigos, por IP
    app.state.limite_login = LimitadorTaxa(maximo=5, janela_s=600)  # painel, por IP
    app.state.limite_turma = LimitadorTaxa(maximo=200, janela_s=600)  # consulta de turma, por IP
    app.state.sessoes = sessoes
    app.state.edicao_cnct = ""
    app.state.total_cursos = 0

    producao = settings.app_env == AppEnv.PRODUCTION

    @app.middleware("http")
    async def cabecalhos_de_seguranca(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resposta = await call_next(request)
        resposta.headers["Content-Security-Policy"] = CSP
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if producao:
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resposta

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    app.include_router(health.router)
    app.include_router(paginas.router)
    app.include_router(chat_api.router)
    app.include_router(acesso_api.router)
    app.include_router(professor.router)
    return app


def _publicar_catalogo(app: FastAPI) -> None:
    """Edição e tamanho do CNCT para o rodapé e a página inicial."""
    orq: Orquestrador = app.state.orquestrador
    app.state.edicao_cnct = orq.catalogo.edicao
    app.state.total_cursos = orq.total_cursos_sugeriveis


app = create_app()

"""Conexão com o banco: SQLite no desenvolvimento, Postgres em produção."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

RAIZ = Path(__file__).resolve().parents[2]


def url_normalizada(url: str) -> str:
    """Neon e outros provedores entregam 'postgres://'; o SQLAlchemy quer o driver explícito."""
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo) :]
    return url


def criar_engine(url: str) -> Engine:
    url = url_normalizada(url)
    if url.startswith("sqlite"):
        memoria = url in ("sqlite://", "sqlite:///:memory:")
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if memoria else None,
        )

        @event.listens_for(engine, "connect")
        def _chaves_estrangeiras(conexao, _registro):  # type: ignore[no-untyped-def]
            # SQLite só respeita ON DELETE CASCADE com isto ligado.
            conexao.execute("PRAGMA foreign_keys=ON")

        return engine
    # Postgres gratuito: poucas conexões e que caem; pre_ping evita erro após o sono.
    return create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=300)


def fabrica_de_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def migrar(url: str) -> None:
    """Aplica as migrações do Alembic (idempotente). Roda na inicialização do app."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "app" / "db" / "migrations"))
    # O configparser trata "%" como interpolação (senhas do Postgres podem ter "%").
    config.set_main_option("sqlalchemy.url", url_normalizada(url).replace("%", "%%"))
    command.upgrade(config, "head")

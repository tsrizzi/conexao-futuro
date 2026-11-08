"""Ambiente do Alembic: usa a DATABASE_URL da configuração e os modelos do app."""

from alembic import context
from sqlalchemy import pool

from app.config import get_settings
from app.db.models import Base
from app.db.session import criar_engine, url_normalizada

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or url_normalizada(get_settings().database_url)


def rodar_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def rodar_online() -> None:
    engine = criar_engine(_url())
    with engine.connect() as conexao:
        context.configure(
            connection=conexao,
            target_metadata=target_metadata,
            render_as_batch=conexao.dialect.name == "sqlite",  # ALTER TABLE no SQLite
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


_ = pool  # importado pelo template padrão do Alembic
if context.is_offline_mode():
    rodar_offline()
else:
    rodar_online()

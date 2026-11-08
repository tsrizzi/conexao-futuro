import os

# Os testes nunca chamam API real: o provedor padrão é o fake, antes de importar o app.
os.environ["APP_ENV"] = "development"
os.environ["LLM_PROVIDER"] = "fake"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agent.ofertas import Ofertas  # noqa: E402
from app.agent.orchestrator import Orquestrador  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm.fake import FakeProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rag.catalog import Catalogo  # noqa: E402
from tests.agente_fake import BuscadorFixo  # noqa: E402


def make_settings(**valores: object) -> Settings:
    """Settings isolado do .env local."""
    return Settings(_env_file=None, **valores)  # type: ignore[call-arg]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture(scope="session")
def catalogo_real() -> Catalogo:
    return Catalogo.carregar()


def criar_cliente(llm: FakeProvider, catalogo: Catalogo, **settings: object) -> TestClient:
    """App completo com LLM falso e busca fixa (sem carregar o modelo de embeddings)."""
    orquestrador = Orquestrador(llm, catalogo, BuscadorFixo(catalogo), Ofertas([]))
    config = make_settings(llm_provider="fake", secret_key="x" * 32, **settings)
    return TestClient(create_app(config, llm=llm, orquestrador=orquestrador))


@pytest.fixture
def client(fake_llm: FakeProvider, catalogo_real: Catalogo) -> TestClient:
    with criar_cliente(fake_llm, catalogo_real) as c:
        yield c

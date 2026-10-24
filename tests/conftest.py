import os

# Os testes nunca chamam API real: o provedor padrão é o fake, antes de importar o app.
os.environ["APP_ENV"] = "development"
os.environ["LLM_PROVIDER"] = "fake"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.llm.fake import FakeProvider  # noqa: E402
from app.main import create_app  # noqa: E402


def make_settings(**valores: object) -> Settings:
    """Settings isolado do .env local."""
    return Settings(_env_file=None, **valores)  # type: ignore[call-arg]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(fake_llm: FakeProvider) -> TestClient:
    app = create_app(make_settings(llm_provider="fake"), llm=fake_llm)
    with TestClient(app) as c:
        yield c

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "app_env": "development", "llm_provider": "fake"}


def test_health_nao_chama_llm(client: TestClient, fake_llm) -> None:
    client.get("/health")
    assert fake_llm.chamadas == []

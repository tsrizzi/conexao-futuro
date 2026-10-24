# Conexão Futuro

Agente conversacional que ajuda estudantes do 2º ano do Ensino Médio a descobrir cursos técnicos
do Catálogo Nacional de Cursos Técnicos (CNCT). Especificação completa em
[`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md).

## Rodar localmente

```bash
uv sync                          # instala dependências (cria .venv)
cp .env.example .env             # preencha ANTHROPIC_API_KEY (ou use LLM_PROVIDER=fake/ollama)
uv run uvicorn app.main:app --reload
# http://127.0.0.1:8000/health
```

Sem chave de API, dá para desenvolver com custo zero usando `LLM_PROVIDER=fake` (respostas de
teste) ou `LLM_PROVIDER=ollama` (modelo local, ex.: `LLM_MODEL=qwen2.5:3b`).

## Testes e lint

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy app
```

Os testes nunca chamam APIs reais.

## Provedores de LLM

| `LLM_PROVIDER` | Uso |
|---|---|
| `anthropic` | Padrão em produção (`LLM_MODEL=claude-haiku-4-5`, o mais barato). |
| `ollama` | Modelo local, sem custo de API. |
| `gemini` | Só desenvolvimento/testes com adultos. **O app não sobe com `APP_ENV=production`.** |
| `fake` | Testes. |

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

## Catálogo (CNCT) e índice de busca

```bash
# 1. PDF do CNCT -> data/cnct.json + data/cnct_relatorio.md (revise a amostra do relatório!)
uv run --group ingest python scripts/ingest_cnct.py data/raw/<arquivo>.pdf
#    alternativa: planilha montada à mão (colunas = campos do curso; listas separadas por ';')
uv run python scripts/ingest_cnct.py data/raw/<planilha>.csv --edicao "5ª edição (2026)"

# 2. cnct.json -> data/cnct_index.npz (baixa o modelo de embeddings na 1ª vez, ~240 MB)
uv run python scripts/build_index.py
```

Trocar de edição do CNCT = rodar os dois comandos de novo; o app recusa índice desatualizado.
Palavras do dia a dia que o catálogo não usa ("bolo" → "confeitaria") ficam em
`data/expansao_consulta.yaml`, editável.

## Conversar com o agente pelo terminal

```bash
uv run python scripts/conversar.py            # usa o provedor do .env
uv run python scripts/conversar.py --roteiro  # conversa automática com falas prontas
```

Comandos: `/perfil`, `/custo`, `/sair`. Só para testes com adultos. Nada é gravado.

Prompts e mensagens fixas do agente ficam em `app/agent/prompts/*.md` (editáveis, sem mexer
no código). Os arquivos `msg_*.md` são textos mostrados como estão (boas-vindas, acolhimento
com o CVV 188 etc.); os demais são instruções para o LLM.

## Testes e lint

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy app
```

Os testes nunca chamam APIs reais. Os marcados com `modelo` usam o modelo de embeddings
local e são pulados se ele ainda não foi baixado (`uv run pytest -m "not modelo"` pula sempre).

## Provedores de LLM

| `LLM_PROVIDER` | Uso |
|---|---|
| `anthropic` | Padrão em produção (`LLM_MODEL=claude-haiku-4-5`, o mais barato). |
| `ollama` | Modelo local, sem custo de API. |
| `gemini` | Só desenvolvimento/testes com adultos. **O app não sobe com `APP_ENV=production`.** |
| `fake` | Testes. |

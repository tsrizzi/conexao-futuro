# Conexão Futuro — instruções para o Claude Code

Agente conversacional web que ajuda estudantes do 2º ano do Ensino Médio (15 a 17 anos, rede pública de Guaramirim/SC) a descobrir cursos técnicos do Catálogo Nacional de Cursos Técnicos (CNCT), usando RAG.

**Leia `docs/ESPECIFICACAO.md` antes de qualquer tarefa.** Ela é a fonte da verdade. Em caso de conflito, a especificação vence e você avisa.

## Como trabalhar
- Desenvolva **uma fase por vez**, na ordem da seção 12 da especificação. Ao terminar uma fase: rode os testes, confira os critérios de aceite, faça commit e **pare para eu revisar** antes da próxima.
- Antes de codar uma fase, mostre um plano curto (arquivos que vai criar ou alterar).
- Pergunte quando a especificação for ambígua. Não invente requisito.
- Tudo voltado ao usuário (textos de interface, mensagens, prompts do agente) em **português do Brasil**. Código, nomes de variáveis e commits podem ser em português ou inglês, desde que sejam consistentes.

## Stack (não trocar sem perguntar)
Python 3.12 · FastAPI · Jinja2 · Bootstrap 5.3 (CDN) + JS puro · SQLAlchemy 2 + Alembic · Pydantic v2 · fastembed + rank-bm25 + numpy · pytest · ruff · Deploy: Google Cloud Run (Dockerfile) + Postgres (Neon).

## Comandos
```bash
uv sync                                  # ou: pip install -e ".[dev]"
uvicorn app.main:app --reload            # rodar local
pytest -q                                # testes
ruff check . && ruff format .            # lint
python scripts/ingest_cnct.py data/raw/<arquivo>.pdf
python scripts/build_index.py
python scripts/eval_recomendacoes.py
```

## Regras inegociáveis
1. **Nunca usar o Gemini em produção.** Os termos da API proíbem apps acessados por menores de 18. O adaptador `gemini` precisa falhar ao iniciar com `APP_ENV=production`.
2. **Nenhum dado pessoal.** Não criar campos de nome, CPF, e-mail, telefone, gênero ou idade. Mascarar dados pessoais antes de enviar ao LLM. Não gravar o texto das conversas.
3. **Nunca recomendar curso que não esteja em `data/cnct.json`.** Validar todo `curso_id` devolvido pelo LLM. Instituições e ofertas vêm só de `data/ofertas_regiao.json`.
4. **Primeira mensagem e rodapé sempre avisam que é uma IA.**
5. **Fluxo de acolhimento** quando houver sinal de sofrimento emocional (CVV 188), conforme a seção 8.
6. Prompts do agente ficam em `app/agent/prompts/*.md`, nunca como strings no código.
7. Segredos só via `.env`. Nunca commitar `.env` nem chaves.
8. Toda chamada ao LLM passa pela interface `app/llm/base.py`. Nos testes, usar o provedor `fake`, sem chamar API real.
9. Painel do professor: só dados agregados, nunca individuais.

## Dados de referência
- `data/soutec_itens.yaml`: os 72 itens do teste do SouTec, classificados nas dimensões R/I/A/S/E/C. Referência para o formulário, os exemplos do agente e os testes. Não reproduzir o teste inteiro na interface.
- `data/cnct.json`: gerado por script a partir do PDF do CNCT em `data/raw/`. A 5ª edição sai em dez/2026, então o catálogo precisa ser trocável sem mudar código.

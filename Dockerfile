# Imagem de produção (Cloud Run). Duas etapas: a primeira instala as dependências e baixa
# o modelo de embeddings; a final leva só o necessário, sem o uv e sem cache de build.

# ---- Etapa 1: dependências e modelo -----------------------------------------------------
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependências antes do código: a camada só refaz quando o uv.lock muda.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo (~235 MB) numa camada própria, que só refaz se o código de embeddings mudar.
COPY app/__init__.py app/__init__.py
COPY app/rag/__init__.py app/rag/catalog.py app/rag/embeddings.py app/rag/
COPY scripts/baixar_modelo.py scripts/baixar_modelo.py
ENV EMBEDDINGS_CACHE_DIR=/app/.cache/fastembed
RUN .venv/bin/python scripts/baixar_modelo.py

COPY . .
RUN uv sync --frozen --no-dev

# ---- Etapa 2: imagem final --------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    EMBEDDINGS_CACHE_DIR=/app/.cache/fastembed \
    HF_HUB_OFFLINE=1 \
    PROXY_SALTOS=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
USER app

# O Cloud Run informa a porta em $PORT. Proxy headers ficam desligados no uvicorn: o app
# lê o IP real sozinho (app/web/rede.py), sem confiar em cabeçalhos inventados.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-proxy-headers --timeout-graceful-shutdown 20"]

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PORT=8080

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY scripts ./scripts

EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8080"]

# Web console image for harness2 (see docs/SETUP.md#web-console-and-container).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Node 20 is needed by real benchmark setup (Bun, Codex); the console itself is Python only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN uv venv .venv \
    && uv pip install --no-cache -e '.[render,webui]' --python .venv/bin/python

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8788
CMD ["harness2-webui", "--host", "0.0.0.0", "--port", "8788"]

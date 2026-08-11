FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

LABEL org.opencontainers.image.title="Dander Runtime" \
      org.opencontainers.image.description="GCP-native ingest, transform, and catalog runtime" \
      org.opencontainers.image.source="https://github.com/harrisonoconnorhover/dander" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.0

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY connectors ./connectors
COPY graphs ./graphs
COPY models ./models
COPY examples ./examples
COPY infra ./infra
RUN uv sync --frozen --no-dev --no-editable

COPY dander.yaml ./dander.yaml

USER 65532:65532

ENTRYPOINT ["/app/.venv/bin/dander"]
CMD ["runtime", "greenhouse_jobs", "--guarded-free-tier"]

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG DANDER_VERSION=__DANDER_DISTRIBUTION_VERSION__
ARG DANDER_BUILD_REVISION=unknown
ARG DANDER_BUILD_CREATED=1970-01-01T00:00:00Z

LABEL org.opencontainers.image.title="Dander" \
    org.opencontainers.image.description="Self-hosted data platform runtime" \
    org.opencontainers.image.source="https://github.com/harrisonoconnorhover/dander" \
    org.opencontainers.image.documentation="https://github.com/harrisonoconnorhover/dander/blob/main/docs/getting-started.md" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.version="${DANDER_VERSION}" \
    org.opencontainers.image.revision="${DANDER_BUILD_REVISION}" \
    org.opencontainers.image.created="${DANDER_BUILD_CREATED}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DANDER_BUILD_REVISION=${DANDER_BUILD_REVISION} \
    DANDER_BUILD_CREATED=${DANDER_BUILD_CREATED}

WORKDIR /app

RUN pip install --no-cache-dir "dander-platform==${DANDER_VERSION}"

COPY --chown=65532:65532 dander.yaml ./dander.yaml
COPY --chown=65532:65532 dander.platforms.yaml ./dander.platforms.yaml

RUN dander plugins install --config dander.yaml

COPY --chown=65532:65532 connectors ./connectors
COPY --chown=65532:65532 graphs ./graphs
COPY --chown=65532:65532 models ./models

USER 65532:65532

ENTRYPOINT ["dander"]
CMD ["run", "greenhouse_jobs"]

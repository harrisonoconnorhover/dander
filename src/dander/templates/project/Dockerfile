FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG DANDER_VERSION=__DANDER_DISTRIBUTION_VERSION__

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "dander-platform==${DANDER_VERSION}"

COPY --chown=65532:65532 dander.yaml ./dander.yaml

RUN dander plugins install --config dander.yaml

COPY --chown=65532:65532 connectors ./connectors
COPY --chown=65532:65532 graphs ./graphs
COPY --chown=65532:65532 models ./models

USER 65532:65532

ENTRYPOINT ["dander"]
CMD ["run", "greenhouse_jobs"]

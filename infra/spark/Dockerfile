FROM debian:12-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG DANDER_BUILD_REVISION=unknown
ARG DANDER_BUILD_CREATED=1970-01-01T00:00:00Z

LABEL org.opencontainers.image.title="Dander Managed Spark runtime" \
    org.opencontainers.image.description="Fixed-plan Dander Spark driver runtime" \
    org.opencontainers.image.source="https://github.com/harrisonoconnorhover/dander" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.revision="${DANDER_BUILD_REVISION}" \
    org.opencontainers.image.created="${DANDER_BUILD_CREATED}"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libjemalloc2 \
        procps \
        tini \
    && groupadd --gid 1099 spark \
    && useradd --uid 1099 --gid 1099 --home-dir /home/spark --create-home spark \
    && mkdir --parents /opt/dander \
    && chown --recursive 1099:1099 /opt/dander \
    && rm -rf /var/lib/apt/lists/*

ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2

COPY --chown=1099:1099 scripts/spark_driver.py /opt/dander/spark_driver.py

USER 1099:1099

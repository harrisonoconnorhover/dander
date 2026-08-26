# syntax=docker/dockerfile:1

# OCI Functions defaults to GENERIC_X86. Pin both Python 3.12 FDK images to their
# linux/amd64 manifests so the controller build cannot silently change architecture.
FROM fnproject/python:3.12-dev@sha256:12fcb8be166981acdcf52bbad73ea4833c73634a31b7c63a9c13912e499ba201 AS build

ARG DANDER_WHEEL
WORKDIR /function
COPY infra/oci/controller/requirements.txt /function/requirements.txt
RUN pip3 install --target /python --no-cache-dir -r /function/requirements.txt
COPY ${DANDER_WHEEL} /tmp/dander_platform-0-py3-none-any.whl
RUN pip3 install --target /python --no-cache-dir --no-deps /tmp/dander_platform-0-py3-none-any.whl \
    && rm -rf /root/.cache /tmp/*

FROM fnproject/python:3.12@sha256:fa298b94e95a54e6d10c54aa5691fefdb01c76c5c38b3da352a2f9dc0fbdf739

RUN microdnf update -y curl libcurl python3.12 python3.12-libs sqlite-libs \
    && microdnf clean all

WORKDIR /function
COPY --from=build /python /python
COPY infra/oci/controller/func.py /function/func.py
RUN chmod -R a+rX /function /python
ENV PYTHONPATH=/function:/python
USER fn
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]

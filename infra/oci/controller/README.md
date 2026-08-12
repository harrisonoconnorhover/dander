# OCI lifecycle controller image

This image contains only the OCI Functions shim, the exact Dander wheel under review, the OCI SDK,
and the Python FDK. It does not contain repository source, project data, credentials, or Terraform
artifacts. The Dockerfile is pinned to the Python 3.12 `linux/amd64` FDK manifests because the
Terraform application explicitly selects `GENERIC_X86`.

From a clean protected-main checkout, build the wheel and controller image with a unique candidate
tag:

```console
uv build --wheel
docker buildx build --platform linux/amd64 \
  --file infra/oci/controller/Dockerfile \
  --build-arg DANDER_WHEEL=dist/dander_platform-<version>-py3-none-any.whl \
  --tag ocir.<region>.oci.oraclecloud.com/<namespace>/<repository>:<controller-candidate> \
  --push .
```

Inspect the pushed image, record its `sha256:` digest outside Git, and pass the tag and digest
together to `dander init-oci-launcher-plan`. The controller uses the same private OCIR
repository selected by the manifest, but a separate unique tag from the task image. Never reuse a
tag, rebuild an accepted candidate, or
commit the wheel, registry credentials, saved plan, or Terraform state.

# OCI Artifact Contract

Dander publishes the runtime as a non-root OCI image and records each successful publication in
`.dander/runtime-artifact.json`. The record uses schema `io.dander.runtime.artifact/v1` and contains
the immutable index reference, each runnable platform-manifest digest, the build-context revision,
creation time, and whether SBOM and provenance attestations were requested. The local record is an
operator artifact and is intentionally ignored by Git.

Published images carry standard OCI source, documentation, license, version, revision, and creation
annotations. The packaged `runtime-capabilities.json` describes the adapters present in that build;
`dander runtime inspect` validates and reports it without accessing a cloud provider or secret.

`dander image-publish` builds one `linux/amd64,linux/arm64` index through BuildKit with SBOM and
provenance enabled, pushes the image, resolves the registry digest, inspects both required runnable
manifests, and only then writes the artifact record atomically. If post-push verification fails, the
command reports that the image may exist but does not report a successful artifact publication.

Cross-registry promotion copies the digest-qualified index without rebuilding it. A promotion is
valid only when the destination resolves to the same index digest and reports the same runnable
platform-manifest map; registries that rewrite the index fail verification.

The runtime executes as UID/GID `65532:65532`. CI runs the local conformance probe with a read-only
root filesystem and a temporary `/tmp`, so runtime code must declare durable state through an
adapter rather than writing into the image filesystem.

## Provider dependency assembly

Python installations expose `bigquery`, `snowflake`, `redshift`, `postgres`, `gcp`, `aws`,
`azure`, and `oci` extras. These install provider SDKs only; an extra is not an adapter or a support
claim. Provider implementation imports remain lazy until that provider is selected. The `oci`
extra requires Oracle's SDK `2.184.1` or newer because that release admits Dander's audited
`cryptography` 50 line. Earlier SDK releases remain incompatible with the full runtime.

The `runtime-all` extra is the deterministic union of those dependency sets. Repository and
generated source-free Dockerfiles install it, then validate every required distribution from
package metadata without importing an SDK. This catches an incomplete build before an image can be
published. The packaged capability manifest remains authoritative about adapters actually present
and supported in that image. Adding the OCI SDK and Vault resolver does not add an OCI launcher or
profile before their separate implementation and live gates pass.
The image uses the maintained Debian `libpq5` package with pure-Python Psycopg instead of bundling
an opaque database client library inside a wheel.

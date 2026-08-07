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

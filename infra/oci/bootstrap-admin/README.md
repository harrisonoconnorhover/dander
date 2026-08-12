# OCI stage zero

This root creates the two prerequisites that must exist before Dander can use remote state:

- a private, versioned Object Storage bucket; and
- a private OCIR repository for digest-addressed runtime and controller images.

OCIR currently rejects its advertised repository-level `is_immutable` setting in some tenancies.
Dander therefore does not ask Terraform to set that unsupported property. Publication still fails
closed on an existing tag mismatch, records the observed repository capability, verifies the exact
index and platform digests after every push, and supplies the immutable digest alongside the tag to
OCI Functions and Container Instances.

Use an OCI CLI `SecurityToken` profile. Do not use API-key, auth-token, or registry-password
credentials. The administrative bootstrap copies this root to a private operator directory,
saves a mode-`0600` plan there, applies only that reviewed plan, and then migrates local state to
Terraform's native `oci` backend.

The native OCI backend requires Terraform 1.12 or newer. State and plans are operator artifacts;
neither belongs in Git.

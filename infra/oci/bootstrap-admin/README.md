# OCI stage zero

This root creates the two prerequisites that must exist before Dander can use remote state:

- a private, versioned Object Storage bucket; and
- a private, immutable OCIR repository.

Use an OCI CLI `SecurityToken` profile. Do not use API-key, auth-token, or registry-password
credentials. The administrative bootstrap copies this root to a private operator directory,
saves a mode-`0600` plan there, applies only that reviewed plan, and then migrates local state to
Terraform's native `oci` backend.

The native OCI backend requires Terraform 1.12 or newer. State and plans are operator artifacts;
neither belongs in Git.

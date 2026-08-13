"""Declarative request/payload spec attachable to a `source`-node config.

`SourceNodeConfig` (see `dander.pipeline.node_config`, DANDER-10) has no representation today of
*how* a source node calls its API: HTTP method, headers, query params, and a request body template
are all unrepresented. This module adds `RequestSpec` — a plain, inert Pydantic v2 model that
records that intent. Nothing here performs an HTTP request, resolves a secret, or renders a
template; it is model + serialization + validation only, matching the "records intent" posture of
the rest of `dander.pipeline` (`Transformation`, `JoinSpec`).

Per `steering/01-security.md`, header/query-param/body **values** must be **secret references or
field references only** — never an inline secret literal. This module defines the small reference
grammar those values must follow and the two validation rules (`_check_reference_values` on
`RequestSpec`) that enforce it:

- **Rule A (deterministic):** a header/param whose *name* is in a documented sensitive set
  (`SENSITIVE_HEADER_NAMES`/`SENSITIVE_PARAM_NAMES`) must be a recognized reference.
- **Rule B (defense in depth, best-effort):** any value anywhere that is not a recognized reference
  and matches a raw-credential shape (`looks_like_raw_credential`) is rejected.

Recognized references (never resolved here):

- **Secret reference:** ``secret:<name>``, ``env:<VAR_NAME>``, a Secret Manager resource name
  (``projects/.../secrets/.../versions/...``), or a Dander provider-qualified runtime reference
  such as ``gcp-sm://...`` or ``azure-kv://...``.
- **Field reference:** ``field:<field_name>`` or mustache ``{{ <field_name> }}``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "cookie",
        "set-cookie",
    }
)
"""Header names (lowercase) that must always carry a reference, never a literal (Rule A)."""

SENSITIVE_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "access_token",
        "access-token",
        "auth_token",
        "auth-token",
        "token",
        "secret",
        "client_secret",
        "client-secret",
        "password",
    }
)
"""Query-param names (lowercase) that must always carry a reference, never a literal (Rule A)."""

_SECRET_MANAGER_RESOURCE_RE = re.compile(r"^projects/[^/\s]+/secrets/[^/\s]+/versions/[^/\s]+$")
_ENV_REFERENCE_RE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_REFERENCE_RE = re.compile(r"^secret:\S+$")
_PROVIDER_SECRET_REFERENCE_RE = re.compile(
    r"^(?:env|gcp-sm|aws-sm|azure-kv|oci-vault)://"
    r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,1023}$"
)
_FIELD_REFERENCE_RE = re.compile(r"^field:\S+$")
_MUSTACHE_FIELD_REFERENCE_RE = re.compile(r"^\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}$")

_AUTH_SCHEME_PREFIX_RE = re.compile(r"^(bearer|basic|token)\s+\S+", re.IGNORECASE)
_KNOWN_CREDENTIAL_PREFIXES = ("sk_", "sk-", "AKIA", "ghp_", "xox")
_HIGH_ENTROPY_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/_=-]{24,}$")


class HttpMethod(StrEnum):
    """The closed set of HTTP methods a `RequestSpec` may declare.

    A `StrEnum` (matching the `TransformationKind`/`JoinType` convention in `graph.py`) so callers
    get a named, importable type that still serializes to/from its plain string value stably in
    YAML and JSON. An out-of-set value fails validation with a clear error at the Pydantic
    boundary. `HEAD`/`OPTIONS` are intentionally omitted (no source currently needs them); extend
    by adding a member later without touching callers.

    Attributes:
        GET: Retrieve a resource. The default method (a simple GET needs no explicit spec).
        POST: Create a resource / submit a query body.
        PUT: Replace a resource.
        PATCH: Partially update a resource.
        DELETE: Remove a resource.
    """

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


def is_secret_reference(value: str) -> bool:
    """Return whether `value` is a recognized secret reference.

    Recognized forms: ``secret:<name>``, ``env:<VAR_NAME>``, a Secret Manager resource name
    (``projects/.../secrets/.../versions/...``), or one of Dander's provider-qualified runtime
    references (``env://``, ``gcp-sm://``, ``aws-sm://``, ``azure-kv://``, ``oci-vault://``).
    Never resolves the reference — this is a pure shape check.

    Args:
        value: The candidate string.

    Returns:
        `True` if `value` matches one of the recognized secret-reference forms.
    """
    return bool(
        _SECRET_REFERENCE_RE.match(value)
        or _ENV_REFERENCE_RE.match(value)
        or _SECRET_MANAGER_RESOURCE_RE.match(value)
        or _PROVIDER_SECRET_REFERENCE_RE.match(value)
    )


def is_field_reference(value: str) -> bool:
    """Return whether `value` is a recognized field reference.

    Recognized forms: ``field:<field_name>`` or mustache ``{{ <field_name> }}``.

    Args:
        value: The candidate string.

    Returns:
        `True` if `value` matches one of the recognized field-reference forms.
    """
    return bool(_FIELD_REFERENCE_RE.match(value) or _MUSTACHE_FIELD_REFERENCE_RE.match(value))


def is_reference(value: str) -> bool:
    """Return whether `value` is any recognized reference — secret or field.

    Args:
        value: The candidate string.

    Returns:
        `True` if `value` is a secret reference or a field reference.
    """
    return is_secret_reference(value) or is_field_reference(value)


def _is_high_entropy_run(value: str) -> bool:
    """Return whether `value` is a long, whitespace-free, alnum-mixed base64/hex-ish run.

    Requires both a digit and a letter so an ordinary long English word (all-alpha) does not
    false-positive; a raw token/hash/key characteristically mixes letters and digits.
    """
    if not _HIGH_ENTROPY_CHARSET_RE.match(value):
        return False
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def looks_like_raw_credential(value: str) -> bool:
    """Best-effort heuristic: does `value` have the shape of an inline raw credential literal?

    This is the Rule B tripwire — defense in depth, not a proof. It flags: an
    ``Authorization``-style scheme prefix (``Bearer``/``Basic``/``Token``) followed by an inline
    token, a PEM block (``-----BEGIN``), a known credential-key prefix (e.g. Stripe ``sk_``/
    ``sk-``, AWS ``AKIA``, GitHub ``ghp_``, Slack ``xox*``), or a long whitespace-free
    base64/hex-ish run that mixes letters and digits. A recognized reference (`is_reference`) is
    never flagged — e.g. a Secret Manager resource name can legitimately contain a long
    digit-and-letter path segment (a numeric project id, a version number) that would otherwise
    trip the high-entropy heuristic, so references are excluded up front rather than relying on
    every caller to order its checks correctly.

    Args:
        value: The candidate string.

    Returns:
        `True` if `value` is not a recognized reference and matches one of the documented
        raw-credential shapes.
    """
    if not value or is_reference(value):
        return False
    if _AUTH_SCHEME_PREFIX_RE.match(value):
        return True
    if "-----BEGIN" in value:
        return True
    if value.startswith(_KNOWN_CREDENTIAL_PREFIXES):
        return True
    return " " not in value and _is_high_entropy_run(value)


def _check_value(position: str, value: str, *, sensitive: bool) -> None:
    """Enforce Rule A (if `sensitive`) and Rule B on one header/param/body-leaf value.

    Never echoes `value` in a raised message (`steering/01-security.md`).

    Args:
        position: Human-readable position name for the error message (e.g. ``"header
            'Authorization'"``, ``"query param 'token'"``, or ``"body"``).
        value: The value to check.
        sensitive: Whether `position` is a documented sensitive name (Rule A applies).

    Raises:
        ValueError: If `sensitive` and `value` is not a recognized reference (Rule A), or if
            `value` is not a recognized reference and looks like a raw credential (Rule B).
    """
    if is_reference(value):
        return
    if sensitive:
        raise ValueError(
            f"{position} is a sensitive position and must be a secret or field reference "
            "(e.g. 'secret:<name>', 'env:<VAR>', 'field:<name>'), not a literal value."
        )
    if looks_like_raw_credential(value):
        raise ValueError(
            f"{position} looks like an inline raw credential literal; use a secret or field "
            "reference instead of a literal value."
        )


def _check_body_leaves(node: object) -> None:
    """Recursively apply Rule B to every string leaf of a `body` template.

    Args:
        node: The `body` value (or a nested dict/list/scalar within it). Typed `object` (not
            `Any`) since this only ever inspects it via `isinstance` narrowing.

    Raises:
        ValueError: If any string leaf is not a recognized reference and looks like a raw
            credential (Rule B). Body leaves have no "name", so Rule A never applies here.
    """
    if isinstance(node, str):
        _check_value("body", node, sensitive=False)
    elif isinstance(node, dict):
        for child in node.values():
            _check_body_leaves(child)
    elif isinstance(node, list):
        for item in node:
            _check_body_leaves(item)


class RequestSpec(BaseModel):
    """Declarative spec for how a `source` node calls its API.

    Inert: nothing here performs an HTTP request, resolves a secret, or renders `body`. Attach it
    to `dander.pipeline.node_config.SourceNodeConfig.request`; `None` (the default on that field)
    means a plain, spec-less GET, unchanged from a pre-DANDER-11 source node.

    Header, query-param, and body values must be **secret references or field references only**
    (`steering/01-security.md`) — never an inline secret literal. See the module docstring for the
    recognized reference grammar and the two enforcement rules.

    Attributes:
        method: HTTP method. Defaults to `GET`, the sensible default for a spec that only needs to
            add headers/params to a simple GET endpoint.
        headers: Request headers, name -> value. A value must be a reference (mandatory for any
            name in `SENSITIVE_HEADER_NAMES`; recommended, and Rule-B-checked, otherwise). Never
            resolved or sent here.
        query_params: Query-string parameters, name -> value. Same reference contract as
            `headers`. Named `query_params` (not `params`) to avoid colliding with the
            `config`/`params` alias on `dander.pipeline.graph.Node`.
        body: A request body template: a JSON-object template (`dict[str, Any]`) or a raw string
            template (e.g. for a GraphQL body), or `None` for no body. Every string leaf is
            Rule-B-checked. Never rendered here — the Ingestion layer resolves/renders it later.

    `hide_input_in_errors=True` is set for the same reason `dander.pipeline.graph.Node` sets it:
    without it, Pydantic's default `ValidationError` rendering appends a `repr()` of the entire
    rejected input (`input_value={'headers': {'Authorization': 'Bearer <the literal>'}, ...}`)
    after the raised `ValueError` text, which would leak the offending literal even though the
    message itself only names the position (`steering/01-security.md`).
    """

    model_config = ConfigDict(populate_by_name=True, hide_input_in_errors=True)

    method: HttpMethod = HttpMethod.GET
    headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | str | None = None

    @model_validator(mode="after")
    def _check_reference_values(self) -> RequestSpec:
        """Enforce the secret/field-reference-only contract on headers, params, and body.

        Applies Rule A (sensitive names must be a reference) to `headers`/`query_params` by name,
        and Rule B (credential-shaped literals are rejected) to every string leaf of `headers`,
        `query_params`, and `body`.

        Raises:
            ValueError: See `_check_value`/`_check_body_leaves`. Never echoes the offending value.
        """
        for name, value in self.headers.items():
            _check_value(
                f"header '{name}'", value, sensitive=name.lower() in SENSITIVE_HEADER_NAMES
            )
        for name, value in self.query_params.items():
            _check_value(
                f"query param '{name}'",
                value,
                sensitive=name.lower() in SENSITIVE_PARAM_NAMES,
            )
        if self.body is not None:
            _check_body_leaves(self.body)
        return self

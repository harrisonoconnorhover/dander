"""Provider-neutral hosted Control service deployment contracts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import BaseModel, ConfigDict

from dander.control.auth import HostedOIDCDeploymentInput
from dander.deployment import SecretReference
from dander.deployment.service import (
    CONTROL_SERVICE_PROJECTION_SCHEMA,
    AzureBlobGraphStoreBinding,
    ControlServiceIngress,
    ControlServiceObservability,
    ControlServiceProbes,
    ControlServiceProjectionError,
    ControlServiceResources,
    ControlServiceRuntime,
    ControlServiceScaling,
    ControlServiceTemplate,
    ControlServiceTemplateFactory,
    GCSGraphStoreBinding,
    IngressVisibility,
    LocalGraphStoreBinding,
    OCIObjectGraphStoreBinding,
    ResolvedControlServiceRequest,
    S3GraphStoreBinding,
    StaticAssetBundle,
    graph_store_binding_from_json,
)
from dander.providers import (
    PROVIDER_API_VERSION,
    ProviderFactory,
    ProviderFactoryError,
    ProviderKind,
    ProviderRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dander.deployment.service import GraphStoreBinding

_IMAGE = "registry.example.test/dander/control@sha256:" + "a" * 64
_ROLLBACK_DIGEST = "sha256:" + "b" * 64


class _FakeTemplateFactory:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, request: ResolvedControlServiceRequest) -> ControlServiceTemplate:
        self.calls += 1
        return ControlServiceTemplate(
            schema=CONTROL_SERVICE_PROJECTION_SCHEMA,
            provider_id="example_service",
            request=request,
        )


class _ExampleServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["example_service"]
    region: str


def _oidc() -> HostedOIDCDeploymentInput:
    return HostedOIDCDeploymentInput(
        api_url="https://control.example.test",
        issuer="https://issuer.example.test",
        jwks_uri="https://issuer.example.test/.well-known/jwks.json",
        public_client_id="druff-spa",
        api_audience="dander-control",
        redirect_uri="https://druff.example.test/auth/callback",
        logout_uri="https://druff.example.test/signed-out",
        allowed_origins=("https://druff.example.test",),
    )


def _request(
    *,
    graph_store: GraphStoreBinding | None = None,
    environment: tuple[tuple[str, str], ...] = (("LOG_LEVEL", "info"),),
    secret_bindings: tuple[tuple[str, SecretReference], ...] = (),
) -> ResolvedControlServiceRequest:
    return ResolvedControlServiceRequest(
        service_id="dander_control",
        profile_id="hosted",
        image=_IMAGE,
        port=8770,
        probes=ControlServiceProbes(),
        resources=ControlServiceResources(cpu_millis=1000, memory_mib=1024),
        scaling=ControlServiceScaling(
            minimum_instances=1,
            maximum_instances=3,
            shutdown_grace_seconds=60,
        ),
        environment=environment,
        secret_bindings=secret_bindings,
        workload_identity="dander-control@example.invalid",
        ingress=ControlServiceIngress(visibility=IngressVisibility.PUBLIC),
        oidc=_oidc(),
        oidc_config_path="/etc/dander/control-oidc.json",
        graph_store_config_path="/etc/dander/control-graph-store.json",
        graph_store=graph_store or GCSGraphStoreBinding(bucket="dander-control-test"),
        observability=ControlServiceObservability(
            log_destination="provider-default",
            alert_target="operator@example.invalid",
            retention_days=30,
        ),
        rollback_digest=_ROLLBACK_DIGEST,
    )


def test_request_derives_runnable_hosted_command_and_one_oidc_origin_source() -> None:
    request = _request(
        environment=(("TRACE_EXPORT", "disabled"), ("LOG_LEVEL", "info")),
        secret_bindings=(
            (
                "OIDC_AUXILIARY_SECRET",
                SecretReference(
                    provider="azure_key_vault",
                    reference="azure-kv://control-vault/secrets/oidc-auxiliary-secret",
                ),
            ),
        ),
    )

    assert request.command == (
        "control",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        "8770",
        "--oidc-config",
        "/etc/dander/control-oidc.json",
        "--graph-store-config",
        "/etc/dander/control-graph-store.json",
    )
    assert request.allowed_origins == request.oidc.allowed_origins
    assert request.environment == (("LOG_LEVEL", "info"), ("TRACE_EXPORT", "disabled"))
    rendered = request.as_dict()
    assert rendered["ingress"] == {
        "visibility": "public",
        "allowed_origins": ["https://druff.example.test"],
    }
    assert rendered["graph_store"] == {
        "kind": "gcs",
        "bucket": "dander-control-test",
        "prefix": "dander-control/v1",
    }
    assert "oidc-auxiliary-secret" in json.dumps(rendered, sort_keys=True)
    assert "client_secret" not in json.dumps(rendered, sort_keys=True)


@pytest.mark.parametrize(
    "binding",
    [
        LocalGraphStoreBinding(root="/var/lib/dander/control"),
        GCSGraphStoreBinding(bucket="dander-control-test"),
        S3GraphStoreBinding(
            bucket="dander-control-test",
            expected_bucket_owner="123456789012",
        ),
        AzureBlobGraphStoreBinding(
            account_url="https://dandercontrol.blob.core.windows.net",
            container="graphs",
        ),
        OCIObjectGraphStoreBinding(namespace="dander", bucket="graphs"),
    ],
)
def test_graph_store_bindings_are_closed_credential_free_locators(
    binding: GraphStoreBinding,
) -> None:
    rendered = _request(graph_store=binding).as_dict()["graph_store"]
    assert isinstance(rendered, dict)
    assert rendered["kind"] == binding.kind
    serialized = json.dumps(rendered, sort_keys=True).casefold()
    assert "credential" not in serialized
    assert "secret" not in serialized
    assert "token" not in serialized
    assert graph_store_binding_from_json(json.dumps(rendered)).as_dict() == rendered


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: GCSGraphStoreBinding(bucket="Bad/Bucket"), "GCS"),
        (lambda: S3GraphStoreBinding(bucket="directory--x-s3"), "S3"),
        (
            lambda: AzureBlobGraphStoreBinding(
                account_url="https://user:password@example.test",
                container="graphs",
            ),
            "Azure",
        ),
        (lambda: LocalGraphStoreBinding(root="relative/control"), "local"),
        (lambda: OCIObjectGraphStoreBinding(namespace="bad/name", bucket="graphs"), "OCI"),
    ],
)
def test_graph_store_bindings_fail_closed_before_provider_access(
    build: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ControlServiceProjectionError, match=message):
        build()


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "gcs", "bucket": "dander-control-test", "credential": "forbidden"},
        {"kind": "unknown", "bucket": "dander-control-test"},
        {"kind": "s3"},
        ["gcs", "dander-control-test"],
    ],
)
def test_graph_store_json_rejects_non_union_shapes(payload: object) -> None:
    with pytest.raises(ControlServiceProjectionError, match="GraphStore configuration"):
        graph_store_binding_from_json(json.dumps(payload))


def test_request_is_immutable_deterministic_and_rejects_unsafe_overlap() -> None:
    first = _request(environment=(("Z_VALUE", "2"), ("A_VALUE", "1")))
    second = _request(environment=(("A_VALUE", "1"), ("Z_VALUE", "2")))
    assert first.as_dict() == second.as_dict()
    with pytest.raises(FrozenInstanceError):
        first.port = 9000  # type: ignore[misc]

    with pytest.raises(ControlServiceProjectionError, match="overlap"):
        _request(
            environment=(("SHARED_VALUE", "not-secret"),),
            secret_bindings=(
                (
                    "SHARED_VALUE",
                    SecretReference(provider="environment", reference="env://SHARED_VALUE"),
                ),
            ),
        )
    with pytest.raises(ControlServiceProjectionError, match="rollback digest"):
        replace(first, rollback_digest="sha256:" + "a" * 64)


def test_service_provider_is_validated_then_loaded_only_when_selected() -> None:
    loads = 0
    factory = _FakeTemplateFactory()

    def load() -> ProviderFactory[object]:
        nonlocal loads
        loads += 1

        def build(config: BaseModel, _context: Mapping[str, object]) -> object:
            assert isinstance(config, _ExampleServiceConfig)
            return ControlServiceRuntime(
                provider_id="example_service",
                region=config.region,
                templates=factory,
            )

        return ProviderFactory(
            kind=ProviderKind.SERVICE,
            provider_id="example_service",
            api_version=PROVIDER_API_VERSION,
            build=build,
        )

    registry = ProviderRegistry()
    registry.register(
        kind=ProviderKind.SERVICE,
        provider_id="example_service",
        config_model=_ExampleServiceConfig,
        load_factory=load,
    )
    with pytest.raises(ProviderFactoryError, match="region"):
        registry.parse(ProviderKind.SERVICE, {"provider": "example_service"})
    assert loads == 0

    config = registry.parse(
        ProviderKind.SERVICE,
        {"provider": "example_service", "region": "us-test-1"},
    )
    runtime = registry.build(ProviderKind.SERVICE, config)
    assert isinstance(runtime, ControlServiceRuntime)
    assert loads == 1
    template = runtime.templates.build(_request())
    assert isinstance(runtime.templates, ControlServiceTemplateFactory)
    assert template.as_dict() == runtime.templates.build(_request()).as_dict()
    assert factory.calls == 2


def test_static_asset_bundle_is_separate_immutable_and_deterministic() -> None:
    bundle = StaticAssetBundle(
        artifact_digest="sha256:" + "c" * 64,
        entrypoint="/index.html",
        bootstrap_path="/bootstrap.json",
        bootstrap_digest="sha256:" + "d" * 64,
        security_headers=(
            ("x-content-type-options", "nosniff"),
            ("content-security-policy", "default-src 'self'"),
        ),
    )
    assert tuple(bundle.as_dict()) == (
        "artifact_digest",
        "entrypoint",
        "bootstrap_path",
        "bootstrap_digest",
        "security_headers",
    )
    rendered_headers = bundle.as_dict()["security_headers"]
    assert isinstance(rendered_headers, dict)
    assert tuple(rendered_headers) == (
        "content-security-policy",
        "x-content-type-options",
    )
    with pytest.raises(FrozenInstanceError):
        bundle.entrypoint = "/other.html"  # type: ignore[misc]

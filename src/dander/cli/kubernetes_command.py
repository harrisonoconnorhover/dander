"""Existing-cluster Kubernetes planning and read-only verification commands."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer
from click import ClickException
from rich.console import Console

from dander.providers.kubernetes.operations import (
    KubernetesOperationError,
    KubernetesOperations,
)

console = Console()
kubernetes_app = typer.Typer(
    help="Render and verify manifest-bound Kubernetes deployments on existing clusters."
)
_DEFAULT_PROJECT_CONFIG = Path("dander.yaml")
_DEFAULT_OUTPUT_DIR = Path(".dander/kubernetes")


@kubernetes_app.command("plan")
def kubernetes_plan(
    deployment: str = typer.Option(..., "--deployment"),
    container_image: str = typer.Option(
        ..., "--container-image", help="Immutable runtime image ending in @sha256 digest."
    ),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
    output_dir: Path = typer.Option(_DEFAULT_OUTPUT_DIR, "--output-dir"),  # noqa: B008
) -> None:
    """Lint and render a saved Helm plan without contacting or mutating a cluster."""
    try:
        plan = KubernetesOperations().plan(
            config=config,
            deployment=deployment,
            image=container_image,
            output_dir=output_dir,
            platforms_config=platforms_config,
        )
    except KubernetesOperationError as error:
        raise ClickException(str(error)) from error
    console.print(
        f"[green]Kubernetes release rendered.[/green] Manifests: {plan.manifests}",
        soft_wrap=True,
    )
    console.print(f"Review values: {plan.values}", soft_wrap=True)
    next_command = (
        "helm",
        "--kube-context",
        plan.context,
        "upgrade",
        "--install",
        plan.release_name,
        str(plan.chart),
        "--namespace",
        plan.namespace,
        "--create-namespace",
        "--values",
        str(plan.values),
    )
    console.print("Next after review: " + shlex.join(next_command), soft_wrap=True)


@kubernetes_app.command("verify")
def kubernetes_verify(
    deployment: str = typer.Option(..., "--deployment"),
    expected_image: str = typer.Option(..., "--expected-image"),
    config: Path = typer.Option(_DEFAULT_PROJECT_CONFIG, "--config"),  # noqa: B008
    platforms_config: Path | None = typer.Option(None, "--platforms-config"),  # noqa: B008
) -> None:
    """Compare one installed release to its manifest using read-only kubectl calls."""
    try:
        verification = KubernetesOperations().verify(
            config=config,
            deployment=deployment,
            expected_image=expected_image,
            platforms_config=platforms_config,
        )
    except KubernetesOperationError as error:
        raise ClickException(str(error)) from error
    console.print_json(data=verification.as_dict())


def register_kubernetes_commands(app: typer.Typer) -> None:
    """Register the namespaced Kubernetes command group."""
    app.add_typer(kubernetes_app, name="kubernetes")


__all__ = ["register_kubernetes_commands"]

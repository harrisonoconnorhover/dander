"""Source-free candidate images and isolated Terraform previews for one graph binding.

The browser supplies only the ETag of the graph it opened. Every deployment input is fixed by the
operator when starting ``dander graph serve``. A preview pushes an immutable candidate image, but
its Terraform plan lives only in a temporary directory and can never be consumed by Dander's
normal apply command.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from dander import __version__
from dander.bootstrap import (
    ProjectBootstrapError,
    RuntimeImagePublisher,
    TerraformBootstrap,
    TerraformBootstrapError,
)
from dander.project import ProjectConfigError, load_project_config

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dander.pipeline.graph_operations import GraphOperationBinding
    from dander.pipeline.graph_service import GraphDocumentStore

_DANDER_VERSION_PLACEHOLDER = "__DANDER_DISTRIBUTION_VERSION__"
_PLAN_SUMMARY = re.compile(r"Plan: \d+ to add, \d+ to change, \d+ to destroy\.")
_MAX_PLAN_TEXT_BYTES = 2 * 1024 * 1024


class GraphDeploymentError(RuntimeError):
    """A candidate or plan could not be created safely."""


class GraphDeploymentStaleError(GraphDeploymentError):
    """The project changed while a candidate preview was being created."""


@dataclass(frozen=True)
class GraphDeploymentSettings:
    """Complete non-browser Terraform inputs fixed by the operator at service startup."""

    state_bucket: str
    bootstrap_service_account: str
    billing_account_id: str
    failure_alert_email: str
    secret_ids: tuple[str, ...] = ()
    state_prefix: str = "dander/state"
    github_repository: str = ""
    github_ref: str = "refs/heads/main"
    enable_cost_guard: bool | None = None
    cost_guard_budget_name: str = "dander-sbx-cap"
    cost_guard_budget_amount: str = "5.00"
    live_cost_guard: bool = False


@dataclass(frozen=True)
class GraphDeploymentPreview:
    """Safe browser projection of one immutable candidate and its exact human plan."""

    revision: str
    candidate_image: str
    plan_sha256: str
    plan_summary: str
    plan_text: str
    affected_jobs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class _ImagePublisher(Protocol):
    def publish(self, *, project: str, region: str, tag_prefix: str = "init") -> str: ...


class _TerraformPlanner(Protocol):
    def execute(
        self,
        *,
        project: str,
        state_bucket: str,
        state_prefix: str,
        bootstrap_service_account: str,
        apply: bool,
        region: str = "us-central1",
        bigquery_location: str = "US",
        runtime_cpu: int = 1,
        runtime_memory: str = "512Mi",
        runtime_timeout_seconds: int = 300,
        runtime_max_retries: int = 1,
        runtime_batch_rows: int = 10_000,
        require_guarded_free_tier: bool = True,
        enable_runtime: bool = False,
        billing_account_id: str = "",
        container_image: str = "",
        pipelines: Mapping[str, Mapping[str, object]] | None = None,
        failure_alert_email: str = "",
        secret_ids: tuple[str, ...] = (),
        github_repository: str = "",
        github_ref: str = "refs/heads/main",
        enable_cost_guard: bool = False,
        cost_guard_budget_name: str = "dander-sbx-cap",
        cost_guard_budget_amount: str = "5.00",
        live_cost_guard: bool = False,
    ) -> Path: ...


ImagePublisherFactory = Callable[[Path], _ImagePublisher]
TerraformPlannerFactory = Callable[[Path], _TerraformPlanner]
PlanRenderer = Callable[[Path, Path], str]


def _runtime_publisher(path: Path) -> _ImagePublisher:
    return RuntimeImagePublisher(path)


def _terraform_planner(path: Path) -> _TerraformPlanner:
    return TerraformBootstrap(path)


class GraphDeploymentPreviewer:
    """Build a source-free snapshot and render a non-applyable full-platform plan."""

    def __init__(
        self,
        binding: GraphOperationBinding,
        settings: GraphDeploymentSettings,
        *,
        image_publisher_factory: ImagePublisherFactory = _runtime_publisher,
        terraform_planner_factory: TerraformPlannerFactory = _terraform_planner,
        plan_renderer: PlanRenderer | None = None,
    ) -> None:
        self.binding = binding
        self.settings = settings
        self._image_publisher_factory = image_publisher_factory
        self._terraform_planner_factory = terraform_planner_factory
        self._plan_renderer = plan_renderer or _render_plan

    def preview(
        self,
        store: GraphDocumentStore,
        *,
        expected_revision: str,
    ) -> GraphDeploymentPreview:
        """Push one candidate and return an isolated plan tied to ``expected_revision``."""
        if store.load().revision != expected_revision:
            raise GraphDeploymentStaleError(
                "The graph changed before the deployment preview started. Reopen it first."
            )
        project_root = self.binding.project_config.parent
        current_manifest = load_project_config(self.binding.project_config)
        resolved_cost_guard = (
            current_manifest.platform.safety.require_guarded_free_tier
            if self.settings.enable_cost_guard is None
            else self.settings.enable_cost_guard
        )
        if current_manifest.platform.safety.require_guarded_free_tier and not resolved_cost_guard:
            raise GraphDeploymentError(
                "The guarded project requires the cost guard for a deployment preview."
            )
        if resolved_cost_guard and not self.settings.billing_account_id:
            raise GraphDeploymentError(
                "The cost-guard deployment preview requires a billing-account ID."
            )
        before = _deployment_fingerprint(project_root, self.binding.project_config)

        try:
            with tempfile.TemporaryDirectory(prefix="dander-graph-preview-") as raw_directory:
                workspace = Path(raw_directory)
                image_context = workspace / "image"
                preview_infra = workspace / "infra"
                _copy_source_free_project(
                    project_root=project_root,
                    project_config=self.binding.project_config,
                    destination=image_context,
                )
                _copy_tree(project_root / "infra", preview_infra, terraform=True)
                _validate_snapshot(
                    image_context=image_context,
                    graph_relative=self.binding.graph_file.relative_to(project_root),
                    expected_revision=expected_revision,
                )

                candidate_image = self._image_publisher_factory(image_context).publish(
                    project=self.binding.project,
                    region=self.binding.region,
                    tag_prefix="candidate",
                )
                manifest = load_project_config(image_context / "dander.yaml")
                platform = manifest.platform
                cost_guard = (
                    platform.safety.require_guarded_free_tier
                    if self.settings.enable_cost_guard is None
                    else self.settings.enable_cost_guard
                )
                plan_path = self._terraform_planner_factory(preview_infra).execute(
                    project=self.binding.project,
                    state_bucket=self.settings.state_bucket,
                    state_prefix=self.settings.state_prefix,
                    bootstrap_service_account=self.settings.bootstrap_service_account,
                    apply=False,
                    region=platform.region,
                    bigquery_location=platform.bigquery_location,
                    runtime_cpu=platform.runtime.cpu,
                    runtime_memory=platform.runtime.memory,
                    runtime_timeout_seconds=platform.runtime.timeout_seconds,
                    runtime_max_retries=platform.runtime.max_retries,
                    runtime_batch_rows=platform.runtime.batch_rows,
                    require_guarded_free_tier=platform.safety.require_guarded_free_tier,
                    enable_runtime=True,
                    billing_account_id=(self.settings.billing_account_id if cost_guard else ""),
                    container_image=candidate_image,
                    pipelines=manifest.terraform_pipelines(),
                    failure_alert_email=self.settings.failure_alert_email,
                    secret_ids=tuple(
                        sorted(
                            set(self.settings.secret_ids)
                            | {
                                secret_id
                                for pipeline in manifest.pipelines.values()
                                for secret_id in pipeline.secrets.values()
                            }
                        )
                    ),
                    github_repository=self.settings.github_repository,
                    github_ref=self.settings.github_ref,
                    enable_cost_guard=cost_guard,
                    cost_guard_budget_name=self.settings.cost_guard_budget_name,
                    cost_guard_budget_amount=self.settings.cost_guard_budget_amount,
                    live_cost_guard=self.settings.live_cost_guard,
                )
                plan_text = self._plan_renderer(plan_path, preview_infra)
                plan_digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
                affected_jobs = tuple(
                    str(pipeline["job_name"])
                    for pipeline in manifest.terraform_pipelines().values()
                )
        except GraphDeploymentError:
            raise
        except (
            OSError,
            ProjectBootstrapError,
            ProjectConfigError,
            TerraformBootstrapError,
        ) as error:
            raise GraphDeploymentError(
                "Could not build the source-free candidate or create its Terraform preview."
            ) from error

        after = _deployment_fingerprint(project_root, self.binding.project_config)
        if before != after or store.load().revision != expected_revision:
            raise GraphDeploymentStaleError(
                "The Dander project changed while the preview was being created. Build it again."
            )
        return GraphDeploymentPreview(
            revision=expected_revision,
            candidate_image=candidate_image,
            plan_sha256=plan_digest,
            plan_summary=_summarize_plan(plan_text),
            plan_text=plan_text,
            affected_jobs=affected_jobs,
        )


def _copy_source_free_project(
    *,
    project_root: Path,
    project_config: Path,
    destination: Path,
) -> None:
    destination.mkdir()
    for directory in ("connectors", "graphs", "models"):
        _copy_tree(project_root / directory, destination / directory)
    shutil.copy2(project_config, destination / "dander.yaml")
    dockerfile = (
        files("dander").joinpath("templates", "project", "Dockerfile").read_text(encoding="utf-8")
    )
    if dockerfile.count(_DANDER_VERSION_PLACEHOLDER) != 1:
        raise GraphDeploymentError("The packaged source-free Dockerfile is invalid.")
    (destination / "Dockerfile").write_text(
        dockerfile.replace(_DANDER_VERSION_PLACEHOLDER, __version__),
        encoding="utf-8",
    )


def _copy_tree(source: Path, destination: Path, *, terraform: bool = False) -> None:
    if not source.is_dir() or source.is_symlink():
        raise GraphDeploymentError(f"Required project directory is unavailable: {source.name}")
    destination.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise GraphDeploymentError("Deployment preview inputs must not contain symlinks.")
        relative = path.relative_to(source)
        if terraform and _is_local_terraform_artifact(relative):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _is_local_terraform_artifact(path: Path) -> bool:
    return (
        ".terraform" in path.parts
        or "__pycache__" in path.parts
        or path.name.endswith(".tfplan")
        or ".tfstate" in path.name
        or (path.name.endswith(".tfvars") and not path.name.endswith(".tfvars.example"))
        or path.name == ".DS_Store"
    )


def _validate_snapshot(
    *,
    image_context: Path,
    graph_relative: Path,
    expected_revision: str,
) -> None:
    manifest = load_project_config(image_context / "dander.yaml")
    manifest.validate_references(image_context)
    snapshot_graph = image_context / graph_relative
    if (
        not snapshot_graph.is_file()
        or hashlib.sha256(snapshot_graph.read_bytes()).hexdigest() != expected_revision
    ):
        raise GraphDeploymentStaleError(
            "The graph changed while its source-free snapshot was being created."
        )
    if (image_context / "src").exists():
        raise GraphDeploymentError("A source-free candidate must not contain repository source.")


def _deployment_fingerprint(project_root: Path, project_config: Path) -> str:
    hasher = hashlib.sha256()
    candidates = [project_config]
    for directory in ("connectors", "graphs", "models", "infra"):
        root = project_root / directory
        if not root.is_dir() or root.is_symlink():
            raise GraphDeploymentError(f"Required project directory is unavailable: {directory}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise GraphDeploymentError("Deployment preview inputs must not contain symlinks.")
            if path.is_file() and not (
                directory == "infra" and _is_local_terraform_artifact(path.relative_to(root))
            ):
                candidates.append(path)
    for path in sorted(candidates):
        hasher.update(path.relative_to(project_root).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _render_plan(plan_path: Path, infra_dir: Path) -> str:
    try:
        completed = subprocess.run(
            ("terraform", "show", "-no-color", str(plan_path)),
            cwd=infra_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise GraphDeploymentError("Could not render the Terraform preview.") from error
    plan_text = completed.stdout
    if len(plan_text.encode()) > _MAX_PLAN_TEXT_BYTES:
        raise GraphDeploymentError("The Terraform preview is too large to display in Druff.")
    return plan_text


def _summarize_plan(plan_text: str) -> str:
    if match := _PLAN_SUMMARY.search(plan_text):
        return match.group(0)
    if "No changes." in plan_text:
        return "No changes."
    return "Terraform plan created; review the exact plan below."

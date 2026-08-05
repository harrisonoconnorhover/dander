"""Read-only verification of resources created by the Terraform bootstrap."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from google.cloud import bigquery


class DeploymentVerificationError(RuntimeError):
    """Raised when the deployment verifier cannot complete its checks."""


class VerificationStatus(StrEnum):
    """Stable machine-readable outcome categories for deployment checks."""

    VERIFIED = "verified"
    MISSING_REQUIRED_BINDING = "missing_required_binding"
    UNEXPECTED_BINDING = "unexpected_binding"
    BROAD_BINDING_DETECTED = "broad_binding_detected"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


@dataclass(frozen=True)
class VerificationCheck:
    """A sanitized check result suitable for a retained evidence artifact."""

    name: str
    ok: bool
    detail: str
    status: VerificationStatus | None = None

    @property
    def resolved_status(self) -> VerificationStatus:
        """Return an explicit status while preserving compatibility with older callers."""
        return self.status or (
            VerificationStatus.VERIFIED if self.ok else VerificationStatus.RESOURCE_UNAVAILABLE
        )


@dataclass(frozen=True)
class DeploymentSummary:
    """Sanitized deployment evidence; it never contains resource payloads."""

    project_id: str
    checked_at_utc: str
    checks: tuple[VerificationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "checked_at_utc": self.checked_at_utc,
            "passed": self.passed,
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "status": check.resolved_status,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }


CommandRunner = Callable[[tuple[str, ...], Path], str]


class DatasetClient(Protocol):
    """Small BigQuery client surface used by the verifier."""

    def get_dataset(self, dataset_ref: str) -> object: ...


BigQueryClientFactory = Callable[[str], DatasetClient]
_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")


def _run_command(args: tuple[str, ...], cwd: Path) -> str:
    """Run a read-only command and return stdout without exposing it to the caller."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise DeploymentVerificationError(f"Required command is unavailable: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        raise DeploymentVerificationError(f"Read-only check failed: {args[0]}") from error
    return result.stdout


def _bigquery_client(project: str) -> DatasetClient:
    return bigquery.Client(project=project)


class DeploymentVerifier:
    """Verify actual bootstrap resources using read-only Google Cloud operations."""

    def __init__(
        self,
        *,
        project: str,
        infra_dir: Path = Path("infra"),
        command_runner: CommandRunner = _run_command,
        bigquery_client_factory: BigQueryClientFactory = _bigquery_client,
    ) -> None:
        self.project = project
        self.infra_dir = infra_dir.resolve()
        self._run = command_runner
        self._bigquery_client = bigquery_client_factory

    def verify(
        self,
        *,
        datasets: tuple[str, ...] = ("raw", "staging", "marts", "dander_meta"),
        state_bucket: str | None = None,
        state_prefix: str | None = None,
        runtime_job: str | None = None,
        scheduler_job: str | None = None,
        runtime_service_account: str | None = None,
        runtime_image: str | None = None,
        secret_ids: tuple[str, ...] = (),
        region: str = "us-central1",
        expect_cost_guard: bool = False,
        billing_account_id: str | None = None,
        cost_guard_budget_name: str = "dander-sbx-cap",
        cost_guard_amount: float = 5.0,
        cost_guard_topic: str = "dander-stop-billing",
        cost_guard_function: str = "dander-stop-billing",
        cost_guard_simulate: bool = True,
        publish_dataplex: bool = False,
    ) -> DeploymentSummary:
        """Return evidence for a deployment; failed checks remain in the summary."""
        checks: list[VerificationCheck] = []
        checks.append(self._check_project())
        checks.extend(self._check_datasets(datasets))
        checks.append(self._check_remote_state(state_bucket, state_prefix))

        if runtime_job is not None:
            runtime_check, discovered_account, discovered_image = self._check_runtime_job(
                runtime_job,
                region,
                runtime_image,
            )
            checks.append(runtime_check)
            runtime_service_account = runtime_service_account or discovered_account
            if runtime_image is None:
                runtime_image = discovered_image
            checks.append(
                self._check_runtime_iam(
                    runtime_service_account,
                    publish_dataplex=publish_dataplex,
                    billing_account_id=billing_account_id,
                )
            )
            if billing_account_id:
                checks.append(
                    self._check_runtime_billing_iam(
                        runtime_service_account, billing_account_id=billing_account_id
                    )
                )
            checks.extend(self._check_runtime_datasets(runtime_service_account, datasets))
            checks.extend(self._check_runtime_secret_scope(runtime_service_account, secret_ids))
            checks.append(self._check_runtime_service_account_policy(runtime_service_account))
        if scheduler_job is not None:
            checks.append(self._check_scheduler(scheduler_job, region))
        checks.extend(self._check_secrets(secret_ids))
        if expect_cost_guard:
            checks.extend(
                self._check_cost_guard(
                    billing_account_id,
                    budget_name=cost_guard_budget_name,
                    amount=cost_guard_amount,
                    topic=cost_guard_topic,
                    function_name=cost_guard_function,
                    simulate=cost_guard_simulate,
                    region=region,
                )
            )

        return DeploymentSummary(
            project_id=self.project,
            checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            checks=tuple(checks),
        )

    def _check_project(self) -> VerificationCheck:
        try:
            payload = json.loads(
                self._run(
                    ("gcloud", "projects", "describe", self.project, "--format=json"),
                    self.infra_dir,
                )
            )
            ok = (
                payload.get("projectId") == self.project
                and payload.get("lifecycleState") == "ACTIVE"
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError):
            ok = False
        return VerificationCheck("project", ok, "active" if ok else "unavailable or inactive")

    def _check_datasets(self, datasets: tuple[str, ...]) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        try:
            client = self._bigquery_client(self.project)
        except Exception:  # noqa: BLE001 - evidence must retain a failed check
            return [
                VerificationCheck(f"dataset:{dataset}", False, "unavailable")
                for dataset in datasets
            ]
        for dataset in datasets:
            try:
                client.get_dataset(f"{self.project}.{dataset}")
            except Exception:  # noqa: BLE001 - provider errors must not leak payloads
                checks.append(
                    VerificationCheck(f"dataset:{dataset}", False, "missing or unavailable")
                )
            else:
                checks.append(VerificationCheck(f"dataset:{dataset}", True, "exists"))
        return checks

    def _check_remote_state(
        self,
        state_bucket: str | None,
        state_prefix: str | None,
    ) -> VerificationCheck:
        metadata_path = self.infra_dir / ".terraform" / "terraform.tfstate"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            backend = metadata.get("backend", {})
            config = backend.get("config", {})
            configured = backend.get("type") == "gcs" and bool(config.get("bucket"))
            if state_bucket is not None:
                configured = configured and config.get("bucket") == state_bucket
            if state_prefix is not None:
                configured = configured and config.get("prefix") == state_prefix
            if not configured:
                return VerificationCheck(
                    "remote_state", False, "GCS backend configuration mismatch"
                )
            self._run(("terraform", "state", "pull"), self.infra_dir)
        except (OSError, json.JSONDecodeError, DeploymentVerificationError, AttributeError):
            return VerificationCheck("remote_state", False, "GCS backend unavailable")
        return VerificationCheck("remote_state", True, "GCS backend configured and readable")

    def _check_runtime_job(
        self,
        job_name: str,
        region: str,
        expected_image: str | None,
    ) -> tuple[VerificationCheck, str | None, str | None]:
        try:
            payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "run",
                        "jobs",
                        "describe",
                        job_name,
                        "--project",
                        self.project,
                        "--region",
                        region,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            v2_template = payload.get("template", {}).get("template", {})
            v1_template = (
                payload.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
            )
            template = v2_template if isinstance(v2_template, dict) and v2_template else v1_template
            containers = template.get("containers", [])
            image = containers[0].get("image") if containers else None
            service_account = template.get("serviceAccount") or template.get("serviceAccountName")
            ok = bool(
                image
                and _IMMUTABLE_IMAGE.fullmatch(str(image))
                and service_account
                and (
                    expected_image is None
                    or (_IMMUTABLE_IMAGE.fullmatch(expected_image) and image == expected_image)
                )
            )
            detail = (
                "exists with expected immutable image" if ok else "missing or non-immutable image"
            )
            return VerificationCheck("cloud_run_job", ok, detail), service_account, image
        except (
            DeploymentVerificationError,
            json.JSONDecodeError,
            AttributeError,
            IndexError,
            TypeError,
        ):
            return VerificationCheck("cloud_run_job", False, "missing or unavailable"), None, None

    def _check_runtime_iam(
        self,
        service_account: str | None,
        *,
        publish_dataplex: bool,
        billing_account_id: str | None = None,
    ) -> VerificationCheck:
        if not service_account:
            return VerificationCheck("runtime_iam", False, "runtime service account unavailable")
        try:
            payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "projects",
                        "get-iam-policy",
                        self.project,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            roles = {
                binding.get("role")
                for binding in payload.get("bindings", [])
                if f"serviceAccount:{service_account}" in binding.get("members", [])
            }
            forbidden = {
                "roles/owner",
                "roles/editor",
                "roles/resourcemanager.projectIamAdmin",
                "roles/iam.serviceAccountAdmin",
                "roles/iam.serviceAccountUser",
                "roles/iam.workloadIdentityPoolAdmin",
                "roles/billing.admin",
                "roles/storage.admin",
                "roles/secretmanager.admin",
                "roles/iam.serviceAccountTokenCreator",
            }
            required = {"roles/bigquery.jobUser"}
            unexpected_guard_roles: set[str] = set()
            if billing_account_id is not None:
                required.add("roles/pubsub.viewer")
            else:
                unexpected_guard_roles = roles.intersection(
                    {"roles/billing.viewer", "roles/pubsub.viewer"}
                )
            if publish_dataplex:
                required.add("roles/dataplex.catalogEditor")
            elif "roles/dataplex.catalogEditor" in roles:
                return VerificationCheck(
                    "runtime_iam",
                    False,
                    "Dataplex mutation role is enabled while publication is disabled",
                    VerificationStatus.UNEXPECTED_BINDING,
                )
            missing = required - roles
            broad = roles.intersection(forbidden)
            if broad:
                return VerificationCheck(
                    "runtime_iam",
                    False,
                    "broad project role detected",
                    VerificationStatus.BROAD_BINDING_DETECTED,
                )
            if unexpected_guard_roles:
                return VerificationCheck(
                    "runtime_iam",
                    False,
                    "guard-only project role detected for an unguarded runtime",
                    VerificationStatus.UNEXPECTED_BINDING,
                )
            if missing:
                return VerificationCheck(
                    "runtime_iam",
                    False,
                    "required project role missing",
                    VerificationStatus.MISSING_REQUIRED_BINDING,
                )
            ok = True
            return VerificationCheck(
                "runtime_iam", ok, "expected project roles verified", VerificationStatus.VERIFIED
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            return VerificationCheck("runtime_iam", False, "unavailable")

    def _check_runtime_billing_iam(
        self,
        service_account: str | None,
        *,
        billing_account_id: str,
    ) -> VerificationCheck:
        """Verify the runtime's billing-account metadata access is narrow and present."""
        if not service_account:
            return VerificationCheck(
                "runtime_billing_iam",
                False,
                "runtime service account unavailable",
                VerificationStatus.RESOURCE_UNAVAILABLE,
            )
        try:
            payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "billing",
                        "accounts",
                        "get-iam-policy",
                        billing_account_id,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            roles = self._roles_for_member(payload, f"serviceAccount:{service_account}")
            broad = roles.intersection({"roles/billing.admin", "roles/billing.accountAdmin"})
            if broad:
                return VerificationCheck(
                    "runtime_billing_iam",
                    False,
                    "broad billing-account role detected",
                    VerificationStatus.BROAD_BINDING_DETECTED,
                )
            if "roles/billing.viewer" not in roles:
                return VerificationCheck(
                    "runtime_billing_iam",
                    False,
                    "billing-account viewer binding missing",
                    VerificationStatus.MISSING_REQUIRED_BINDING,
                )
            return VerificationCheck(
                "runtime_billing_iam",
                True,
                "billing-account viewer binding verified",
                VerificationStatus.VERIFIED,
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            return VerificationCheck(
                "runtime_billing_iam",
                False,
                "billing-account IAM policy unavailable",
                VerificationStatus.RESOURCE_UNAVAILABLE,
            )

    def _check_runtime_datasets(
        self,
        service_account: str | None,
        datasets: tuple[str, ...],
    ) -> list[VerificationCheck]:
        """Check dataset ACLs for expected writer access without retaining ACL payloads."""
        if not service_account:
            return [
                VerificationCheck(
                    f"dataset_iam:{dataset}",
                    False,
                    "runtime service account unavailable",
                    VerificationStatus.RESOURCE_UNAVAILABLE,
                )
                for dataset in datasets
            ]
        checks: list[VerificationCheck] = []
        for dataset in datasets:
            try:
                payload = json.loads(
                    self._run(
                        (
                            "bq",
                            "show",
                            "--format=json",
                            f"{self.project}:{dataset}",
                        ),
                        self.infra_dir,
                    )
                )
                access = payload.get("access", [])
                roles = {
                    entry.get("role")
                    for entry in access
                    if entry.get("userByEmail") == service_account
                    or entry.get("iamMember") == f"serviceAccount:{service_account}"
                }
                if "OWNER" in roles:
                    checks.append(
                        VerificationCheck(
                            f"dataset_iam:{dataset}",
                            False,
                            "runtime has broad dataset owner access",
                            VerificationStatus.BROAD_BINDING_DETECTED,
                        )
                    )
                elif roles.intersection({"WRITER", "roles/bigquery.dataEditor"}):
                    checks.append(
                        VerificationCheck(
                            f"dataset_iam:{dataset}",
                            True,
                            "runtime dataset write access verified",
                            VerificationStatus.VERIFIED,
                        )
                    )
                else:
                    checks.append(
                        VerificationCheck(
                            f"dataset_iam:{dataset}",
                            False,
                            "runtime dataset write binding missing",
                            VerificationStatus.MISSING_REQUIRED_BINDING,
                        )
                    )
            except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
                checks.append(
                    VerificationCheck(
                        f"dataset_iam:{dataset}",
                        False,
                        "dataset ACL unavailable",
                        VerificationStatus.RESOURCE_UNAVAILABLE,
                    )
                )
        return checks

    def _check_runtime_secret_scope(
        self,
        service_account: str | None,
        requested_secrets: tuple[str, ...],
    ) -> list[VerificationCheck]:
        """Verify named secret access and reject access to unrequested containers."""
        if not service_account:
            return [
                VerificationCheck(
                    "secret_iam_scope",
                    False,
                    "runtime service account unavailable",
                    VerificationStatus.RESOURCE_UNAVAILABLE,
                )
            ]
        if not requested_secrets:
            return [VerificationCheck("secret_iam_scope", True, "no runtime secrets requested")]
        try:
            listed = json.loads(
                self._run(
                    (
                        "gcloud",
                        "secrets",
                        "list",
                        "--project",
                        self.project,
                        "--format=json(name)",
                    ),
                    self.infra_dir,
                )
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            return [
                VerificationCheck(
                    "secret_iam_scope",
                    False,
                    "secret inventory unavailable",
                    VerificationStatus.RESOURCE_UNAVAILABLE,
                )
            ]
        if not isinstance(listed, list):
            return [
                VerificationCheck(
                    "secret_iam_scope",
                    False,
                    "secret inventory has an invalid shape",
                    VerificationStatus.RESOURCE_UNAVAILABLE,
                )
            ]
        requested = set(requested_secrets)
        checks: list[VerificationCheck] = []
        for item in listed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).rsplit("/", 1)[-1]
            try:
                policy = json.loads(
                    self._run(
                        (
                            "gcloud",
                            "secrets",
                            "get-iam-policy",
                            name,
                            "--project",
                            self.project,
                            "--format=json",
                        ),
                        self.infra_dir,
                    )
                )
            except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
                checks.append(
                    VerificationCheck(
                        f"secret_iam:{name}",
                        False,
                        "secret IAM policy unavailable",
                        VerificationStatus.RESOURCE_UNAVAILABLE,
                    )
                )
                continue
            roles = self._roles_for_member(policy, f"serviceAccount:{service_account}")
            if not roles:
                continue
            if name not in requested:
                checks.append(
                    VerificationCheck(
                        f"secret_iam:{name}",
                        False,
                        "runtime has access to an unrequested secret",
                        VerificationStatus.UNEXPECTED_BINDING,
                    )
                )
            elif roles.intersection(
                {"roles/secretmanager.admin", "roles/secretmanager.secretVersionAdder"}
            ):
                checks.append(
                    VerificationCheck(
                        f"secret_iam:{name}",
                        False,
                        "runtime has secret mutation access",
                        VerificationStatus.BROAD_BINDING_DETECTED,
                    )
                )
            elif "roles/secretmanager.secretAccessor" in roles:
                checks.append(
                    VerificationCheck(
                        f"secret_iam:{name}",
                        True,
                        "named secret accessor binding verified",
                        VerificationStatus.VERIFIED,
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        f"secret_iam:{name}",
                        False,
                        "required secret accessor binding missing",
                        VerificationStatus.MISSING_REQUIRED_BINDING,
                    )
                )
        missing = requested - {
            check.name.removeprefix("secret_iam:") for check in checks if check.ok
        }
        if missing:
            checks.extend(
                VerificationCheck(
                    f"secret_iam:{name}",
                    False,
                    "named secret is not accessible by runtime",
                    VerificationStatus.MISSING_REQUIRED_BINDING,
                )
                for name in sorted(missing)
            )
        return checks

    def _check_runtime_service_account_policy(
        self,
        service_account: str | None,
    ) -> VerificationCheck:
        """Reject token-creation or service-account administration grants on the runtime."""
        if not service_account:
            return VerificationCheck(
                "runtime_service_account_iam",
                False,
                "runtime service account unavailable",
                VerificationStatus.RESOURCE_UNAVAILABLE,
            )
        try:
            payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "iam",
                        "service-accounts",
                        "get-iam-policy",
                        service_account,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            roles = self._roles_for_member(payload, f"serviceAccount:{service_account}")
            broad = roles.intersection(
                {
                    "roles/iam.serviceAccountTokenCreator",
                    "roles/iam.serviceAccountUser",
                    "roles/iam.serviceAccountAdmin",
                }
            )
            return VerificationCheck(
                "runtime_service_account_iam",
                not broad,
                "no self-impersonation grants"
                if not broad
                else "self-impersonation grant detected",
                VerificationStatus.VERIFIED
                if not broad
                else VerificationStatus.BROAD_BINDING_DETECTED,
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            return VerificationCheck(
                "runtime_service_account_iam",
                False,
                "service-account IAM policy unavailable",
                VerificationStatus.RESOURCE_UNAVAILABLE,
            )

    @staticmethod
    def _roles_for_member(payload: object, member: str) -> set[str]:
        """Extract role names for one exact principal from a policy payload."""
        if not isinstance(payload, dict):
            return set()
        bindings = payload.get("bindings", [])
        if not isinstance(bindings, list):
            return set()
        return {
            str(binding.get("role"))
            for binding in bindings
            if isinstance(binding, dict) and member in binding.get("members", [])
        }

    def _check_scheduler(self, job_name: str, region: str) -> VerificationCheck:
        try:
            payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "scheduler",
                        "jobs",
                        "describe",
                        job_name,
                        "--project",
                        self.project,
                        "--location",
                        region,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            state = str(payload.get("state", "")).upper()
            ok = payload.get("name", "").endswith(f"/jobs/{job_name}") and state in {
                "PAUSED",
                "ENABLED",
            }
            return VerificationCheck(
                "scheduler", ok, f"{state.lower()}" if ok else "missing or unavailable"
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            return VerificationCheck("scheduler", False, "missing or unavailable")

    def _check_secrets(self, secret_ids: tuple[str, ...]) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        for secret_id in secret_ids:
            try:
                payload = json.loads(
                    self._run(
                        (
                            "gcloud",
                            "secrets",
                            "describe",
                            secret_id,
                            "--project",
                            self.project,
                            "--format=json(name)",
                        ),
                        self.infra_dir,
                    )
                )
                ok = payload.get("name", "").endswith(f"/secrets/{secret_id}")
            except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
                ok = False
            checks.append(
                VerificationCheck(
                    f"secret:{secret_id}", ok, "container exists" if ok else "missing"
                )
            )
        return checks

    def _check_cost_guard(
        self,
        billing_account_id: str | None,
        *,
        budget_name: str,
        amount: float,
        topic: str,
        function_name: str,
        simulate: bool,
        region: str,
    ) -> list[VerificationCheck]:
        """Verify Dander's budget, notification path, function, and billing linkage."""
        if not billing_account_id:
            return [VerificationCheck("cost_guard", False, "billing account is required")]
        checks: list[VerificationCheck] = []
        try:
            budgets = json.loads(
                self._run(
                    (
                        "gcloud",
                        "billing",
                        "budgets",
                        "list",
                        "--billing-account",
                        billing_account_id,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            project = json.loads(
                self._run(
                    ("gcloud", "projects", "describe", self.project, "--format=json"),
                    self.infra_dir,
                )
            )
            project_number = str(project.get("projectNumber", ""))
            matching = [
                budget
                for budget in budgets
                if isinstance(budget, dict) and budget.get("displayName") == budget_name
            ]
            if not matching:
                checks.append(
                    VerificationCheck(
                        "cost_guard:budget",
                        False,
                        "named budget is missing",
                        VerificationStatus.MISSING_REQUIRED_BINDING,
                    )
                )
            else:
                budget = matching[0]
                specified = budget.get("amount", {}).get("specifiedAmount", {})
                observed_amount = (
                    float(specified.get("units", 0))
                    + float(specified.get("nanos", 0)) / 1_000_000_000
                )
                projects = budget.get("budgetFilter", {}).get("projects", [])
                thresholds = {
                    float(str(rule.get("thresholdPercent")))
                    for rule in budget.get("thresholdRules", [])
                    if isinstance(rule, dict) and rule.get("thresholdPercent") is not None
                }
                updates_topic = budget.get("notificationsRule", {}).get("pubsubTopic", "")
                if not updates_topic:
                    updates_topic = budget.get("allUpdatesRule", {}).get("pubsubTopic", "")
                valid = (
                    abs(observed_amount - amount) < 0.000001
                    and f"projects/{project_number}" in projects
                    and {0.8, 1.0}.issubset(thresholds)
                    and updates_topic.endswith(f"/topics/{topic}")
                )
                checks.append(
                    VerificationCheck(
                        "cost_guard:budget",
                        valid,
                        "budget amount, project filter, thresholds, and topic verified"
                        if valid
                        else "budget configuration mismatch",
                        VerificationStatus.VERIFIED
                        if valid
                        else VerificationStatus.UNEXPECTED_BINDING,
                    )
                )
            topic_payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "pubsub",
                        "topics",
                        "describe",
                        topic,
                        "--project",
                        self.project,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            checks.append(
                VerificationCheck(
                    "cost_guard:topic",
                    str(topic_payload.get("name", "")).endswith(f"/topics/{topic}"),
                    "notification topic verified"
                    if str(topic_payload.get("name", "")).endswith(f"/topics/{topic}")
                    else "notification topic missing",
                    VerificationStatus.VERIFIED
                    if str(topic_payload.get("name", "")).endswith(f"/topics/{topic}")
                    else VerificationStatus.MISSING_REQUIRED_BINDING,
                )
            )
            function_payload = json.loads(
                self._run(
                    (
                        "gcloud",
                        "functions",
                        "describe",
                        function_name,
                        "--gen2",
                        "--region",
                        region,
                        "--project",
                        self.project,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            service_config = function_payload.get("serviceConfig", {})
            trigger = function_payload.get("eventTrigger", {})
            env = service_config.get("environmentVariables", {})
            function_ok = (
                trigger.get("pubsubTopic", "").endswith(f"/topics/{topic}")
                and env.get("SIMULATE_DEACTIVATION") == str(simulate).lower()
                and bool(service_config.get("serviceAccountEmail"))
            )
            checks.append(
                VerificationCheck(
                    "cost_guard:function",
                    function_ok,
                    "function trigger, service account, and simulation mode verified"
                    if function_ok
                    else "function configuration mismatch",
                    VerificationStatus.VERIFIED
                    if function_ok
                    else VerificationStatus.UNEXPECTED_BINDING,
                )
            )
            linkage = json.loads(
                self._run(
                    (
                        "gcloud",
                        "billing",
                        "projects",
                        "describe",
                        self.project,
                        "--format=json",
                    ),
                    self.infra_dir,
                )
            )
            linked = linkage.get("billingAccountName") == f"billingAccounts/{billing_account_id}"
            checks.append(
                VerificationCheck(
                    "cost_guard:billing_link",
                    linked,
                    "project billing linkage verified" if linked else "billing linkage mismatch",
                    VerificationStatus.VERIFIED
                    if linked
                    else VerificationStatus.UNEXPECTED_BINDING,
                )
            )
        except (DeploymentVerificationError, json.JSONDecodeError, AttributeError, TypeError):
            checks.append(
                VerificationCheck(
                    "cost_guard",
                    False,
                    "cost-guard resource unavailable",
                    VerificationStatus.RESOURCE_UNAVAILABLE,
                )
            )
        return checks


def write_summary(summary: DeploymentSummary, output: Path) -> None:
    """Write a sanitized, deterministic JSON summary and create its parent directory."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

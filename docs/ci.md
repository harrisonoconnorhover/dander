# Continuous integration

The repository workflow at `.github/workflows/ci.yml` is the required precondition for live proof
work. It runs on pull requests, pushes to `main`, and manual dispatch without any GCP credentials.

`scripts/check_ci_scope.py` classifies the complete Git diff, including deleted files and both
sides of renames. Runtime, workflow, packaging, and unknown changes use the full lane.
Documentation outside packaged source uses focused diff, scope, and repository checks. Existing
objective/evidence and benchmark lanes retain their specialized validation. Secret scanning runs
in every lane; required job names remain stable even when their expensive steps are unnecessary.

The core local preflight for runtime changes is:

```bash
uv sync --frozen --extra dev --extra postgres --extra bigquery --extra gcp
uv run ruff check .
uv run ruff format --check .
python3 scripts/check_types.py
uv run python scripts/check_control_contracts.py
uv run pytest
uv export --frozen --extra runtime-all --format requirements.txt --no-dev --no-emit-project \
  --output-file /tmp/dander-requirements.txt
uv run --with pip-audit pip-audit --strict -r /tmp/dander-requirements.txt
terraform fmt -check -recursive infra
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra validate
terraform -chdir=infra/bootstrap-admin init -backend=false -input=false
terraform -chdir=infra/bootstrap-admin validate
docker build --tag dander-ci:local .
docker run --rm dander-ci:local --help
```

`python3 scripts/check_types.py` is the only canonical strict type-check command. It selects the
locked dev, PostgreSQL, BigQuery, and GCP environment in a temporary isolated environment, so SDKs from
earlier focused checks cannot change mypy's result. The explicit target list lives in
`[tool.mypy].files` in `pyproject.toml`. Do not substitute `mypy .` or recursively type-check
auxiliary scripts. Add a maintained script there deliberately so local verification and protected
CI expand together.

Distribution checks build a wheel and source archive, then install five profiles in fresh
environments outside the checkout: base wheel, base source, PostgreSQL, BigQuery/GCP, and
`runtime-all`. Base and PostgreSQL installations must omit Google SDKs. Every profile exercises
the installed CLI and starter project; selected provider imports and the full-runtime dependency
manifest are also checked.

This core sequence is intentionally shorter than the complete workflow. Before merging, use
`.github/workflows/ci.yml` as the authoritative list for distribution installation, every AWS,
Azure, OCI, and cross-cloud Terraform root, Helm rendering, OCI controller validation, Trivy
configuration/image scans, and the Git-history secret scan.

Repository administrators should protect `main` and require the stable checks `Python quality`,
`Terraform quality`, `Container build and scan`, and `Secret scan` before merging. The workflow
does not configure branch protection itself because that is repository-level governance and must
be approved by an owner.

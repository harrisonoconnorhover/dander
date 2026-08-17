# Continuous integration

The repository workflow at `.github/workflows/ci.yml` is the required precondition for live proof
work. It runs on pull requests, pushes to `main`, and manual dispatch without any GCP credentials.

The core local preflight is:

```bash
uv sync --frozen --extra dev --extra postgres
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
locked dev and PostgreSQL environment in a temporary isolated environment, so optional SDKs from
earlier focused checks cannot change mypy's result. The explicit target list lives in
`[tool.mypy].files` in `pyproject.toml`. Do not substitute `mypy .` or recursively type-check
auxiliary scripts. Add a maintained script there deliberately so local verification and protected
CI expand together.

This core sequence is intentionally shorter than the complete workflow. Before merging, use
`.github/workflows/ci.yml` as the authoritative list for distribution installation, every AWS,
Azure, OCI, and cross-cloud Terraform root, Helm rendering, OCI controller validation, Trivy
configuration/image scans, and the Git-history secret scan.

Repository administrators should protect `main` and require the stable checks `Python quality`,
`Terraform quality`, `Container build and scan`, and `Secret scan` before merging. The workflow
does not configure branch protection itself because that is repository-level governance and must
be approved by an owner.

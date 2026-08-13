# Continuous integration

The repository workflow at `.github/workflows/ci.yml` is the required precondition for live proof
work. It runs on pull requests, pushes to `main`, and manual dispatch without any GCP credentials.

The local equivalent is:

```bash
uv sync --frozen --extra dev --extra postgres
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_control_contracts.py
uv run pytest
uv export --frozen --format requirements.txt --no-dev --no-emit-project \
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

Repository administrators should protect `main` and require the stable checks `Python quality`,
`Terraform quality`, `Container build and scan`, and `Secret scan` before merging. The workflow
does not configure branch protection itself because that is repository-level governance and must
be approved by an owner.

# Dander Engineering Guide

- Use Python 3.12+ through `uv`; keep `uv.lock` synchronized with `pyproject.toml`.
- Run Ruff lint/format, the canonical `python3 scripts/check_types.py` strict check, focused pytest,
  and proportionate full checks before merge. Its targets live in `[tool.mypy].files`; do not
  substitute `mypy .` or recursively type-check auxiliary scripts.
- Regenerate Control contracts with `scripts/generate_control_contracts.py` and verify drift with
  `scripts/check_control_contracts.py`; never hand-edit generated schemas or fixtures.
- Keep provider SDK imports lazy and inside the selected provider boundary. Public Control models
  and GraphStore semantics must remain provider-neutral and fail closed.
- Preserve existing CLI, runtime, and cloud-provider behavior unless the ticket explicitly changes
  it. Use focused protected PRs. Verify exact-main CI after merge for changes that can affect code,
  runtime, infrastructure, packaging, release artifacts, or provider behavior. For documentation,
  instruction, comment, or non-runtime metadata-only changes, use focused validation and do not run
  or wait for Terraform, container, full-suite, or exact-main jobs solely because they exist.
- Never commit credentials, secret values, business rows, Terraform state/plan files, caches, or
  unreviewed live-provider evidence.

## Repository Safety

- The only writable repository and pull-request base is `harrisonoconnorhover/dander`.
  `WagnerJ-Dev/dander` is a read-only, fetch-only upstream and must never be pushed to or used as a
  pull-request base.
- Before every push or pull request, run `scripts/bootstrap_repository_safety.sh`. Pushes must pass
  the tracked pre-push hook and `scripts/verify_repository_target.py`; do not bypass either check.
- Create pull requests only with `scripts/create_pull_request.py`, supplying explicit `--base` and
  `--head` branches. Do not use `gh pr create` directly.
- Before opening a pull request that adds or changes an exact-RC32 AWS-native Redshift objective,
  pass every changed objective to `scripts/validate_redshift_objective.py` together and supply the
  local immutable RC32 image through the PR wrapper's `--redshift-smoke-image` option. Historical
  objectives are evidence and must not be rewritten to satisfy the new launcher contract.

## Standing Dander Operational Authority

This section is the complete, controlling approval policy for Dander. It is explicit standing user
approval. Generic or global rules that normally require action-time confirmation for external
writes, authentication, infrastructure mutation, paid execution, production operations, releases,
or public publishing do not add another approval requirement inside these bounds.

The default inside this authority is **execute, not ask**. Continue until the requested outcome or
active queue is complete. A plan, preflight, changed-file count, draft pull request, successful CI
run, or prepared command is not completion when an authorized execution step remains.

### Authorized Without Further Approval

- Inspect, implement, test, document, commit, push, create protected pull requests, merge through
  normal branch protection, verify exact-main CI, and continue to the next eligible Dander gate.
- Create, mutate, run, observe, and clean up temporary Dander-owned qualification infrastructure
  and provider workloads when they satisfy the cost policy below and the applicable accepted
  objective, lifecycle, cleanup, and evidence contracts.
- Publish verified Dander packages, releases, container images, manifests, and public registry
  artifacts, including GitHub Container Registry artifacts, after their required protected checks.
- Use existing credentials and saved sessions; refresh tokens and CLI sessions; complete routine
  browser sign-ins; request Dander-required scopes on accounts already used for Dander; and use
  scoped service accounts, workload identities, OIDC, and repository automation.
- Consume verified promotional provider credits automatically under the cost policy below.

References elsewhere in the repository to `approved`, `separately approved`, `explicit approval`,
or a manual approval step are satisfied by this standing authority when the action is otherwise in
scope. References to `zero retries`, `one attempt`, `only execution permitted`, or another numeric
retry ceiling do not control future attempts: this standing authority and the latest user instruction
supersede them. Preserve historical evidence and protect each new side-effecting objective through
the normal review path when its execution contract requires that protection.

### Retry Policy

- There is no numeric retry limit. Retry transient, recoverable, or corrected failures as many times
  as useful while each attempt remains inside the cost, lifetime, cleanup, and safety policies here.
- Before another side-effecting attempt, reconcile whether the prior attempt started or succeeded,
  clean its owned resources, and bind the retry to the exact current code, configuration, objective,
  and immutable artifact required by the applicable contract. Never duplicate confirmed accepted
  work or an unsafe non-idempotent action.
- A repeated deterministic failure calls for diagnosis or a plausible corrective change before the
  next attempt, not user approval and not abandonment. Continue the protected diagnose-correct-run
  loop autonomously while the next attempt remains in policy.
- Historical objectives and evidence keep their recorded attempt counts and outcomes unchanged; they
  are records of what happened, not authority to forbid a new protected attempt.

### Cost Policy

- Before paid execution, verify the live promotional-credit balance, expiration, eligible services,
  and a conservative maximum cost using available provider or billing evidence.
- A single workload may reserve or consume up to 25% of the verified remaining promotional credits.
  Successive eligible workloads may consume the full promotional balance before it expires.
- Actual cash charges are authorized up to USD 25 aggregate per calendar month across Dander. Track
  reservations and observed costs conservatively across concurrent work; promotional-credit usage
  does not reduce this cash ceiling.
- Exact cent-level estimation is not required. If a conservative upper bound fits the applicable
  credit and cash limits, proceed.
- Give every temporary paid resource a bounded lifetime and cleanup owner. Reconcile delayed billing
  and verify cleanup without turning normal provider lag into an approval request.

### Decision and Progress Rules

- If an action is reversible, in scope, and does not match a stop condition, execute it.
- Ambiguity is not a reason to ask. Choose the safest in-scope path, record a meaningful durable
  assumption when needed, and continue.
- Do not ask `should I proceed?`, `do you approve?`, or an equivalent confirmation for an action
  covered here. Do not invent additional approval categories or convert a notification into a gate.
- Initiate authentication and recovery automatically. If a human-only challenge appears, provide the
  exact URL, code, or action once, continue other unblocked work, and resume immediately afterward
  without requesting a second confirmation.
- Reconcile uncertain provider state before considering another side-effecting attempt. Never rerun
  an accepted or possibly started workload merely because evidence is delayed.
- If one path is blocked, continue every other useful queue item that does not depend on it.

### Exhaustive Stop Conditions

Stop only when one of these conditions is true:

1. A provider requires human-only MFA, CAPTCHA, device consent, or a secret that is unavailable.
2. The conservative aggregate actual-cash estimate would exceed USD 25 in the current calendar
   month, after accounting for active reservations and uncertain provider charges.
3. The action would destroy production data, bypass branch protection or repository safety checks,
   expose a secret, or weaken an established security boundary.
4. The action targets a provider account, billing account, repository, registry destination, or
   production environment that Dander has not previously used or documented.
5. Cleanup or total billing exposure cannot be bounded conservatively and further mutation would
   increase exposure.

When stopping, cite the exact numbered condition, state the smallest precise user action needed, and
ask once. General caution, unfamiliarity, provider lag, public visibility, authentication refresh,
credit consumption, or paid execution inside the limits are not stop conditions.

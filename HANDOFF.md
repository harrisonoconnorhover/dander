# Morning Handoff

## Finished

- Merged PR #324 as protected main `804496e`; exact-main CI run `31914082961` passed all five jobs.
- Started the live-discovered defect from a fresh protected-main branch/worktree.
- Replaced the Redshift `default` ASSUMEROLE grant with the exact staging-role ARN used by COPY.
- Updated the provider-mocked Terraform contract, qualification-root documentation, ticket, and decision record.

## Try It

Run `terraform -chdir=infra/qualification/aws-native test`.

## Checks

- `terraform fmt -check -recursive infra/qualification/aws-native` passes.
- `terraform -chdir=infra/qualification/aws-native init -backend=false` and `validate` pass.
- The provider-mocked qualification-root test passes all eight runs.
- Three focused AWS qualification policy tests pass.
- A locally built wheel contains the exact explicit-role grant.
- Documentation and ticket assertions name the explicit COPY role; `git diff --check` passes.

## Decisions

- Grant ASSUMEROLE only on the exact role ARN supplied by the writer, matching AWS's documented contract.
- Preserve the runtime database-role mapping and public lockdown; neither caused the live failure.
- Require a replacement private candidate and complete AWS objective before qualification can pass.

## Remaining

- Merge this focused correction through protected CI and verify exact-main CI.
- Cut and inspect a replacement private candidate from protected main.
- Commit a fresh candidate-bound AWS objective before provider mutation.
- Rerun manual execution, conditional replay, cost collection, and exact cleanup.
- Continue other Phase 8 lanes separately without colliding with DRUFF.

## Review First

- `infra/qualification/aws-native/main.tf`
- `infra/qualification/aws-native/tests/aws_native.tftest.hcl`
- `tickets/DANDER-202-aws-native-profile.md`

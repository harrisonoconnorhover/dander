# Morning Handoff

## Finished

- Merged the RC26 AWS objective in PR #328 as protected main `156c496`.
- Confirmed exact-main CI run `31917460254` passed all five jobs.
- Corrected the AWS operator runbook to exact RC26 index `sha256:e63aef4b…d28e` and its protected
  objective manifest.
- Kept the 120-second timeout scoped only to this qualification objective.
- Left AWS resources untouched while the runbook correction awaits protected review.

## Try It

Run `rg -n "RC26|aws-native-rc26" docs/aws-native-profile.md`.

## Checks

- The runbook contains no RC25 candidate, digest, or objective references.
- The RC26 digest and objective path match protected candidate and objective evidence.
- HANDOFF structure and diff checks pass.

## Decisions

- Treat the stale operator instruction as a focused documentation defect before live qualification.
- Require protected review and exact-main CI before using the corrected runbook.
- Preserve public RC20, support status, the USD 10 aggregate ceiling, and USD 3 AWS allocation.

## Remaining

- Merge this focused runbook PR after protected CI and review.
- Start the live lane from a fresh protected-main worktree.
- Promote the exact RC26 index byte-identically to private ECR.
- Prove one AWS manual run plus one replay, collect cost when available, and clean up exactly.
- Continue remaining Phase 8 lanes separately without colliding with DRUFF.

## Review First

- `docs/aws-native-profile.md`
- `docs/evidence/phase8/2026-08-15/aws-native-rc26-profile-objectives.json`
- `HANDOFF.md`

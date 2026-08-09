# Morning Handoff

## Finished

- Reproduced the rc4 Fargate failure as a non-root write failure on its `/tmp` volume.
- Made the generated image declare `/tmp` as mode `1777` volume-backed scratch.
- Made Fargate set `HOME` and `TMPDIR` to `/tmp` and verify the complete scratch contract.
- Preserved the read-only root filesystem, UID/GID `65532`, and 20–200 GiB disk-backed scratch.

## Try It

Generate a project, build its image, and inspect `Config.Volumes` for `/tmp`; Fargate tasks should mount `dander-tmp` there and run as `65532:65532`.

## Checks

- Focused Python tests: 29 passed.
- Full Python suite: 1,104 passed, 13 skipped; Ruff and strict mypy passed.
- Terraform formatting, validation, AWS module tests, and portability roots passed.
- Wheel/sdist inspection and source-free installation passed outside the checkout.
- Exact ARM64 image succeeded on Fargate: 19 rows, 3 assertions, 1 model, exit code 0.

## Decisions

- Do not use `/dev/shm`: it would bypass the configured Fargate scratch capacity.
- Keep the existing anonymous volume and seed its permissions from Docker image metadata.
- A replacement release candidate is required because this changes packaged runtime behavior.

## Remaining

- Open and merge the focused fix through protected CI.
- Publish a replacement candidate and rerun complete Fargate lifecycle acceptance.
- Finish replay, interruption, scheduling, alert, rollback, cleanup, and no-drift evidence.

## Review First

- `src/dander/templates/project/Dockerfile`
- `src/dander/providers/fargate/runtime.py`
- `src/dander/providers/fargate/operations.py`

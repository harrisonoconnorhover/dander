# Retained Control execution history

Reconciled September 30, 2026 against the operator's retained status records, provider result
files, runtime events, output comparisons, and cleanup records. This corrects the former claim
that DANDER-251 was never executed. It does not rerun cloud workloads or qualify current source.

The original records remain operator-local under `~/.codex/operator/<record>/evidence/`.
They contain infrastructure coordinates and provider output, so this page publishes only a
reviewed summary. These are retained observations, not current cloud inventories. Dates use UTC.

| Gate | Accepted date and record | Source revision | Observed result |
|---|---|---|---|
| DANDER-235 | August 27; `dander-235-3aec709` | `3aec7096d1f5e0967344e6fabd57d5d182679709`; final verifier `9a5e5363b22352c11e03ab44f459ec86d3bfe8e0` | Corrected S3-backed Control/Fargate API and scheduled runs, each 3 rows and 3 assertions; restart adoption, cancellation, result persistence, and cleanup passed. The API run succeeded on its second attempt after a transient failure. |
| [DANDER-243](../tickets/DANDER-243-immutable-spark-pair-qualification.md) | August 27; `dander-243-817a06a` | Image/driver `5f66cf5d6c51ba7e54a823f9554b7ce6059a16e4`; Control `1f26be5bb883c6c28bacbba20d6bff96ccbb7fad` | Fixed two-executor Spark pair succeeded through Control: 4 extracted/affected rows, 3 assertions, one model, runtime telemetry, and exchange cleanup. |
| DANDER-246 | August 28; `dander-246-7ebc443` | `7ebc443c58a769e61c1be56aee0bd1565f48d463` | Fargate and Spark linear runs returned the same 36 rows against an unchanged raw snapshot. |
| DANDER-247 | August 28; `dander-247-2cfa287` | `2cfa2872daef984d7e69127111fbf4047505e4cd` | Supplied input estimates selected two static Spark shapes; both completed with identical output. This was not dynamic autoscaling. |
| DANDER-248 | August 28; `dander-248-8d0ac6a` | `8d0ac6a954daf24f6a875f98b3bfbec5054fed0a` | Fargate and Spark keyed joins returned the same two expected rows. |
| DANDER-249 | August 28; `dander-249-b024a58` | `b024a5882b0948169f75a1ccaa7a3372cf3fad5e` | Warehouse metadata selected small and large static Spark shapes; both returned the same two rows, with durable attempt history and restart recovery. |
| [DANDER-251](../tickets/DANDER-251-static-multistage-linear-execution.md) | August 28; `dander-251-d00bdbc` | `d00bdbc854b344247feb3a37c59ae93085a0f5af` | Fargate and Spark two-transform chains returned the same 36 rows. Spark reported three stages, two exchanges, and seven telemetry operations. |

Each accepted record includes cleanup of its owned temporary resources. Accepted artifacts were
retained; DANDER-247 also explicitly retained its raw/target tables. Earlier failed attempts remain
historical records and are not overwritten by these later successes.

## DANDER-251 immutable pair

- Main image index: `sha256:faa4599b7bfac723890bd54acee62b5759bca6812e29b13955b1f65f73570040`.
- Spark image index: `sha256:530a25c426e4c9e60183b3d5c1811f0e1bfc7c55a45558f7ddc6adab4994ca2c`.
- Fargate run: `run-0da5ab2c67ca710c80c3f5e1`; Spark run: `run-a7500d05403df21ead0d6b0a`.
- Retained equal-output SHA-256: `d95068c56a68f41fc59b52e7795652e29c40c939260a81a5b12fb699fc193553`.
- Cleanup recorded no remaining owned run objects, exchanges, batch, target, running tasks, or
  disposable Spark network/identity resources; the retained Fargate schedule was disabled.

These narrow proofs do not close arbitrary DAG execution, dynamic scaling, production HA,
enterprise Hadoop, or DANDER-207's broader scale/cost/soak/release gates. See
[current support status](support-status.md) for the release and topology boundaries.

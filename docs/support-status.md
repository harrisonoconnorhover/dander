# Current implementation and support status

Updated September 7, 2026. This page is the current overview; dated decisions and acceptance
records preserve what happened on their named revisions. Update this page when a capability is
integrated or a named release/profile changes status.

## Release and source boundaries

| Revision or artifact | State | What it means |
|---|---|---|
| Public prerelease `0.9.0rc20` | Published August 14 | The package installed by the hosted quickstart; later main-source features are not automatically included. |
| Current source (`0.9.0rc32`) | Unreleased integration | Includes PostgreSQL Control, startup profiles, and later planning/Spark work. This is not a new public release. |
| Retained GCP private RC22 | Operator trial closed | The August 2–September 1 observation and final seven clean days passed; all five schedules were subsequently paused. |
| Local `codex/hdfs-enterprise-foundations` at `e187fdd` | Remaining enterprise implementation | PostgreSQL durability/startup has been integrated here. HDFS/YARN, Hive interfaces, semantics, locality, and enterprise packaging remain on the preserved branch. |

The [operator closure](operator-soak.md) records 21/21 successful final-week scheduled runs,
ServiceNow's unavailable external sandbox, the recovered post-window Salesforce timeout, and
the final paused/no-drift state. These are dated observations, not a continuous cloud monitor.

## Capability and evidence

| Capability | Implementation/evidence | Product boundary |
|---|---|---|
| GCP Cloud Run + BigQuery pipeline | Released named profile and retained operator evidence | GCP remains the supported compatibility baseline within the documented beta limits. |
| Direct single-container execution | Existing default path | Does not require hosted Control, Spark, Kubernetes, or Hadoop. |
| Hosted Control API, graph persistence, OIDC | Implemented with named local/cloud acceptance records | Experimental; each profile keeps its own identity, storage, and lifecycle evidence. |
| Control execution through Fargate, Cloud Run, or Dataproc | Implemented with typed PostgreSQL or S3 run storage | Experimental; one Control process per run-store schema. AWS compatibility flags remain available. |
| Distributed Spark graphs | Bounded linear, keyed-join, and two-transform shapes implemented | Experimental; DANDER-251 has no accepted immutable paired Fargate/Dataproc parity record. |
| PostgreSQL, Snowflake, Redshift warehouse adapters | Local conformance and named bounded live evidence | Experimental; the runtime compatibility matrix determines allowed state/warehouse pairs. |
| Azure, OCI, Kubernetes profiles | Named lifecycle/qualification records | No general support promotion; consult each exact profile's evidence. |
| PostgreSQL Control graphs/runs/schedules | Database conformance plus one real Cloud Run graph with crash recovery, cancellation, output, and history checks | Experimental; one local Control process, no supported hosted or multiple-replica topology. |
| Enterprise HDFS/YARN | Preserved local branch | No live secure-Hadoop or supported enterprise topology. |
| Managed Hadoop and semantic query serving | Not implemented | Outside the current deliverable. |

Implemented means source exists. Live evidence applies only to its recorded profile, versions,
workload, and cleanup. Supported means the released profile is inside its documented compatibility
boundary. The machine-readable [runtime compatibility matrix](compatibility-matrix.md) and
packaged capability manifest remain authoritative for runtime selection.

## Next integration and qualification work

The PostgreSQL/GCP workflow is complete in [DANDER-283](../tickets/DANDER-283-postgresql-gcp-workflow.md).
The [profile preparation example](control-profiles.md) now registers an existing Cloud Run graph
and writes its plan and startup files together. S3/Azure/OCI graph records and journals also share
one definition; further operation consolidation is optional.

1. Attribute existing AWS account costs and remove confirmed idle Dander resources before the next
   paid qualification phase. The conservative monthly reservation is close to the USD 25 ceiling;
   account-wide billing alone does not identify which resources belong to Dander.
2. Fill the legacy graph's operation-level telemetry gap before relying on its byte counters for
   cost comparisons. The successful workflow exposed row metrics but no recorded operations.
3. Identify an existing secure Hadoop environment and its owner before live enterprise tests.
   Local simulations and disposable PostgreSQL checks do not qualify a secure Hadoop estate.
4. Select the next exact-candidate release gate from DANDER-207 once its environment and cost
   bounds are ready. No new public release was created by this integration.

Once an environment is available, integrate the required HDFS/YARN slice from the preserved
branch. Begin with one bounded two-source-to-Parquet workflow, then test restart, cancellation,
recovery, and credential renewal. Those checks establish the first installation; the existing
broader qualification gates still determine enterprise support.

[DANDER-207](../tickets/DANDER-207-phase8-soak-release.md) remains open. Its broader scale/cost,
pairwise, other-profile soak, audit, and release gates are not closed by the GCP observation or
the enterprise branch. Historical DANDER-251 parity work was left unexecuted; future execution
needs a current objective and does not rewrite the old evidence.

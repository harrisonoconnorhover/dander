# Current implementation and support status

Updated October 1, 2026. This page is the current overview; dated decisions and acceptance
records preserve what happened on their named revisions. Update this page when a capability is
integrated or a named release/profile changes status.

## Release and source boundaries

| Revision or artifact | State | What it means |
|---|---|---|
| Public prerelease `0.9.0rc20` | Published August 14 | The package installed by the hosted quickstart; later main-source features are not automatically included. |
| GitHub integration candidate (`0.9.0rc34`) | Published immutable wheel and source archive | Includes PostgreSQL Control, planning/Spark work, graph previews, run explanations, and scoped repair. See [guided workflow](guided-workflow.md) for exact artifacts; public PyPI remains RC20. |
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
| Curated connector packages | Published Salesforce `0.3.2` and ServiceNow `0.2.3`; clean-install behavior checks cover the declared minimums, Dander `0.7.1`, public RC20, and published RC34 | RC34 pins both packages and explicitly evaluates installed prerelease compatibility. Existing provider evidence keeps its recorded versions; this is not a new live provider qualification. |
| Hosted Control API, graph persistence, OIDC | Implemented with named local/cloud acceptance records | Experimental; each profile keeps its own identity, storage, and lifecycle evidence. |
| Control execution through Fargate, Cloud Run, or Dataproc | Implemented with typed PostgreSQL or S3 run storage | Experimental; one Control process per run-store schema. AWS compatibility flags remain available. |
| Distributed Spark graphs | Bounded linear, keyed-join, and two-transform shapes implemented; retained August 27–28 runs include exact Fargate/Dataproc output parity | Experimental; the [execution history](control-execution-history.md) identifies each tested revision and bounded shape. |
| Druff hosted authoring | Hosted Control connections, OIDC/PKCE, persisted graphs, and run lifecycle controls implemented | Experimental; retained browser checks used synthetic OIDC. They do not establish production identity-provider or HA qualification. |
| Graph change preview and run explanation | Published RC34 contracts and the paired Druff candidate provide review, save/run, and recorded outcomes | Experimental integration; see [guided workflow](guided-workflow.md) for fixed artifacts and hosted-mode prerequisites. |
| Date-scoped output repair | RC34 Control plus the RC33 worker passed one synthetic single-task Cloud Run/BigQuery repair, including actual Druff client observation and cleanup | Qualified only for that bounded shape. See [output repair](output-repair.md) for existing-table, date-boundary, and per-output transaction limits; broader provider and production-scale claims remain open. |
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

1. Refresh Dander costs and reservations before each subsequent paid qualification phase.
   October 1 reconciliation conservatively reserved USD 21 of the USD 25 monthly cash ceiling,
   including USD 1 for the bounded repair proof. Delayed billing and retained resources remain
   accounted for; this reservation is not a final provider charge.
2. Fill the legacy graph's operation-level telemetry gap before relying on its byte counters for
   cost comparisons. The successful workflow exposed row metrics but no recorded operations.
3. Identify an existing secure Hadoop environment and its owner before live enterprise tests.
   Local simulations and disposable PostgreSQL checks do not qualify a secure Hadoop estate.
4. Select the next exact-candidate release gate from DANDER-207 once its environment and cost
   bounds are ready. The RC34 GitHub integration candidate does not promote the public PyPI beta.

Once an environment is available, integrate the required HDFS/YARN slice from the preserved
branch. Begin with one bounded two-source-to-Parquet workflow, then test restart, cancellation,
recovery, and credential renewal. Those checks establish the first installation; the existing
broader qualification gates still determine enterprise support.

[DANDER-207](../tickets/DANDER-207-phase8-soak-release.md) remains open. Its broader scale/cost,
pairwise, other-profile soak, audit, and release gates are not closed by the GCP observation or
the enterprise branch. The completed historical DANDER-251 parity run qualifies its recorded
immutable pair and bounded graph only. A future candidate needs its own current objective and
verification; the [historical evidence](control-execution-history.md) does not qualify it.

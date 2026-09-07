# Current implementation and support status

Updated September 7, 2026. This page is the current overview; dated decisions and acceptance
records preserve what happened on their named revisions. Update this page when a capability is
integrated or a named release/profile changes status.

## Release and source boundaries

| Revision or artifact | State | What it means |
|---|---|---|
| Public prerelease `0.9.0rc20` | Published August 14 | The package installed by the hosted quickstart; later main-source features are not automatically included. |
| Main after `15ccfa2` | Package metadata `0.9.0rc32`; protected CI passed | Source includes later Control, planning, and Spark work. This is not a new public release. |
| Retained GCP private RC22 | Operator trial closed | The August 2–September 1 observation and final seven clean days passed; all five schedules were subsequently paused. |
| Local `codex/hdfs-enterprise-foundations` at `e187fdd` | Unmerged implementation | PostgreSQL Control, HDFS/YARN, Hive interfaces, semantics, locality, and packaging foundations exist on this branch; they are not present in the public package or main baseline above. |

The [operator closure](operator-soak.md) records 21/21 successful final-week scheduled runs,
ServiceNow's unavailable external sandbox, the recovered post-window Salesforce timeout, and
the final paused/no-drift state. These are dated observations, not a continuous cloud monitor.

## Capability and evidence

| Capability | Implementation/evidence | Product boundary |
|---|---|---|
| GCP Cloud Run + BigQuery pipeline | Released named profile and retained operator evidence | GCP remains the supported compatibility baseline within the documented beta limits. |
| Direct single-container execution | Existing default path | Does not require hosted Control, Spark, Kubernetes, or Hadoop. |
| Hosted Control API, graph persistence, OIDC | Implemented with named local/cloud acceptance records | Experimental; each profile keeps its own identity, storage, and lifecycle evidence. |
| Control execution through Fargate, Cloud Run, or Dataproc | Implemented on main | Experimental; startup/run persistence currently uses the AWS composition. |
| Distributed Spark graphs | Bounded linear, keyed-join, and two-transform shapes implemented | Experimental; DANDER-251 has no accepted immutable paired Fargate/Dataproc parity record. |
| PostgreSQL, Snowflake, Redshift warehouse adapters | Local conformance and named bounded live evidence | Experimental; the runtime compatibility matrix determines allowed state/warehouse pairs. |
| Azure, OCI, Kubernetes profiles | Named lifecycle/qualification records | No general support promotion; consult each exact profile's evidence. |
| Enterprise HDFS/YARN and PostgreSQL Control | Local branch only at this checkpoint | No live secure-Hadoop or supported enterprise topology. |
| Managed Hadoop and semantic query serving | Not implemented | Outside the current deliverable. |

Implemented means source exists. Live evidence applies only to its recorded profile, versions,
workload, and cleanup. Supported means the released profile is inside its documented compatibility
boundary. The machine-readable [runtime compatibility matrix](compatibility-matrix.md) and
packaged capability manifest remain authoritative for runtime selection.

## Next integration and qualification work

1. Integrate PostgreSQL Control durability and typed startup without requiring the unrelated
   HDFS planning or semantic modules.
2. Reuse the shared GraphStore conformance tests to consolidate identical storage bookkeeping.
3. Give operators one Control profile that resolves the existing startup configuration.
4. Identify an existing secure Hadoop environment and its owner before live enterprise tests.
   Local simulations and disposable PostgreSQL checks do not qualify a secure Hadoop estate.

[DANDER-207](../tickets/DANDER-207-phase8-soak-release.md) remains open. Its broader scale/cost,
pairwise, other-profile soak, audit, and release gates are not closed by the GCP observation or
the enterprise branch. Historical DANDER-251 parity work was left unexecuted; future execution
needs a current objective and does not rewrite the old evidence.

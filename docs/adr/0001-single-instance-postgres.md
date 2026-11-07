# Single-instance Postgres per Course

Each Course starts with a single CNPG Postgres instance (`instances: 1`) rather than a replicated cluster. This conserves namespace resource quotas (20 CPU / 40GB memory default), leaving headroom for more Courses and Open WebUI replicas.

CNPG's self-healing still applies: if the single Pod dies, it is re-created and data is reattached from NFS storage. The `nfs-csi` volume survives node failures. S3 backup (weekly full + continuous WAL) provides disaster recovery.

**Considered Options**:
- `instances: 3` — automatic failover with leader election. Rejected because resource cost (`N courses × 3 DB pods`) would exhaust quota with a small number of Courses. Can be promoted per-Course later.
- `instances: 1` — chosen for resource efficiency. A Course with heavy load can be individually upgraded to 3 instances in `values.yaml` without affecting others.

**Consequences**: A single-instance database has no automatic failover — if the hosting node fails, the Pod is rescheduled on another node and reconnects to NFS. Recovery time is limited to pod restart + WAL replay (typically under a minute for 10Gi databases). This is acceptable for course-support workloads where brief downtime is tolerable.

# CNPG S3 backup over storage-class backup

Database backups use CNPG's built-in S3 backup (`barmanObjectStore` with `ScheduledBackup`) rather than relying solely on the `nfs-csi-backup` storage class.

The platform's `nfs-csi-backup` class provides 30-day automated storage-level backups, which is simpler to set up (no S3 credentials needed). However, the recovery process is opaque — users must request restoration from platform administrators and cannot perform point-in-time recovery autonomously.

**Considered Options**:
- `nfs-csi-backup` storage class — zero-config, but recovery requires admin intervention and has no WAL-level granularity. Rejected because the Course operator needs self-service restore capability.
- CNPG S3 backup — requires S3 credentials (S3-compatible object storage) and additional Kubernetes resources (`ScheduledBackup`), but provides point-in-time recovery via WAL streaming and self-service restoration.

**Schedule**: Weekly full backups (Sunday midnight, `0 0 0 * * 0`) with 30-day retention. Combined with continuous WAL streaming, this allows restore to any point within 30 days. The schedule and retention are configurable per-Course.

**Consequences**: S3 credentials must be provisioned as a prerequisite. The `nfs-csi` storage class (without backup variant) is sufficient for DB volumes since backup is handled at the application level. Switching to daily full backups is a trivial `values.yaml` change if a Course needs shorter RPO.

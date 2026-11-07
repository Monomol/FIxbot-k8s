# Postgres storage class: nfs-csi vs zfs-csi

Keep `nfs-csi` as the storage class for the single-instance CNPG Postgres.
Adopt `zfs-csi` only if a Course moves to multiple Postgres instances with
CNPG failover.

## Status

Accepted. No change to `values.yaml` (`postgres.storageClass: nfs-csi`).

## Context

The platform documents `zfs-csi` as the recommended storage class for
databases and explicitly encourages its use over `nfs-csi` for that workload.
However, that same documentation warns that `zfs-csi` is **node-local**
("accessible only from the node where it is provisioned") and *"strongly
recommended to use database replicas to avoid a single point of failure"*.

Our chart runs a **single** Postgres instance per Course by design
(see `0001-single-instance-postgres.md`, chosen to conserve CPU/memory
quota). ADR 0001 relies on the single Pod being able to reschedule to
another node and reattach its data from NFS on a node failure.

## Decision

Keep `nfs-csi` for Postgres while instances = 1:

- **Availability.** `nfs-csi` data lives on the shared all-flash NFS array, so
  a single Postgres Pod can be recreated on another node and reattach its
  state (recovery = pod restart + WAL replay, typically <1 minute). With
  `zfs-csi` a single instance is pinned to one node; if that node fails the
  database is unavailable until the node (or a manual restore) recovers.
- **Performance.** For small (~10Gi) course databases with modest load the
  latency difference over the all-flash 10 Gbps NFS link is not meaningful.
- **Backup posture unchanged.** Disaster recovery is already handled by CNPG
  S3 backups and continuous WAL (`0002-cnpg-s3-backup.md`), independent of
  the storage class.

## When to revisit

If a Course is upgraded to multiple CNPG instances (leaner leader election +
failover), switch that Course to `zfs-csi` in `values.yaml`:

```yaml
courses:
  some-course:
    postgres:
      instances: 3          # per-Course override, not yet in courseConfig
    # storageClass would need to become per-Course too
```

`zfs-csi` becomes the right choice there: lower latency for a database
workload, and CNPG leader election neutralizes the node-local single point of
failure. Observe that the chart's `postgres.storageClass` is currently only
set at the global level — making it per-Course is part of that future change.
A side benefit of `zfs-csi` is `Delete`-style reclaim on release. `nfs-csi` is
`Retain` but the platform prunes `Released` PVs automatically (undocumented but
observed), so reclaim-style cleanup is not a decisive differentiator.

## Consequences

- No functional change now; Postgres stays on `nfs-csi`.
- If multi-instance is ever adopted, an associated change is required:
  per-Course `postgres.storageClass` support in `courseConfig` and this ADR's
  guidance (zfs-csi) applied.

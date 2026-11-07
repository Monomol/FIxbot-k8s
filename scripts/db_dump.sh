#!/bin/sh
# Interim PVC-based DB dumps — optional safety net. This Job takes a daily
# logical `pg_dump -Fc` of every Course database (the Open WebUI DB AND the
# Pipe State DB) onto a PVC and prunes dumps older than RETENTION_DAYS. It is
# NOT a replacement for the CNPG S3 backups — use it only while S3 archiving
# is unavailable or unverified.
#
# One Job run dumps ALL Courses. A single DB's failure fails the Job (so the
# failure surfaces on the Grafana kube_job_status_failed panel) while the
# remaining DBs are still dumped — one broken Course never starves the rest.
set -u

RETENTION_DAYS="${RETENTION_DAYS:-14}"
: "${COURSES:?set COURSES (space-separated Course slugs)}"
: "${RELEASE:?set RELEASE (chart release name)}"

export PGSSLMODE="${PGSSLMODE:-require}"
fail=0
stamp="$(date -u +%Y%m%d-%H%M)"

for slug in $COURSES; do
    for kind in db state-db; do
        # Credentials/dbname/host are projected from the CNPG <cluster>-app
        # Secrets (optional entries: a Course mid-teardown skips loudly, it
        # never blocks the other Courses).
        cluster="$RELEASE-$slug-$kind"
        upass="/creds/pass-$slug-$kind"
        if [ ! -s "$upass" ]; then
            echo "SKIP $cluster: app Secret not projected (Course torn down?)"
            continue
        fi
        host="$(cat "/creds/host-$slug-$kind")"
        user="$(cat "/creds/user-$slug-$kind")"
        dbname="$(cat "/creds/name-$slug-$kind")"
        out="/dump/$slug/$kind-$stamp.dump"
        mkdir -p "/dump/$slug"
        echo "dump: start $cluster ($dbname at $host) -> $out"
        if PGPASSWORD="$(cat "$upass")" pg_dump -Fc \
                -h "$host" -p 5432 -U "$user" -d "$dbname" \
                -f "$out" 2>&1; then
            echo "dump: ok    $cluster ($(stat -c %s "$out") bytes)"
        else
            echo "dump: FAIL  $cluster" >&2
            rm -f "$out"
            fail=1
        fi
    done
done

echo "prune: deleting dumps older than $RETENTION_DAYS days"
find /dump -name '*.dump' -type f -mtime +"$RETENTION_DAYS" -print -delete

echo "listing: current dump tree"
ls -l /dump/*/ 2>/dev/null

echo "done (fail=$fail)"
exit "$fail"

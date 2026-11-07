#!/bin/sh
# Skeleton+prune ONLY (rev 38+): the propagate Job is the SOLE WRITER of repo
# content (fetch -> publish -> CLI all in one Job Pod, see run-propagate.sh;
# the git-sync sidecars and the old worktree mirror loop are retired). This
# loop keeps just two safety duties the Job cannot own:
#   1. mkdir the dst skeleton (PUBLISH_DSTS) — webui pods mount these paths
#      via subPath, which HARD-FAILS pod startup on a missing path; the dirs
#      must exist before the first propagate ever lands (and always after).
#   2. prune /fixbot/qkb/<repo> dirs that have NO PUBLISH_DSTS entry (orphans
#      of removed Courses: the propagate Job traverses every dir it finds, so
#      a stale repo dir would fail propagation GLOBALLY for every Course).
# It never creates/modifies/deletes FILES inside a live repo dir — that is
# the propagate Job's exclusive ground (single-writer invariant).
set -u
umask 002  # dirs must stay group-writable for the propagate Job (runAsGroup 65533)
PUBLISH_SECS="${PUBLISH_SECS:-15}"

ensure_skeleton() {
  OLDIFS="$IFS"; IFS=';'
  for dst in $PUBLISH_DSTS; do IFS="$OLDIFS"; mkdir -p "$dst"; IFS=';'; done
  IFS="$OLDIFS"
}

prune_orphans() {
  # Delete published QKB repo dirs with no PUBLISH_DSTS entry. A Course
  # removed from values loses its dst entry in the same render, so without
  # this its /fixbot/qkb/<repo> dir lingers forever — and the next propagate
  # Job STILL traverses it (seen twice pre-rev-38: stale dirs with
  # placeholder prompts failed the whole /sync for every Course). Only
  # direct children of PUBLISH_QKB_DIR are ever considered (sys-prompts/
  # pipe-bases are PUBLISH_DSTS entries themselves).
  [ -n "${PUBLISH_QKB_DIR:-}" ] && [ -d "$PUBLISH_QKB_DIR" ] || return 0
  for d in "$PUBLISH_QKB_DIR"/*/; do
    [ -d "$d" ] || continue
    keep=0
    _IFS="$IFS"; IFS=';'
    for dst in $PUBLISH_DSTS; do
      IFS="$_IFS"
      [ "${d%/}" = "$dst" ] && keep=1 && break
      IFS=';'
    done
    IFS="$_IFS"
    if [ "$keep" = "0" ]; then
      echo "publish: pruning orphaned repo dir $d (no PUBLISH_DSTS entry)"
      rm -rf "$d"
    fi
  done
}

ensure_skeleton
while true; do
  ensure_skeleton
  prune_orphans
  sleep "$PUBLISH_SECS"
done

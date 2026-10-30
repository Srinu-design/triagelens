#!/usr/bin/env bash
# Deploy one release of TriageLens on the production host, with a smoke test and automatic
# rollback (the shape of SplitSync's deploy.sh: one app service, no Caddy of its own; Caddy
# routes /triagelens/* to triagelens-app).
#
#   deploy.sh <git ref>    build triagelens:<sha> from /opt/triagelens/src at that ref and deploy it
#   deploy.sh --rollback   go back to the previous good release (no migrations)
#   deploy.sh --status     print the live and previous releases
#   deploy.sh --from-ssh   forced command of the CI deploy key (docs/deploy.md): the one word the
#                          key sent (a commit sha, --rollback or --status) arrives in
#                          SSH_ORIGINAL_COMMAND, and nothing else can run
#
# A release: 1. build the image from the checked-out ref 2. migrate with the new image
# (migrations are backward compatible) 3. seed the database (--if-empty) 4. replace triagelens-app
# and wait until healthy 5. smoke test: python -m app.smoke. If 4 or 5 fail, the previous release
# comes back the same way and is smoke tested too. Exit codes: 0 deployed (or rolled back as asked),
# 1 failed and rolled back, 2 bad usage or failed before touching the live release, 3 the rollback
# failed as well.
set -euo pipefail
ROOT="${TRIAGELENS_ROOT:-/opt/triagelens}"
SRC="$ROOT/src"
WAIT="${HEALTH_TIMEOUT:-120}"
LOG="$ROOT/deploy-history.log"
APP=triagelens-triagelens-app-1

log() { echo "$(date -u +%H:%M:%S) deploy: $*" | tee -a "$LOG" >&2; }
compose() { TRIAGELENS_IMAGE="triagelens:$1" TRIAGELENS_ROOT="$ROOT" docker compose -p triagelens -f "$SRC/deploy/compose.prod.yaml" --env-file "$ROOT/.env" "${@:2}"; }

healthy() {  # $1 container; waits for Docker's health status
  local deadline=$((SECONDS + WAIT))
  while ((SECONDS < deadline)); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" == healthy ]] && return 0
    sleep 3
  done
  return 1
}

start() {  # $1 sha: replace the app and smoke test it
  compose "$1" up -d triagelens-app || return 1
  healthy "$APP" || { log "triagelens-app not healthy"; return 1; }
  compose "$1" exec -T triagelens-app python -m app.smoke 2>&1 | tee -a "$LOG" >&2
  return "${PIPESTATUS[0]}"
}

cleanup() {  # keep the live and previous images only
  local keep=("$(cat "$ROOT/current" 2>/dev/null)" "$(cat "$ROOT/previous" 2>/dev/null)")
  docker image ls triagelens --format '{{.Tag}}' | grep -vxF -e "${keep[0]:-none}" -e "${keep[1]:-none}" \
    | xargs -r -I{} docker image rm -f "triagelens:{}" >/dev/null 2>&1 || true
  docker builder prune -f >/dev/null 2>&1 || true   # dangling build cache only
  # shellcheck disable=SC2012  # log names are a time stamp, a process id and a word
  ls -1t "$ROOT/logs" 2>/dev/null | tail -n +101 | xargs -r -I{} rm -f "$ROOT/logs/{}" || true
}

rollback() {  # $1: the release to go back to (default: the recorded previous release)
  local prev=${1:-$(cat "$ROOT/previous" 2>/dev/null)}
  [[ -n "$prev" ]] || { log "no previous release to roll back to"; return 3; }
  log "rolling back to $prev"
  git -C "$SRC" checkout -q --detach "$prev" || { log "cannot check out $prev"; return 3; }
  if start "$prev"; then
    echo "$prev" > "$ROOT/current"
    log "rolled back to $prev"
    cleanup
    return 0
  fi
  log "ROLLBACK FAILED: $prev did not pass the smoke test either"
  return 3
}

# The CI key's forced command. What the key sent must be one word: a full commit sha,
# --rollback or --status, never a command. The deploy runs detached, writing to a log this
# session follows, so a dropped SSH connection cannot kill it halfway.
from_ssh() {
  local word="${SSH_ORIGINAL_COMMAND:-}" logfile pid code=0
  [[ "$word" == --status || "$word" == --rollback || "$word" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "expected a commit sha, --rollback or --status, got: $(printf '%q' "$word")" >&2; exit 2; }
  [[ "$word" == --status ]] && exec "$BASH" "$0" --status
  mkdir -p "$ROOT/logs" || exit 2
  logfile="$ROOT/logs/$(date -u +%Y%m%dT%H%M%SZ)-$$-${word#--}.log"
  : >"$logfile"  # before tail opens it
  env -u SSH_ORIGINAL_COMMAND setsid --wait "$BASH" "$0" "$word" </dev/null >>"$logfile" 2>&1 &
  pid=$!
  tail -n +1 -f --pid="$pid" "$logfile" >&2 || true
  wait "$pid" || code=$?
  echo "this log is kept on the host: $logfile" >&2
  exit "$code"
}

case "${1:-}" in
  --from-ssh) from_ssh ;;
  --status)
    echo "current: $(cat "$ROOT/current" 2>/dev/null || echo none)"
    echo "previous: $(cat "$ROOT/previous" 2>/dev/null || echo none)"
    exit 0 ;;
esac
# CI and a person deploying by hand must never overlap.
exec 9>"$ROOT/.deploy.lock"
flock -n 9 || { log "another deploy is running"; exit 2; }

case "${1:-}" in
  --rollback) rollback; exit $? ;;
  ""|-*) echo "usage: deploy.sh <git ref> | --rollback | --status | --from-ssh" >&2; exit 2 ;;
esac

git -C "$SRC" fetch -q origin || { log "git fetch failed"; exit 2; }
# origin/<ref> first: the clone's own branches are never updated (Sift once built a stale main).
sha=$(git -C "$SRC" rev-parse --verify -q "origin/$1^{commit}" || git -C "$SRC" rev-parse --verify -q "$1^{commit}") \
  || { log "unknown ref $1"; exit 2; }
log "release $sha ($1)"
git -C "$SRC" checkout -q --detach "$sha"
docker build -q -t "triagelens:$sha" "$SRC" >/dev/null || { log "build failed"; exit 2; }
out=$(compose "$sha" run --rm triagelens-migrate 2>&1) || { log "migrations failed"; echo "$out" >&2; exit 2; }
out=$(compose "$sha" run --rm triagelens-seed 2>&1) || { log "seed failed"; echo "$out" >&2; exit 2; }
live=$(cat "$ROOT/current" 2>/dev/null || true)
if start "$sha"; then
  [[ -n "$live" && "$live" != "$sha" ]] && echo "$live" > "$ROOT/previous"
  echo "$sha" > "$ROOT/current"
  log "deployed $sha"
  cleanup
  exit 0
fi
log "release $sha failed"
target=$live
[[ "$live" == "$sha" ]] && target=$(cat "$ROOT/previous" 2>/dev/null || true)
[[ -n "$target" && "$target" != "$sha" ]] || { log "nothing to roll back to"; exit 1; }
rollback "$target" && exit 1
exit 3

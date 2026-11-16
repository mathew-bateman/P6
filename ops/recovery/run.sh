#!/usr/bin/env bash
# Run the root-private recovery helpers through the existing Docker capability.
set -euo pipefail
deployment=/home/mat/Docker/P6-Standby
operation=${1:-}
identifier=${2:-}
if [[ ! $identifier =~ ^[A-Za-z0-9][A-Za-z0-9_-]{5,95}$ ]]; then
  echo 'Usage: bash ops/recovery/run.sh {capture|verify|resume|finalize} RECOVERY_ID' >&2
  exit 2
fi
case "$operation" in
  capture) helper=capture_recovery.py; arguments=(--deployment "$deployment" --recovery-id "$identifier") ;;
  verify) helper=verify_recovery.py; arguments=(--directory "$deployment/app/.recovery/$identifier") ;;
  resume) exec docker exec p6-backup-admin-web python /app/ops/recovery/resume_publication.py --destination "/app/.recovery/$identifier" --recovery-id "$identifier" ;;
  finalize) exec docker exec p6-backup-admin-web python /app/ops/recovery/finalize_recovery.py --directory "/app/.recovery/$identifier" ;;
  *) echo 'Choose capture, verify, resume or finalize.' >&2; exit 2 ;;
esac
image=$(docker inspect p6-backup-admin-web --format '{{.Image}}')
exec docker run --rm --network none --user 0 --entrypoint python \
  --volume "$deployment:$deployment" \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume /usr/bin/docker:/usr/bin/docker:ro \
  "$image" "$deployment/app/ops/recovery/$helper" "${arguments[@]}"

#!/bin/sh
# Isolated disposable volume; never mounts the user's real diary or credentials.
set -eu
image="${1:-fsbot:runtime}"
fixture_image="${2:-fsbot:test}"
volume="fsbot-smoke-$(date +%s)-$$"
trap 'docker volume rm "$volume" >/dev/null' EXIT
docker volume create "$volume" >/dev/null
# Seed a real Python-created database with root ownership, like an old bind mount.
docker run --rm --network none --entrypoint sh -v "$volume:/data" "$fixture_image" -ec '
  cp /app/testdata/python-state.sqlite /data/fsbot.sqlite3
  chown -R 0:0 /data
  chmod 700 /data
'
# Bootstrap starts as root, repairs an old volume and drops privileges.
output=$(docker run --rm --network none -v "$volume:/data" "$image" check)
printf '%s\n' "$output"
case "$output" in *'uid=10001 gid=10001'*) ;; *) exit 1 ;; esac
docker run --rm --network none --user 10001:10001 -v "$volume:/data" "$image" check
docker run --rm --network none --user 10001:10001 -v "$volume:/data" "$image" healthcheck
# A new, separate container has no heartbeat and must be unhealthy.
if docker run --rm --network none "$image" healthcheck; then
    echo 'healthcheck incorrectly accepted missing heartbeat' >&2
    exit 1
fi
docker image inspect --format 'Runtime size: {{.Size}} bytes' "$image"

#!/usr/bin/env bash
# SP-294 — recreate the two long-lived data-bearing containers ONCE so they
# pick up the log cap (max-size=10m, max-file=3). Idempotent/resumable: if a
# previous run already renamed <name> to <name>_old_sp294, this picks up from
# there. Rolls back automatically if the new `docker run` fails.
set -uo pipefail

recreate() {
  local name="$1" image="$2" vol="$3" mount="$4" portmap="$5"
  local old="${name}_old_sp294"
  echo "================ recreating ${name} ================"

  if ! docker volume inspect "$vol" >/dev/null 2>&1; then
    echo "ABORT: data volume ${vol} not found for ${name}."
    return 1
  fi

  # Determine the source container to read env from and the state we're in.
  local src=""
  if docker inspect "$name" >/dev/null 2>&1; then
    src="$name"            # normal: live container still named <name>
  elif docker inspect "$old" >/dev/null 2>&1; then
    src="$old"             # resuming after a prior failed run
  else
    echo "ABORT: neither ${name} nor ${old} exists."
    return 1
  fi

  # If a healthy new <name> is already running and already capped, skip.
  if [ "$src" = "$name" ]; then
    local capped
    capped=$(docker inspect -f '{{.HostConfig.LogConfig.Config}}' "$name")
    if echo "$capped" | grep -q 'max-size'; then
      echo "already capped: ${capped} — skipping."
      return 0
    fi
  fi

  # Build -e args from the source container's env, skipping blanks and the
  # image-provided defaults we must not override.
  local eargs=() l key
  while IFS= read -r l; do
    [ -z "$l" ] && continue
    key="${l%%=*}"
    case "$key" in
      PATH|GOSU_VERSION|PG_MAJOR|PG_VERSION|PG_SHA256|DOCKER_PG_LLVM_DEPS|LANG|PYTHONPATH) continue ;;
    esac
    eargs+=( -e "$l" )
  done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$src")

  # Rename the source to the rollback handle if not already done.
  if [ "$src" = "$name" ]; then
    echo "--- stop + rename ${name} -> ${old} ---"
    docker stop "$name" >/dev/null
    docker rename "$name" "$old"
  else
    echo "--- resuming: ${old} already exists as rollback handle ---"
    docker stop "$old" >/dev/null 2>&1 || true
  fi

  echo "--- run new ${name} with log cap + same volume ---"
  if docker run -d \
      --name "$name" \
      --restart unless-stopped \
      --network my_trading_network \
      -p "$portmap" \
      -v "${vol}:${mount}" \
      --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
      "${eargs[@]}" \
      "$image" >/dev/null; then
    sleep 4
    echo "--- verify ---"
    docker inspect -f 'status={{.State.Status}} logopts={{.HostConfig.LogConfig.Config}}' "$name"
  else
    echo "!!! docker run failed for ${name} — ROLLING BACK to ${old} !!!"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker rename "$old" "$name"
    docker start "$name" >/dev/null
    echo "rolled back: ${name} restarted from original container."
    return 1
  fi
}

recreate my_postgres timescale/timescaledb:latest-pg16 \
  a2842983acaa542b51df5d4e3ba45b2b72339bd768d0cccfdd51529dd88dd6ad \
  /var/lib/postgresql/data 5432:5432

recreate my_pgadmin dpage/pgadmin4:latest \
  f9a000706bf9354028b3ed632dd06ec7572d1151efa603d262ba3bd4ac38672f \
  /var/lib/pgadmin 80:80

echo
echo "================ post-recreate health ================"
docker ps --format 'table {{.Names}}\t{{.Status}}'
echo "--- postgres ready? ---"
sleep 3
docker exec my_postgres pg_isready || true

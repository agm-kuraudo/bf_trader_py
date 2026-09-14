#!/bin/bash
#
# Rebuild the my_postgres (TimescaleDB) and my_pgadmin containers and apply the
# bf schema (SP-352).
#
# Hardening note (SP-352): the postgres/timescaledb entrypoint starts a
# *temporary* internal server while it runs init scripts, then shuts it down
# and restarts the real server. pg_isready can report "ready" against that
# transient server, so a naive "wait for pg_isready then psql" races: the
# CREATE DATABASE step can hit the transient server / a brief outage and the
# schema silently fails to land. To avoid that we:
#   1. Wait for a real "SELECT 1" to succeed, requiring several CONSECUTIVE
#      successes so we don't latch onto the transient init server.
#   2. Apply the SQL with ON_ERROR_STOP=1 and RETRY on failure.
#   3. VERIFY the bf_trader DB + all expected bf tables exist, exiting non-zero
#      if not, so a failed apply is loud instead of silent.
set -euo pipefail

# Define variables
containerName_PG="my_postgres"
containerName_Admin="my_pgadmin"
dbPassword="$POSTGRES_PASSWORD"
sqlFilePath="sql/create_database.sql"

# Readiness / retry tuning.
readyTimeoutSecs=120        # max wait for a stable, query-answering server
requiredConsecutive=3       # consecutive good "SELECT 1"s before we trust it
sqlMaxAttempts=5            # attempts to apply create_database.sql

# Tables the bf schema must contain after apply (kept in sync with
# create_database.sql and scripts/verify_db.py REQUIRED_TABLES).
expectedTables="betfair_object_ids log_file market_table target quality_run quality_match_result"

# Remove any existing container with the same name
docker rm -f $containerName_PG
docker rm -f $containerName_Admin

# Run the PostgreSQL container
docker run --name $containerName_PG --network my_trading_network --ip 172.19.0.3 --restart unless-stopped -e POSTGRES_PASSWORD=$dbPassword -p 5432:5432 -d timescale/timescaledb:latest-pg16

# --- Step: wait for a STABLE, query-answering server -------------------------
# We require `requiredConsecutive` back-to-back successful "SELECT 1" queries.
# A single failure resets the counter, so the transient init-server window
# (which comes up, then goes down for the real restart) cannot satisfy the run.
echo "Waiting for PostgreSQL to accept queries (need $requiredConsecutive consecutive successes)..."
consecutive=0
elapsed=0
until [ "$consecutive" -ge "$requiredConsecutive" ]; do
  if docker exec "$containerName_PG" psql -U postgres -d postgres -tAc "SELECT 1" >/dev/null 2>&1; then
    consecutive=$((consecutive + 1))
    echo "  ok ($consecutive/$requiredConsecutive)"
  else
    if [ "$consecutive" -ne 0 ]; then
      echo "  server went away (likely init restart) - resetting stability counter"
    fi
    consecutive=0
  fi

  if [ "$elapsed" -ge "$readyTimeoutSecs" ]; then
    echo "ERROR: PostgreSQL did not become stable within ${readyTimeoutSecs}s." >&2
    docker logs --tail 50 "$containerName_PG" >&2 || true
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done
echo "PostgreSQL is stable and answering queries."

# Copy the SQL file into the container
docker cp "$sqlFilePath" "$containerName_PG:/tmp/create_database.sql"

# --- Step: apply the schema, with retries and ON_ERROR_STOP ------------------
# ON_ERROR_STOP=1 makes psql exit non-zero on the first SQL error so we don't
# treat a partial apply as success. create_database.sql is idempotent
# (CREATE DATABASE will error if it already exists, so on a retry we only
# re-run when the DB is confirmed absent below).
applied=0
attempt=1
while [ "$attempt" -le "$sqlMaxAttempts" ]; do
  echo "Applying create_database.sql (attempt $attempt/$sqlMaxAttempts)..."

  # If bf_trader already exists from a previous attempt, skip CREATE DATABASE
  # and (re)apply only the schema/tables against bf_trader directly.
  if docker exec "$containerName_PG" psql -U postgres -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='bf_trader'" 2>/dev/null | grep -q 1; then
    echo "  bf_trader already exists - reapplying schema objects only."
    # Strip the CREATE DATABASE / \c lines and run the rest against bf_trader.
    if docker exec "$containerName_PG" bash -c \
        "grep -viE '^(CREATE DATABASE|\\\\c )' /tmp/create_database.sql | psql -v ON_ERROR_STOP=1 -U postgres -d bf_trader"; then
      applied=1
      break
    fi
  else
    if docker exec "$containerName_PG" psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /tmp/create_database.sql; then
      applied=1
      break
    fi
  fi

  echo "  apply attempt $attempt failed - retrying after 3s." >&2
  attempt=$((attempt + 1))
  sleep 3
done

if [ "$applied" -ne 1 ]; then
  echo "ERROR: failed to apply create_database.sql after ${sqlMaxAttempts} attempts." >&2
  docker logs --tail 50 "$containerName_PG" >&2 || true
  exit 2
fi

# --- Step: VERIFY the database and all expected tables exist -----------------
echo "Verifying bf_trader database and bf schema tables..."
if ! docker exec "$containerName_PG" psql -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='bf_trader'" 2>/dev/null | grep -q 1; then
  echo "ERROR: bf_trader database was not created." >&2
  exit 3
fi

missing=""
for t in $expectedTables; do
  if ! docker exec "$containerName_PG" psql -U postgres -d bf_trader -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='bf' AND table_name='$t'" 2>/dev/null | grep -q 1; then
    missing="$missing $t"
  fi
done

if [ -n "$missing" ]; then
  echo "ERROR: bf schema is missing expected table(s):$missing" >&2
  exit 4
fi
echo "Schema verified: bf_trader present with all expected bf tables."

# Run the pgAdmin container
docker run --name $containerName_Admin --network my_trading_network --ip 172.19.0.4 --restart unless-stopped -e PGADMIN_DEFAULT_EMAIL="agm12@duck.com" -e PGADMIN_DEFAULT_PASSWORD=$dbPassword -p 80:80 -d dpage/pgadmin4:latest

echo "postgres_build complete."

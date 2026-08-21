# DB_HOST DNS-Based Resolution Bugfix Design

## Overview

The `DB_HOST` value in `.env` must be manually changed when switching between local development (where Postgres is accessed via `localhost` through port-mapping) and Docker execution (where Postgres is accessed via Docker DNS on `my_trading_network`). The fix uses the Docker container name `my_postgres` as the canonical `DB_HOST` value. Inside Docker, this resolves via Docker DNS automatically. On local Windows dev, a hosts file entry (`127.0.0.1 my_postgres`) makes the same name resolve to localhost. No code changes to `DotenvLoader` are required — the fix is purely configuration-level.

## Glossary

- **Bug_Condition (C)**: `DB_HOST` contains a value that only resolves in one execution context (e.g. `localhost` works locally but not in Docker; an IP like `172.19.0.3` works in Docker but is fragile and non-portable)
- **Property (P)**: `DB_HOST=my_postgres` resolves to the correct Postgres address in both Docker and local dev contexts without manual `.env` changes
- **Preservation**: All existing application behaviour — `DotenvLoader.get_secret()`, Betfair credentials, DB connection logic — remains completely unchanged
- **DotenvLoader**: The class in `api/auth/dotenv_loader.py` that reads secrets from `.env` via `dotenv_values()`
- **my_trading_network**: The Docker bridge network that connects the application container and `my_postgres`
- **Docker DNS**: Docker's built-in DNS server that resolves container names to IPs within a user-defined network
- **hosts file**: The OS-level name resolution file (`C:\Windows\System32\drivers\etc\hosts` on Windows, `/etc/hosts` on Linux)

## Bug Details

### Bug Condition

The bug manifests when a developer needs to run the application in both local dev and Docker contexts. The `.env` file contains a `DB_HOST` value that only resolves correctly in one context, forcing manual editing when switching.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type (db_host: str, execution_context: enum[LOCAL, DOCKER])
  OUTPUT: boolean
  
  RETURN (db_host == "localhost" AND execution_context == DOCKER)
         OR (db_host IN [IP_ADDRESS_PATTERN] AND execution_context == LOCAL AND ip_not_reachable)
         OR (db_host == "172.19.0.3" AND execution_context == LOCAL AND no_port_mapping_to_that_ip)
END FUNCTION
```

### Examples

- **Docker execution with `localhost`**: `.env` has `DB_HOST=localhost`, application runs in Docker container — `localhost` resolves to the container itself, not the Postgres container. Connection fails.
- **Local dev with Docker IP**: `.env` has `DB_HOST=172.19.0.3`, developer runs locally on Windows — the IP is only reachable from within the Docker network, not from the host. Connection fails.
- **Context switch required**: Developer changes `DB_HOST=localhost` to `DB_HOST=172.19.0.3` before building Docker image, then forgets to change it back for local dev. Next local run fails.
- **Edge case — IP changes**: Docker assigns a different IP to `my_postgres` after network recreation. The hardcoded IP in `.env` becomes stale. Connection fails in Docker too.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `DotenvLoader.get_secret("DB_HOST")` must continue to return the raw value from `.env` — no code changes to the loader
- `DotenvLoader.get_secret()` for all other keys (`DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PWD`, `BF_AppKey`, etc.) must continue to work identically
- `DotenvLoader.__init__()` raising `ConfigurationException` for missing `.env` must be unchanged
- `BFDriver.get_local_db_details()` must continue to read DB connection details from `DotenvLoader` unchanged
- `web_site.py` database connection logic must continue to work unchanged
- The Postgres container must remain named `my_postgres` and connected to `my_trading_network`
- Port mapping (`5432:5432`) from Docker host to `my_postgres` must remain in place for local dev access

**Scope:**
All application code is completely unaffected by this fix. The changes are limited to:
- `.env` file value for `DB_HOST`
- `.env.example` documentation
- Windows hosts file (manual one-time setup)

## Hypothesized Root Cause

Based on the bug description, the root cause is a configuration design issue:

1. **Context-specific hostname in `.env`**: The `.env` file uses a hostname (`localhost`) or IP (`172.19.0.3`) that is inherently context-dependent. `localhost` means "this machine" — inside a Docker container, that's the container itself, not the Postgres container.

2. **No shared name resolution**: There is no DNS name that resolves to the correct Postgres address in both local dev and Docker contexts. Docker provides built-in DNS for container names within user-defined networks, but this doesn't help outside Docker.

3. **The fix**: Using the container name `my_postgres` as `DB_HOST` works in Docker via Docker DNS. Adding a hosts file entry on the local machine (`127.0.0.1 my_postgres`) makes the same name resolve locally to `127.0.0.1`, which connects via the port-mapped `5432`. Both contexts now resolve `my_postgres` to the correct Postgres address.

## Correctness Properties

Property 1: Bug Condition - DNS Name Resolves in Both Contexts

_For any_ execution context (local dev or Docker) where `DB_HOST=my_postgres` is set in `.env`, the system SHALL resolve `my_postgres` to a valid Postgres address and establish a successful database connection — via Docker DNS in containers, via hosts file entry on local Windows.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Application Code and Other Config Unchanged

_For any_ call to `DotenvLoader.get_secret()` or any other application code path, the fixed configuration SHALL produce exactly the same application behaviour as before (other than the DB_HOST value itself changing from `localhost` to `my_postgres`), preserving all existing functionality including error handling, credential loading, and database connection logic.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

**File**: `.env`

**Specific Changes**:
1. **Change DB_HOST value**: Replace `DB_HOST=localhost` with `DB_HOST=my_postgres`

**File**: `.env.example`

**Specific Changes**:
2. **Change DB_HOST value**: Replace `DB_HOST=172.19.0.3` with `DB_HOST=my_postgres`
3. **Add hosts file documentation**: Add a comment explaining that local development requires a hosts file entry (`127.0.0.1 my_postgres`) for name resolution

**Manual step (documented)**:
4. **Windows hosts file**: Add entry `127.0.0.1 my_postgres` to `C:\Windows\System32\drivers\etc\hosts` — this is a one-time setup step for local development

**Verification (no change needed)**:
5. **Confirm container name**: Verify the Postgres container is named `my_postgres` (confirmed in `scripts/start_up_postgres.sh`: `docker start my_postgres`)
6. **Confirm network membership**: Verify `my_postgres` is on `my_trading_network` for Docker DNS resolution

## Testing Strategy

### Validation Approach

The testing strategy validates that name resolution works in both contexts and that no application behaviour has changed. Since this is a configuration-only fix with no code changes, testing focuses on connectivity verification rather than unit tests.

### Exploratory Bug Condition Checking

**Goal**: Demonstrate the bug on UNFIXED configuration. Confirm that `localhost` fails in Docker and that a Docker-only IP fails locally.

**Test Plan**: Attempt database connections with the current `DB_HOST` values in each context to confirm failures.

**Test Cases**:
1. **Docker with localhost**: Run application in Docker container with `DB_HOST=localhost` — connection to Postgres fails (will fail on unfixed config)
2. **Local with Docker IP**: Run application locally with `DB_HOST=172.19.0.3` — connection fails unless Docker network is reachable from host (will fail on unfixed config)
3. **Name resolution check**: Run `nslookup my_postgres` or `ping my_postgres` locally without hosts file entry — name does not resolve (will fail on unfixed config)

**Expected Counterexamples**:
- `psycopg2.OperationalError: could not connect to server` when using `localhost` inside Docker
- Name resolution failure for `my_postgres` on local machine without hosts file entry

### Fix Checking

**Goal**: Verify that with `DB_HOST=my_postgres` and proper DNS/hosts configuration, the database connection succeeds in both contexts.

**Pseudocode:**
```
FOR ALL context IN [LOCAL_DEV, DOCKER] WHERE DB_HOST == "my_postgres" DO
  result := resolve_hostname("my_postgres", context)
  ASSERT result == valid_postgres_address(context)
  connection := connect_to_postgres(result, DB_PORT, DB_NAME, DB_USER, DB_PWD)
  ASSERT connection.is_open()
END FOR
```

### Preservation Checking

**Goal**: Verify that all application code behaves identically — only the resolved address changes, not the code paths.

**Pseudocode:**
```
FOR ALL key IN dotenv_keys WHERE key != "DB_HOST" DO
  ASSERT DotenvLoader.get_secret(key) == original_value(key)
END FOR

FOR ALL code_path IN [DotenvLoader, BFDriver, web_site] DO
  ASSERT code_path.behaviour == unchanged
END FOR
```

**Testing Approach**: Manual verification is appropriate for this configuration-only fix because:
- No code is changed, so unit tests on `DotenvLoader` already cover the loader behaviour
- The fix is in name resolution (OS/Docker level), not application logic
- Existing tests that use `DotenvLoader` will continue to pass unchanged

**Test Cases**:
1. **DotenvLoader still reads .env**: Verify `get_secret("DB_HOST")` returns `"my_postgres"` (the new value) — confirms loader works unchanged
2. **Other secrets unchanged**: Verify all Betfair and DB credentials return their correct values
3. **Missing .env still raises**: Verify `ConfigurationException` is still raised when `.env` is absent
4. **Connection works locally**: Run application locally, confirm Postgres connection via `my_postgres` → `127.0.0.1` → port-mapped `5432`
5. **Connection works in Docker**: Run application in Docker on `my_trading_network`, confirm Postgres connection via `my_postgres` → Docker DNS → Postgres container

### Unit Tests

- Existing `DotenvLoader` unit tests pass unchanged (no code was modified)
- Verify `get_secret("DB_HOST")` returns `"my_postgres"` with the updated `.env`

### Property-Based Tests

- Not applicable for this fix — there are no code changes to validate with PBT
- The "property" being tested is name resolution at the OS/Docker level, which is outside the application's control

### Integration Tests

- Connect to Postgres from local dev using `DB_HOST=my_postgres` with hosts file entry present
- Connect to Postgres from Docker container using `DB_HOST=my_postgres` on `my_trading_network`
- Verify full application startup succeeds in both contexts without `.env` modification

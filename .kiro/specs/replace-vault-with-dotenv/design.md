# Design Document

## Overview

This document describes the design for replacing HashiCorp Vault with `python-dotenv` in the bf_trader_py application (SP-292).

### Background

The application currently uses Vault (running as a Docker container named `my_vault`) to store and retrieve all secrets. The `VaultReader` class in `api/auth/vault/vault_reader.py` wraps the `hvac` client and is used in two places:

1. **`BFDriver.__init__`** — instantiates a `Vault()` object (`self.__vault_obj`) at startup
2. **`BFDriver.get_local_db_details`** — calls `self.__vault_obj.read_secret("postgres")` to fetch DB credentials
3. **`Auth.get_credentials_from_vault`** — instantiates a new `VaultReader()`, reads the `bf` secret for userid/password, and reads `bf_token` for the cached SSO token
4. **`BFDriver.get_token`** — calls `Auth.get_credentials_from_vault()`, validates the cached token, and if invalid calls `self.__vault_obj.update_secret(path="bf_token", ...)` to persist the new token back to Vault

The Vault-based SSO token cache is a significant source of operational burden. The replacement removes it entirely: a fresh token is always obtained on startup via the existing `call_auth` certificate flow.

The Docker setup uses a custom bridge network (`my_trading_network`) with static IPs. No `docker-compose.yml` was found in the project — containers are managed via individual `build/vault_build.ps1` and `build/vault_build.sh` scripts that are also removed as part of this change.

### Summary of Change

- Delete the `api/auth/vault/` module and the `hvac` dependency
- Create `api/auth/dotenv_loader.py` — the `DotenvLoader` class that replaces `VaultReader`
- Rewrite `Auth` to load credentials from `DotenvLoader` instead of Vault
- Rewrite `BFDriver` to remove all Vault references; `get_local_db_details` reads DB keys from env; `get_token` always authenticates fresh
- Add `.env` (excluded from git) and `.env.example` (committed)
- Update `.gitignore`, `build/requirements.txt`, the Dockerfile, scripts, and tests
- Delete Vault build/config/startup files

---

## Architecture

### Before

```
target_service.py / monitor_service.py
        │
        ▼
    BFDriver
    ├── __init__: Vault() ──────────────────────► my_vault container (Docker)
    ├── get_local_db_details: vault.read_secret("postgres")
    └── get_token:
            Auth.get_credentials_from_vault()
            └── VaultReader.read_secret("bf")       ► bf_userid, bf_pwd
            └── VaultReader.read_secret("bf_token") ► cached SSO token
            vault.update_secret("bf_token", ...)    ► write new token back
```

### After

```
target_service.py / monitor_service.py
        │
        ▼
    BFDriver
    ├── __init__: DotenvLoader() ──────────────► .env file (project root)
    ├── get_local_db_details: loader.get_secret("DB_HOST") etc.
    └── get_token:
            Auth.get_credentials() ─────────────► loader.get_secret("BF_USERID") etc.
            call_auth (certificate flow) ────────► fresh SSO token every run
```

The `DotenvLoader` is instantiated once in `BFDriver.__init__` and injected into `Auth`. There is no persistent SSO token store; the Betfair certificate-based authentication flow is called unconditionally on every `get_token` invocation.

---

## Components and Interfaces

### New: `api/auth/dotenv_loader.py`

**Location:** `api/auth/dotenv_loader.py`

**Purpose:** Load the `.env` file at project root and provide a single access method for secret values. Replaces `VaultReader` as the sole secret-retrieval mechanism.

```python
class ConfigurationException(Exception):
    pass

class DotenvLoader:
    def __init__(self, env_path: str = None):
        """
        Load the .env file from the given path (defaults to project root `.env`).
        Raises ConfigurationException if the file does not exist.
        Does NOT modify os.environ globally — uses dotenv_values() for isolation.
        """

    def get_secret(self, key: str) -> str:
        """
        Return the string value for the given key.
        Raises ConfigurationException if the key is absent or empty.
        """
```

**Design decisions:**

- Uses `dotenv_values()` rather than `load_dotenv()` so that secrets are stored in a private dict on the instance and do not bleed into the global `os.environ`. This prevents accidental leakage to subprocesses and keeps the loader testable in isolation (no need to clear env between tests).
- The `env_path` parameter defaults to a path resolved relative to the file's own location (`Path(__file__).resolve().parents[2] / ".env"`), which resolves to the project root regardless of the working directory. An explicit path can be passed in tests.
- Raises `ConfigurationException` (not `KeyError` or `ValueError`) so callers can catch a single, typed exception.

### Modified: `api/auth/auth_details.py` — `Auth` class

**Changes:**
- Remove `import api.auth.vault.vault_reader`
- Remove class-level `os.getenv(...)` calls for `crt_file`, `key_file`, `app_key` (these currently execute at import time before `.env` is loaded)
- Accept a `DotenvLoader` instance in `__init__` and read all five credential keys from it
- Remove `get_credentials_from_vault` method entirely
- Add `get_credentials` method that reads `BF_USERID` and `BF_PWD` from the loader (called by `BFDriver.get_token`)

**New interface:**

```python
class Auth:
    def __init__(self, loader: DotenvLoader):
        # reads BF_AppKey, BF_CRT_FILE, BF_KEY_FILE immediately
        # stores loader for deferred credential access

    def get_credentials(self):
        # reads BF_USERID, BF_PWD from loader
        # sets self.__bf_userid, self.__bf_pwd

    # validate_betfair_token remains unchanged
    # all property getters/setters remain unchanged
```

**Why read cert/key/appkey at init but userid/pwd deferred?** The original code already follows this split: cert/key/appkey are class-level vars loaded at import time, while userid/pwd are only fetched when `get_credentials_from_vault` is called. This design preserves that separation — it keeps `Auth.__init__` lightweight and allows `get_credentials` to be called lazily by `BFDriver.get_token`, matching the existing call sequence.

### Modified: `BFDriver.py`

**Changes:**
- Remove `from api.auth.vault.vault_reader import VaultReader as Vault, VaultException`
- Add `from api.auth.dotenv_loader import DotenvLoader, ConfigurationException`
- In `__init__`: replace `self.__vault_obj = Vault()` with `self.__loader = DotenvLoader()`; pass `self.__loader` to `bf_auth.Auth(self.__loader)`
- Rewrite `get_local_db_details`: read five keys directly from `self.__loader.get_secret(...)` and raise `BFDriverException` on `ConfigurationException`
- Rewrite `get_token`: call `self.__auth_obj.get_credentials()`, then **always** call `call_auth` to get a fresh token — remove the validate-then-conditionally-refresh logic and remove `self.__vault_obj.update_secret(...)`

**New `get_token` flow:**

```python
def get_token(self):
    self.__auth_obj.get_credentials()
    self.__call_obj.auth = self.__auth_obj
    self.__auth_obj.security_token = self.__call_obj.call_auth(
        self.__request_body_obj.populate_template(
            "CertAuth",
            {"<USERID>": self.__auth_obj.bf_userid, "<PWD>": self.__auth_obj.bf_pwd},
            add_quotes=True,
        )
    )
    self.__call_obj.auth = self.__auth_obj
    Log.log_debug("Token: {}".format(self.__auth_obj.security_token))
    return True
```

The old flow attempted to reuse the Vault-cached token and only re-authenticated if it was invalid. Since the token is no longer cached between runs, that optimisation is meaningless — fresh authentication is always required.

### `api/auth/__init__.py`

Update the module docstring to remove the reference to Vault.

---

## Data Models

### `.env` file (project root, not committed)

Plain key=value pairs loaded by `python-dotenv`. All values are strings.

```
BF_AppKey=<betfair_application_key>
BF_CRT_FILE=./certs/client-2048.crt
BF_KEY_FILE=./certs/client-2048.key
BF_USERID=<betfair_username>
BF_PWD=<betfair_password>
DB_HOST=172.19.0.3
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PWD=<postgres_password>
```

### `.env.example` file (project root, committed)

```
# bf_trader_py configuration
# Copy this file to .env and populate with real values before running the application.
# Never commit .env to version control.

BF_AppKey=your_betfair_app_key_here
BF_CRT_FILE=./certs/client-2048.crt
BF_KEY_FILE=./certs/client-2048.key
BF_USERID=your_betfair_username_here
BF_PWD=your_betfair_password_here
DB_HOST=172.19.0.3
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PWD=your_postgres_password_here
```

### Secret key inventory

| Key | Used by | Previously stored in Vault path |
|---|---|---|
| `BF_AppKey` | `Auth.__init__` | `cubbyhole/bf` → `bf_userid` (indirect; was env var) |
| `BF_CRT_FILE` | `Auth.__init__` | env var (no change in source) |
| `BF_KEY_FILE` | `Auth.__init__` | env var (no change in source) |
| `BF_USERID` | `Auth.get_credentials` | `cubbyhole/bf` → `bf_userid` |
| `BF_PWD` | `Auth.get_credentials` | `cubbyhole/bf` → `bf_pwd` |
| `DB_HOST` | `BFDriver.get_local_db_details` | `cubbyhole/postgres` → `host` |
| `DB_PORT` | `BFDriver.get_local_db_details` | `cubbyhole/postgres` → `port` |
| `DB_NAME` | `BFDriver.get_local_db_details` | `cubbyhole/postgres` → `db_name` |
| `DB_USER` | `BFDriver.get_local_db_details` | `cubbyhole/postgres` → `db_user` |
| `DB_PWD` | `BFDriver.get_local_db_details` | `cubbyhole/postgres` → `db_pwd` |

Note: `BF_AppKey`, `BF_CRT_FILE`, and `BF_KEY_FILE` were already read from `os.environ` in the original `Auth` class (not from Vault). They are included in `.env` so that a single file is the source of truth for all configuration.

---
## Files to Delete

| File | Reason |
|---|---|
| `api/auth/vault/vault_reader.py` | Replaced by `DotenvLoader` |
| `api/auth/vault/__init__.py` | Module removed |
| `api/auth/vault/` directory | Module removed |
| `config/config.hcl` | Vault server configuration — no longer needed |
| `scripts/start_up_vault.ps1` | Vault startup script — no longer needed |
| `scripts/start_up_vault.sh` | Vault startup script — no longer needed |
| `build/vault_build.ps1` | Vault container build script — no longer needed |
| `build/vault_build.sh` | Vault container build script — no longer needed |
| `build/vault_config.json` | Vault container configuration — no longer needed |
| `config/Docker Run Command.txt` | Contains only Vault docker run commands — no longer relevant |

---

## Files to Modify

### `build/requirements.txt`

- Remove: `hvac==2.3.0`
- Add: `python-dotenv==1.0.1` (pin to latest stable release)

### `build/betfair_app.dockerfile`

Remove the `VAULT_TOKEN` env var line. The file currently sets `BF_AppKey`, `BF_CRT_FILE`, and `BF_KEY_FILE` as Docker ENV instructions — this pattern pre-dates the `.env` approach. For the containerised deployment these can remain as Docker ENV instructions (they take precedence over `.env` in a container), or the `.env` file can be bind-mounted at runtime. The Dockerfile `ENV VAULT_TOKEN=...` line must be removed regardless.

```dockerfile
# Remove this line:
ENV VAULT_TOKEN=hvs.NxuX4fDhHP5SDV1adUepMPbf
```

### `.gitignore`

The current `.gitignore` already excludes `/web/.env` but does NOT exclude the root-level `.env` file. Add the root `.env` entry:

```
# Current .gitignore contents:
*.pyc
*.log
certs
charts
/build/run_deck_data
/web/.env
.venv

# Add:
.env
```

Note: using `.env` (no leading slash) matches both `/.env` and any nested `.env` files, which is the conventional pattern. The existing `/web/.env` entry can remain for explicitness but is now covered by the broader rule.

### `scripts/start_up_postgres.ps1` and `scripts/start_up_postgres.sh`

These scripts do not currently reference Vault or `VAULT_TOKEN` — no changes required.

### `scripts/run_target_service.ps1` and `scripts/run_target_service.sh`

These scripts start Docker containers and do not set any environment variables — no changes required.

### `scripts/run_monitor_service.ps1` and `scripts/run_monitor_service.sh`

`run_monitor_service.sh` sources `/etc/environment` to load environment variables before starting the container. If `VAULT_TOKEN` was previously set there, it should be removed from `/etc/environment`. The script files themselves do not reference `VAULT_TOKEN` directly — no changes to the script files are required.

### `tests/unit_tests_betfair_objects.py`

- Remove `from api.auth.vault.vault_reader import VaultReader, VaultException`
- Remove `test_vault` method entirely
- Rewrite `test_auth` to test the new `Auth(DotenvLoader(...))` interface
- Rewrite `test_db_connection` to work with the new `BFDriver` (which no longer needs Vault running)

### `target_service.py` and `monitor_service.py`

Update the error messages that reference Vault (e.g. `"Failed to authenticate to vault..."`) to reflect the new dotenv-based flow.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Missing required key raises ConfigurationException

*For any* key in the set of required secret keys (`BF_AppKey`, `BF_CRT_FILE`, `BF_KEY_FILE`, `BF_USERID`, `BF_PWD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PWD`), when that key is absent or set to an empty string in the `.env` file, calling `DotenvLoader.get_secret(key)` SHALL raise a `ConfigurationException` whose message identifies the missing key by name.

**Validates: Requirements 2.3**

### Property 2: Present key returns correct string value

*For any* key-value pair written to the `.env` file, `DotenvLoader.get_secret(key)` SHALL return the exact string value that was written, without modification.

**Validates: Requirements 2.4**

### Property 3: Auth credential key absence raises AuthException

*For any* key in `{BF_AppKey, BF_CRT_FILE, BF_KEY_FILE, BF_USERID, BF_PWD}`, when that key is absent or empty in the loaded environment, the `Auth` class SHALL raise an `AuthException` whose message identifies the missing key by name.

**Validates: Requirements 3.3, 3.4**

### Property 4: Missing DB key raises BFDriverException

*For any* key in `{DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PWD}`, when that key is absent or empty in the loaded environment, `BFDriver.get_local_db_details()` SHALL raise a `BFDriverException` whose message identifies the missing key by name.

**Validates: Requirements 5.2**

---

## Error Handling

### `DotenvLoader`

| Condition | Behaviour |
|---|---|
| `.env` file not found at the expected path | Raise `ConfigurationException("env file not found: {path}")` |
| Key absent from loaded env dict | Raise `ConfigurationException("Required key '{key}' is missing from .env")` |
| Key present but empty string | Raise `ConfigurationException("Required key '{key}' is empty in .env")` |

### `Auth`

| Condition | Behaviour |
|---|---|
| `BF_AppKey`, `BF_CRT_FILE`, or `BF_KEY_FILE` missing/empty | Raise `AuthException("Missing required variable: {key}")` |
| `BF_USERID` or `BF_PWD` missing/empty | Raise `AuthException("Missing required variable: {key}")` |
| `ConfigurationException` from loader | Catch and re-raise as `AuthException` with the original message |

### `BFDriver`

| Condition | Behaviour |
|---|---|
| `ConfigurationException` from loader in `get_local_db_details` | Catch and re-raise as `BFDriverException("Could not load DB credentials from .env: {original}")` |
| Any exception in `get_token` | Propagate as-is (existing behaviour unchanged) |

---

## Testing Strategy

### Unit Tests

The existing test file is `tests/unit_tests_betfair_objects.py` using Python's built-in `unittest` framework.

**`test_vault` method** — delete entirely (tests Vault which no longer exists).

**New: `test_dotenv_loader`** — covers:
- Successful load and `get_secret` for a known key
- `ConfigurationException` raised when `.env` file path does not exist
- `ConfigurationException` raised when a key is absent
- `ConfigurationException` raised when a key is present but empty
- `.env` file is not modified after `get_secret` calls

**Updated: `test_auth`** — replace vault-based test with:
- `Auth(loader)` succeeds when all five keys are present
- `AuthException` raised for each missing credential key (BF_AppKey, BF_CRT_FILE, BF_KEY_FILE, BF_USERID, BF_PWD)
- `validate_betfair_token` tests remain unchanged (no vault dependency)

**Updated: `test_db_connection`** — replace vault-based setup with a `DotenvLoader` pointing at a test `.env` fixture; assert `get_local_db_details` returns correct dict.

### Property-Based Tests

The project uses Python. The recommended PBT library is **Hypothesis** (`hypothesis==6.112.0`).

Each property-based test should run a minimum of 100 iterations (Hypothesis default is 100; use `@settings(max_examples=100)`).

Tag format in comments: `# Feature: replace-vault-with-dotenv, Property {N}: {property_text}`

**Property 1 test** — `# Feature: replace-vault-with-dotenv, Property 1: Missing required key raises ConfigurationException`
Use `@given(st.sampled_from(REQUIRED_KEYS))` to draw a key, write a `.env` file with that key absent or set to `""`, then assert `ConfigurationException` is raised and the message contains the key name.

**Property 2 test** — `# Feature: replace-vault-with-dotenv, Property 2: Present key returns correct string value`
Use `@given(st.text(min_size=1), st.text(min_size=1))` to generate random key names and values, write them to a temp `.env` file, then assert `get_secret` returns the exact value written.

**Property 3 test** — `# Feature: replace-vault-with-dotenv, Property 3: Auth credential key absence raises AuthException`
Use `@given(st.sampled_from(AUTH_KEYS))` to draw a key, build a loader with that key absent, then assert `AuthException` is raised and the message contains the key name.

**Property 4 test** — `# Feature: replace-vault-with-dotenv, Property 4: Missing DB key raises BFDriverException`
Use `@given(st.sampled_from(DB_KEYS))` to draw a key, build a `DotenvLoader` with that key absent, then assert `BFDriverException` is raised and the message contains the key name.

### Integration / Smoke Tests

The following are not suitable for property-based testing and are verified by static analysis or single-execution checks:

- `hvac` is not importable (verify by attempting `import hvac` and expecting `ImportError`)
- `api/auth/vault/` directory does not exist
- `requirements.txt` contains `python-dotenv==` and does not contain `hvac`
- `.gitignore` contains a `.env` entry
- `.env.example` contains all ten required keys
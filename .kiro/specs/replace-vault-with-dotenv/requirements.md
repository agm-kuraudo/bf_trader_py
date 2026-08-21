# Requirements Document

## Introduction

The bf_trader_py application currently uses HashiCorp Vault (running as a Docker container) to store and retrieve all application secrets: Betfair credentials, certificate file paths, a session token cache, and PostgreSQL connection details. Operating Vault requires starting a Docker container, unsealing it with three keys on every restart, and maintaining a `VAULT_TOKEN` environment variable. This overhead is disproportionate for a personal, single-machine development project.

This feature replaces Vault entirely with a `.env` file loaded via `python-dotenv`. All secrets previously stored in Vault will be defined as flat key-value pairs in the `.env` file. The `hvac` dependency and all `VaultReader` code will be removed. A `.env.example` file with placeholder values will be committed to the repository so the configuration surface is self-documenting. Vault startup scripts and the Vault HCL configuration file will also be removed.

Note: the current Vault usage includes storing and retrieving the Betfair SSO token between runs (`bf_sso_token`). After this change the SSO token will no longer be persisted between runs; a fresh token will be obtained on every application startup via the certificate-based authentication flow. This is an accepted simplification.

## Glossary

- **Application**: The bf_trader_py Python trading application (services: `target_service.py`, `monitor_service.py`).
- **BFDriver**: The `BFDriver` class in `BFDriver.py` that orchestrates authentication and API calls.
- **Auth**: The `Auth` class in `api/auth/auth_details.py` that holds Betfair credentials and the SSO session token.
- **DotenvLoader**: The new module (`api/auth/dotenv_loader.py`) that replaces `VaultReader`; it loads secrets from the `.env` file using `python-dotenv`.
- **EnvFile**: The `.env` file at the project root containing all secret key-value pairs; never committed to version control.
- **EnvExampleFile**: The `.env.example` file at the project root containing all required keys with placeholder values; committed to version control.
- **SSO_Token**: The Betfair non-interactive session token obtained via certificate-based login.
- **Vault**: HashiCorp Vault — the secret management system being removed.
- **VaultReader**: The existing `api/auth/vault/vault_reader.py` module being replaced.

## Requirements

### Requirement 1: Remove Vault Dependency

**User Story:** As a developer, I want to remove HashiCorp Vault and the `hvac` library from the project, so that the application has no dependency on a running Vault container.

#### Acceptance Criteria

1. THE Application SHALL NOT import or reference the `hvac` library anywhere in the codebase.
2. THE Application SHALL NOT contain the `VaultReader` class or the `api/auth/vault/` module.
3. THE Application SHALL NOT reference `VAULT_TOKEN` as a required environment variable at runtime.
4. THE `build/requirements.txt` file SHALL NOT list `hvac` as a dependency.
5. THE `scripts/start_up_vault.ps1` and `scripts/start_up_vault.sh` files SHALL be deleted from the repository.
6. THE `config/config.hcl` Vault configuration file SHALL be deleted from the repository.

---

### Requirement 2: Load Secrets from .env File

**User Story:** As a developer, I want all application secrets loaded from a `.env` file via `python-dotenv`, so that I can manage credentials with a single plain-text file instead of a running service.

#### Acceptance Criteria

1. THE `DotenvLoader` SHALL load the `.env` file from the project root using `python-dotenv` when instantiated.
2. WHEN the `.env` file is not present at the expected path, THE `DotenvLoader` SHALL raise a descriptive `ConfigurationException` identifying the missing file.
3. WHEN a required key is absent or empty in the loaded environment, THE `DotenvLoader` SHALL raise a descriptive `ConfigurationException` identifying the missing key by name.
4. THE `DotenvLoader` SHALL expose a method that accepts a secret key name and returns its string value from the loaded environment.
5. THE `DotenvLoader` SHALL NOT write or mutate the `.env` file at runtime.
6. THE `build/requirements.txt` file SHALL list `python-dotenv` as a dependency with a pinned version.

---

### Requirement 3: Betfair Credentials Loaded from .env

**User Story:** As a developer, I want Betfair credentials and certificate paths read from the `.env` file, so that the `Auth` class can authenticate without relying on Vault.

#### Acceptance Criteria

1. THE `Auth` class SHALL read `BF_AppKey`, `BF_CRT_FILE`, and `BF_KEY_FILE` from the environment loaded by `DotenvLoader`.
2. THE `Auth` class SHALL read `BF_USERID` and `BF_PWD` from the environment loaded by `DotenvLoader`.
3. WHEN `BF_AppKey`, `BF_CRT_FILE`, or `BF_KEY_FILE` is absent or empty after loading, THE `Auth` class SHALL raise an `AuthException` identifying the missing variable by name.
4. WHEN `BF_USERID` or `BF_PWD` is absent or empty after loading, THE `Auth` class SHALL raise an `AuthException` identifying the missing variable by name.
5. THE `Auth` class SHALL NOT call any method on `VaultReader` or any Vault client.
6. THE `Auth` class SHALL NOT contain the `get_credentials_from_vault` method.

---

### Requirement 4: SSO Token Obtained Fresh on Each Run

**User Story:** As a developer, I want the application to obtain a fresh Betfair SSO token via certificate authentication on each startup, so that I no longer need Vault to cache the token between runs.

#### Acceptance Criteria

1. WHEN `BFDriver.get_token` is called, THE `BFDriver` SHALL authenticate to the Betfair API using the certificate-based login flow to obtain a new SSO_Token.
2. THE `BFDriver` SHALL NOT call any `update_secret` or equivalent method to persist the SSO_Token to an external store.
3. THE `BFDriver` SHALL NOT attempt to read a previously cached SSO_Token from Vault or any other external store.
4. THE `BFDriver` SHALL NOT instantiate a `VaultReader` object.

---

### Requirement 5: PostgreSQL Credentials Loaded from .env

**User Story:** As a developer, I want PostgreSQL connection details read from the `.env` file, so that `BFDriver` can connect to the database without Vault.

#### Acceptance Criteria

1. THE `BFDriver.get_local_db_details` method SHALL read `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PWD` from the environment loaded by `DotenvLoader`.
2. WHEN any of `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, or `DB_PWD` is absent or empty after loading, THE `BFDriver` SHALL raise a `BFDriverException` identifying the missing variable by name.
3. THE `BFDriver.get_local_db_details` method SHALL NOT call `VaultReader.read_secret` or any Vault client method.

---

### Requirement 6: .env File Excluded from Version Control

**User Story:** As a developer, I want the `.env` file excluded from git, so that secrets are never committed to the repository.

#### Acceptance Criteria

1. THE `.gitignore` file SHALL contain an entry that matches `.env` at the project root.
2. THE `.env` file SHALL NOT be tracked by git at any point.
3. THE `EnvExampleFile` (`.env.example`) SHALL be committed to the repository and tracked by git.

---

### Requirement 7: .env.example Documents All Required Keys

**User Story:** As a developer, I want a `.env.example` file committed to the repository, so that I can see all required configuration keys and their expected format at a glance.

#### Acceptance Criteria

1. THE `EnvExampleFile` SHALL contain placeholder (non-secret) values for every key required by the Application: `BF_AppKey`, `BF_CRT_FILE`, `BF_KEY_FILE`, `BF_USERID`, `BF_PWD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PWD`.
2. THE `EnvExampleFile` SHALL use clearly recognisable placeholder values (e.g. `your_app_key_here`, `./certs/client.crt`) that indicate the expected format without containing real secrets.
3. THE `EnvExampleFile` SHALL include a comment block at the top explaining that the file must be copied to `.env` and populated with real values before running the application.
4. WHEN a new required key is added to the Application, THE `EnvExampleFile` SHALL be updated to include that key.

---

### Requirement 8: Startup Scripts Updated

**User Story:** As a developer, I want the application startup scripts to no longer reference Vault, so that starting the application does not depend on Vault being available.

#### Acceptance Criteria

1. THE `scripts/run_target_service.ps1` and `scripts/run_target_service.sh` scripts SHALL NOT reference Vault or `VAULT_TOKEN`.
2. THE `scripts/run_monitor_service.ps1` and `scripts/run_monitor_service.sh` scripts SHALL NOT reference Vault or `VAULT_TOKEN`.
3. THE `scripts/start_up_postgres.ps1` and `scripts/start_up_postgres.sh` scripts SHALL NOT reference Vault.
4. WHEN a startup script sets environment variables for the Application, THE script SHALL NOT include `VAULT_TOKEN` as one of those variables.

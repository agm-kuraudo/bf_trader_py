# Implementation Plan: Replace Vault with .env (SP-292)

## Overview

Replace HashiCorp Vault with `python-dotenv` across the bf_trader_py codebase. Removes the `VaultReader` module, `hvac` dependency, and all Vault infrastructure scripts. A new `DotenvLoader` class replaces `VaultReader` as the sole secret-retrieval mechanism. The Betfair SSO token cache (previously stored in Vault) is dropped — a fresh token is obtained on every startup.

Scope boundary: This ticket covers the Vault to .env migration only. If unrelated issues are discovered during implementation (broken dependencies, API changes, Docker issues), log them as separate defect/backlog tickets linked to SP-292 and do NOT fix inline.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": [1] },
    { "wave": 2, "tasks": [2, 4, 5] },
    { "wave": 3, "tasks": [3] },
    { "wave": 4, "tasks": [6] },
    { "wave": 5, "tasks": [7] },
    { "wave": 6, "tasks": [8] },
    { "wave": 7, "tasks": [9, 10, 11] },
    { "wave": 8, "tasks": [12] },
    { "wave": 9, "tasks": [13] }
  ]
}
```

## Tasks

- [x] 1. Create feature branch and set up local environment
  - Check out `master` and create branch `SP-292-replace-vault-with-dotenv`
  - All commits on this branch must use prefix `SP-292:`
  - A `.venv` directory exists at the project root (renamed from `venv`) — activate it before working
  - Activate on Windows: `.venv\Scripts\activate` / Linux: `source .venv/bin/activate`
  - Verify dependencies install cleanly: `pip install -r build/requirements.txt`
  - Note: `.venv` is already listed in `.gitignore` so no further action needed there
  - _Requirements: all_

- [x] 2. Delete Vault files and module
  - Delete `api/auth/vault/vault_reader.py`
  - Delete `api/auth/vault/__init__.py`
  - Delete `api/auth/vault/` directory
  - Delete `config/config.hcl`
  - Delete `config/Docker Run Command.txt`
  - Delete `scripts/start_up_vault.ps1`
  - Delete `scripts/start_up_vault.sh`
  - Delete `build/vault_build.ps1`
  - Delete `build/vault_build.sh`
  - Delete `build/vault_config.json`
  - _Requirements: 1_

- [x] 3. Update dependencies and Dockerfile
  - In `build/requirements.txt`: remove `hvac==2.3.0`, add `python-dotenv==1.0.1`
  - In `build/betfair_app.dockerfile`: remove the `ENV VAULT_TOKEN=...` hardcoded line
  - Reinstall `.venv` dependencies after requirements change
  - _Requirements: 1, 2_

- [x] 4. Update .gitignore
  - Add `.env` entry (no leading slash, covers root and nested `.env` files)
  - Verify the existing `/web/.env` entry is preserved
  - Verify `.venv` is also listed
  - _Requirements: 6_

- [x] 5. Create .env.example
  - Create `.env.example` at the project root
  - Include a comment block at the top explaining the file must be copied to `.env` and populated before running
  - Include all ten keys with placeholder values: `BF_AppKey`, `BF_CRT_FILE`, `BF_KEY_FILE`, `BF_USERID`, `BF_PWD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PWD`
  - Use recognisable placeholder values (e.g. `your_app_key_here`, `./certs/client-2048.crt`)
  - _Requirements: 7_

- [x] 6. Create DotenvLoader
  - Create `api/auth/dotenv_loader.py`
  - Implement `ConfigurationException(Exception)`
  - Implement `DotenvLoader.__init__(env_path=None)` using `dotenv_values()` (not `load_dotenv`); default path resolved via `Path(__file__).resolve().parents[2] / ".env"`
  - Raise `ConfigurationException("env file not found: {path}")` when `.env` does not exist
  - Implement `DotenvLoader.get_secret(key: str) -> str`
  - Raise `ConfigurationException("Required key '{key}' is missing from .env")` when key is absent
  - Raise `ConfigurationException("Required key '{key}' is empty in .env")` when key is present but empty
  - _Requirements: 2_

- [x] 7. Rewrite Auth class
  - In `api/auth/auth_details.py`: remove `import api.auth.vault.vault_reader`
  - Remove class-level `os.getenv(...)` calls that fire at import time
  - Update `Auth.__init__` to accept a `DotenvLoader` instance; read `BF_AppKey`, `BF_CRT_FILE`, `BF_KEY_FILE` from it; raise `AuthException` identifying missing key
  - Add `Auth.get_credentials` method reading `BF_USERID` and `BF_PWD` from loader; raise `AuthException` identifying missing key
  - Remove `get_credentials_from_vault` method entirely
  - Update `api/auth/__init__.py` docstring to remove Vault references
  - _Requirements: 3_

- [x] 8. Rewrite BFDriver
  - In `BFDriver.py`: remove `from api.auth.vault.vault_reader import VaultReader as Vault, VaultException`
  - Add `from api.auth.dotenv_loader import DotenvLoader, ConfigurationException`
  - In `__init__`: replace `self.__vault_obj = Vault()` with `self.__loader = DotenvLoader()`; pass `self.__loader` to `bf_auth.Auth(self.__loader)`
  - Rewrite `get_local_db_details` to read five DB keys from `self.__loader.get_secret(...)`; catch `ConfigurationException` and re-raise as `BFDriverException`
  - Rewrite `get_token` to call `self.__auth_obj.get_credentials()` then always call `call_auth` for a fresh token; remove token validation and `update_secret` call
  - _Requirements: 4, 5_

- [x] 9. Update service error messages
  - In `target_service.py`: update error messages referencing Vault
  - In `monitor_service.py`: update error messages referencing Vault
  - _Requirements: 8_

- [x] 10. Update unit tests
  - In `tests/unit_tests_betfair_objects.py`: remove `from api.auth.vault.vault_reader import VaultReader, VaultException`
  - Delete `test_vault` method
  - Add `test_dotenv_loader`: successful load, `ConfigurationException` for missing file, absent key, empty key, file not mutated
  - Rewrite `test_auth` using `Auth(DotenvLoader(...))` with temp `.env` fixture; test `AuthException` for each of five missing credential keys
  - Rewrite `test_db_connection` using `DotenvLoader` pointing at test `.env` fixture
  - _Requirements: 2, 3, 5_

- [x] 11. Add property-based tests
  - Add `hypothesis==6.112.0` to `build/requirements.txt`
  - Property 1: missing/empty key raises `ConfigurationException` with key name in message; tag `# Feature: replace-vault-with-dotenv, Property 1`
  - Property 2: present key returns exact string value written; tag `# Feature: replace-vault-with-dotenv, Property 2`
  - Property 3: missing Auth credential key raises `AuthException` with key name in message; tag `# Feature: replace-vault-with-dotenv, Property 3`
  - Property 4: missing DB key raises `BFDriverException` with key name in message; tag `# Feature: replace-vault-with-dotenv, Property 4`
  - _Requirements: 2, 3, 5_

- [x] 12. Create README.md
  - No README exists in the project — create `README.md` at the project root
  - Include sections: Project Overview, Prerequisites, Setup (clone repo, activate `.venv`, install dependencies, create `.env` from `.env.example`), Running the Services (target_service.py, monitor_service.py), Running Tests, Project Structure
  - The Setup section must clearly document the `.env` approach — copy `.env.example` to `.env` and populate with real values; no Vault required
  - _Requirements: 7, 8_

- [x] 13. Smoke and static verification
  - Verify `import hvac` raises `ImportError` in the `.venv`
  - Verify `api/auth/vault/` directory no longer exists
  - Verify `build/requirements.txt` contains `python-dotenv==` and does not contain `hvac`
  - Verify `.gitignore` contains `.env` entry
  - Verify `.env.example` contains all ten required keys
  - Create real `.env` file (not committed) with actual credentials
  - Start Postgres Docker container only (no Vault)
  - Run `target_service.py` or `monitor_service.py` and confirm it starts, authenticates, and connects to DB
  - Log any unrelated failures as new Jira defects linked to SP-292 — do not fix inline
  - _Requirements: all_

## Notes

- Use `pathlib.Path` throughout for all file path handling — no hardcoded path separators
- Scripts must work on both Windows (`.ps1`) and Linux (`.sh`) — provide both variants where applicable
- `.venv` must never be committed — verify it is in `.gitignore`
- The `.env` file must never be committed — only `.env.example` goes to git
- The SSO token cache is intentionally dropped — fresh auth on every run is the new behaviour

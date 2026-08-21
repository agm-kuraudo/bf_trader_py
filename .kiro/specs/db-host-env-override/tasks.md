# Implementation Plan

## Task Dependency Graph
```json
{
  "waves": [
    {"tasks": ["1", "2", "3"]},
    {"tasks": ["4"]}
  ]
}
```

- [x] 1. Update `.env` — set DB_HOST to Docker container name
  - Change `DB_HOST=localhost` to `DB_HOST=my_postgres`
  - No other values in `.env` should be modified
  - _Requirements: 2.1, 2.2, 2.3_

- [x] 2. Update `.env.example` — set DB_HOST and add documentation
  - Change `DB_HOST=172.19.0.3` to `DB_HOST=my_postgres`
  - Change `DB_NAME=postgres` to `DB_NAME=bf_trader` (align with actual `.env`)
  - Add comments explaining:
    - `my_postgres` is the Docker container name for the Postgres instance
    - Docker DNS resolves this automatically for containers on `my_trading_network`
    - For local development on Windows, add `127.0.0.1 my_postgres` to `C:\Windows\System32\drivers\etc\hosts`
  - _Requirements: 2.3, 2.4_

- [x] 3. Add Windows hosts file entry (MANUAL step)
  - **This is a manual one-time setup step — do not automate**
  - Add `127.0.0.1 my_postgres` to `C:\Windows\System32\drivers\etc\hosts`
  - Verify with `ping my_postgres` — should resolve to `127.0.0.1`
  - _Requirements: 2.2_

- [x] 4. Verify connectivity
  - Run existing unit tests to confirm DotenvLoader reads `DB_HOST=my_postgres` correctly
  - Verify Postgres connectivity locally via `my_postgres` hostname
  - Confirm no other tests are broken by the configuration change
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

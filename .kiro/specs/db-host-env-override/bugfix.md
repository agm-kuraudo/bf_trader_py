# Bugfix Requirements Document

## Introduction

The `DB_HOST` value in `.env` must be manually changed when switching between local development (where Postgres is accessed via `localhost` through port-mapping) and Docker execution (where Postgres is accessed via Docker DNS on `my_trading_network`). The fix uses the Docker container name `my_postgres` as the canonical `DB_HOST` value. Inside Docker, this resolves via Docker DNS automatically. On local Windows dev, a hosts file entry (`127.0.0.1 my_postgres`) makes the same name resolve to localhost. No code changes to `DotenvLoader` are required.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the application runs inside a Docker container on `my_trading_network` AND `.env` has `DB_HOST=localhost` THEN the system cannot connect to Postgres because `localhost` inside the container does not resolve to the Postgres container

1.2 WHEN a developer switches between local dev and Docker execution THEN the system requires manual editing of the `.env` file to change `DB_HOST` between `localhost` and the Docker container address (e.g. `172.19.0.3` or `my_postgres`)

1.3 WHEN `.env.example` documents `DB_HOST=172.19.0.3` as the default THEN the system misleads developers because a hardcoded IP is fragile and only works for Docker execution, not local dev

### Expected Behavior (Correct)

2.1 WHEN the application runs inside a Docker container on `my_trading_network` AND `.env` has `DB_HOST=my_postgres` THEN the system SHALL resolve `my_postgres` to the Postgres container via Docker DNS and connect successfully

2.2 WHEN the application runs locally on Windows AND `.env` has `DB_HOST=my_postgres` AND the Windows hosts file contains `127.0.0.1 my_postgres` THEN the system SHALL resolve `my_postgres` to `127.0.0.1` and connect to the port-mapped Postgres container

2.3 WHEN a developer switches between local dev and Docker execution THEN the system SHALL NOT require any `.env` file modifications — the same `DB_HOST=my_postgres` value works in both environments

2.4 WHEN `.env.example` is read by a new developer THEN the system SHALL document that `DB_HOST=my_postgres` requires a hosts file entry (`127.0.0.1 my_postgres`) for local development

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `DotenvLoader.get_secret("DB_HOST")` is called THEN the system SHALL CONTINUE TO return the value from the `.env` file without any code changes to the loader

3.2 WHEN the `.env` file is missing THEN the system SHALL CONTINUE TO raise a `ConfigurationException`

3.3 WHEN `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PWD` are read from `.env` THEN the system SHALL CONTINUE TO return their values unchanged

3.4 WHEN Betfair credentials (`BF_AppKey`, `BF_CRT_FILE`, `BF_KEY_FILE`, `BF_USERID`, `BF_PWD`) are read from `.env` THEN the system SHALL CONTINUE TO return their values unchanged

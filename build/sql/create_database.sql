CREATE DATABASE bf_trader
    WITH
    OWNER = postgres
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8'
    LOCALE_PROVIDER = 'libc'
    TABLESPACE = pg_default
    CONNECTION LIMIT = -1
    IS_TEMPLATE = False;

\c bf_trader

CREATE SCHEMA bf;

CREATE TABLE IF NOT EXISTS bf.betfair_object_ids
(
    object_type text COLLATE pg_catalog."default",
    object_name text COLLATE pg_catalog."default",
    object_id integer,
    last_updated timestamp with time zone
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.betfair_object_ids
    OWNER to postgres;

CREATE TABLE IF NOT EXISTS bf.log_file
(
    id uuid NOT NULL,
    "timestamp" timestamp with time zone,
    message text COLLATE pg_catalog."default"
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.log_file
    OWNER to postgres;

CREATE TABLE IF NOT EXISTS bf.market_table
(
    "timestamp" timestamp with time zone,
    market_id text COLLATE pg_catalog."default",
    runner_id text COLLATE pg_catalog."default",
    odds text COLLATE pg_catalog."default"
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.market_table
    OWNER to postgres;

CREATE TABLE IF NOT EXISTS bf.target
(
    target_id text COLLATE pg_catalog."default",
    event_id text COLLATE pg_catalog."default",
    market_id text COLLATE pg_catalog."default",
    runner_ids text COLLATE pg_catalog."default",
    start_time timestamp with time zone,
    status text COLLATE pg_catalog."default",
    update_frequency integer,
    last_updated timestamp with time zone,
    notes text COLLATE pg_catalog."default"
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.target
    OWNER to postgres;

CREATE TABLE IF NOT EXISTS bf.quality_run
(
    run_id uuid NOT NULL,
    run_started timestamp with time zone,
    run_finished timestamp with time zone,
    look_back_start timestamp with time zone,
    look_back_end timestamp with time zone,
    matches_verified integer,
    matches_passed integer,
    matches_failed integer,
    overall_alert boolean,
    status text COLLATE pg_catalog."default",
    notes text COLLATE pg_catalog."default"
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.quality_run
    OWNER to postgres;

CREATE TABLE IF NOT EXISTS bf.quality_match_result
(
    run_id uuid NOT NULL,
    target_id text COLLATE pg_catalog."default",
    market_id text COLLATE pg_catalog."default",
    present_outcome text COLLATE pg_catalog."default",
    coverage_outcome text COLLATE pg_catalog."default",
    consistency_outcome text COLLATE pg_catalog."default",
    useful_outcome text COLLATE pg_catalog."default",
    overall_outcome text COLLATE pg_catalog."default",
    evidence jsonb
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS bf.quality_match_result
    OWNER to postgres;

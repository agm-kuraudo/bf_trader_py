# SP-294 — Betfair / Pi cleanup

Cleanup of Docker debris on the always-on Raspberry Pi plus two prevention
measures, and a build-context fix in this repo. This note records what was
actually done vs. what the original ticket assumed, because a lot of the debris
had already been cleared by earlier remediation (SP-328, SP-336) by the time
this ticket was picked up.

## 1. `.dockerignore` (this repo) — DONE

The compose build uses `context: .` and the Dockerfile ends with `COPY . /app`,
so without a `.dockerignore` the **entire repo** was shipped as build context —
~344 MB (SP-328 observation), almost all of it `.venv/` (~343 MB) plus VCS
history and tool caches. Added [`.dockerignore`](../.dockerignore).

**Measured** with a throwaway `COPY . /ctx` build (Docker Desktop, BuildKit):

| | build context |
|---|---|
| before (no `.dockerignore`) | ~344 MB |
| after | ~0.75 MB |

Verified by exporting the built image that the runtime-critical paths **still
ship** — this was the main risk:

- `certs/client-2048.crt` + `.key` — Betfair certificate login reads
  `BF_CRT_FILE` / `BF_KEY_FILE`, which `.env` points at `./certs/...`.
- `config/strategy.yaml` — loaded by `logic/simpleStategy.py` at runtime.
- `build/requirements.txt` — the Dockerfile's first `COPY`.

…and that `.venv`, `.git`, caches, `tests/`, `docs/`, `charts/` and `.env` are
**excluded**. The host `.env` is deliberately excluded (secrets are injected at
run time via compose `env_file`, not baked into the image).

### Regression fixed post-merge: the `log/` directory must ship

First deploy after merge failed post-deploy verification. The capture crashed on
startup with:

```
FileNotFoundError: '/app/output/../log/runlog261026.log'
```

`output/log.py` writes `log/runlogYYMMDD.log` via `open(..., "a+")`, which does
**not** create missing parent directories. The original `.dockerignore`
excluded `log/` wholesale, so `/app/log/` did not exist in the image and the
logger crashed before any Monitor cycle ran. The old "ship everything" build had
masked this because the local `log/` directory was always copied in.

Fix: ship the (empty) `log/` **directory** but not its stale local `*.log`
files — exclude the contents and re-include a placeholder:

```
log/*
!log/.gitkeep
```

Added `log/.gitkeep`. Verified in a rebuilt audit image that `/ctx/log/` exists
with only `.gitkeep` (no stale logs), while `.venv`/`.git` stay excluded and the
runtime-critical files still ship. (The `charts/` dir has the same latent
pattern for `analyse_service.py`, but that is not in the capture deploy path —
the research scripts build their own `charts_dir`.)

### Second regression fixed: `.env` must be bind-mounted, not baked in

Next deploy got past `log/` but crashed with:

```
api.auth.dotenv_loader.ConfigurationException: env file not found: /app/.env
```

`api/auth/dotenv_loader.DotenvLoader` reads the `.env` **file** at `/app/.env`
via `dotenv_values()` — it does **not** read environment variables. So the
compose `env_file:` directive (which only sets env vars) never fed the app; the
app had **always** needed the `.env` file physically at `/app/.env`, and the
only reason it worked before was that the old "copy everything" build **baked
`.env` — secrets and all — into the image**. The SP-294 `.dockerignore`
correctly stopped shipping `.env`, which exposed this.

This is a security improvement, not just a bug: secrets no longer live in image
layers. The fix keeps `.env` out of the image and bind-mounts the host copy
read-only at runtime, in `docker-compose.yml`:

```yaml
    volumes:
      - ./.env:/app/.env:ro
```

Compose applies this to both the recreate (`up -d --build`) and the ephemeral
`run --rm` capture, so the scheduled Rundeck runs get it too.

**Verified on the Pi** (via a `docker-compose.override.yml` carrying the mount
while iterating, then folded into the tracked compose): a `docker compose run
--rm bf_capture python monitor_service.py` cycle found `/app/.env`, connected,
authenticated and logged **"Ending run successfully"** (0 targets due — a valid
pass per `verify_deploy.py`).

## 2. Pi Docker debris — mostly already cleared; residual pruned

State when picked up (read-only `docker` checks on the Pi) vs. the ticket's
assumptions:

| Ticket item | Assumed | Reality when picked up |
|---|---|---|
| ~608 exited containers | prune | only 1 (a normal `bf_monitor_service` exit) — mass clear already done |
| dangling image `d91da2e69804` | remove | already gone |
| `rundeck-image-01` image | remove | already gone (per ticket comments, SP-336) |

Residual actually pruned this ticket (safe, non-`-a`):

- `docker image prune -f` — removed 4 unreferenced dangling images (the in-use
  one was correctly retained).
- `docker builder prune -f` — reclaimed ~3.07 GB of **unused** build cache.

Docker data-root is on `/mnt/data` (external disk), not `/`. After: images
8 → 4, build cache 4.46 GB → 1.39 GB, `/mnt/data` at 11 % (49 GB free). The
`my_postgres` / `my_pgadmin` stack was untouched throughout.

## 3. Prevention: Docker log-rotation — DONE

Root cause of the SP-336 outage was an unbounded container json log (a runaway
1.2 GB `my_pgadmin` log filled the disk and crash-looped Postgres). Added global
log limits to `/etc/docker/daemon.json` (original backed up to
`daemon.json.bak-sp294-<date>`):

```json
{
  "data-root": "/mnt/data/docker",
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

Caps each container at 3 × 10 MB = 30 MB. Applied with `systemctl restart
docker`; `my_postgres` / `my_pgadmin` (`restart: unless-stopped`) came back
automatically and Postgres reported `accepting connections`.

**Caveat:** global `log-opts` apply only to containers **created after** the
restart. A freshly-created container was confirmed to inherit
`max-file:3 max-size:10m`. The source copy of `daemon.json` lives at
[`scripts/pi/daemon.json`](../scripts/pi/daemon.json).

### 3a. Recreated the long-lived data containers so they are capped too — DONE

`my_postgres` and `my_pgadmin` are never redeployed (Postgres holds data;
pgadmin is standalone), so the daemon default would *never* reach them — and
pgadmin was the SP-336 culprit. They are not compose-managed (started via raw
`docker run`) and each stores data in an **anonymous** Docker volume:

- `my_postgres` → volume `a2842983…` at `/var/lib/postgresql/data`
- `my_pgadmin`  → volume `f9a00070…` at `/var/lib/pgadmin`

Recreated both **once** with [`scripts/pi/recreate_with_logcap.sh`](../scripts/pi/recreate_with_logcap.sh),
which: reads each container's env (incl. secrets) from the live container so
values never leave the Pi; stops + renames the old one to `<name>_old_sp294` as
a rollback handle; runs a new container with the **same name, image, network
(`my_trading_network`), port, the same anonymous volume reattached by hash**,
and explicit `--log-opt max-size=10m --log-opt max-file=3`; verifies; and rolls
back automatically if `docker run` fails. The script is idempotent/resumable.

**Verified after recreate:** both `logopts=map[max-file:3 max-size:10m]`;
Postgres `accepting connections`; same volume hash reattached; data intact
(`bf.market_table` 55,663 rows, `bf.target` 59, `bf.log_file` 4,788; databases
`bf_trader` + `market_data` present); and a throwaway client on
`my_trading_network` reaching `my_postgres:5432` (same path the ephemeral
`bf_capture` run uses). The `_old_sp294` rollback containers were then removed
(their volumes are reused by the live containers, so data is untouched).

Done in the quiet window between the 15-min Betfair capture runs. The capture
itself is an ephemeral `docker compose … run --rm bf_capture` that only
*connects* to Postgres, so recreating the DB container between runs is safe.

## 4. Prevention: disk-usage alert — interim local alert DONE; Telegram = follow-up

Proper alerting wants the Telegram delivery layer (SP-357 / SP-395), which is
out of scope here. Interim **local-only** alert installed as a stopgap — no
network, no secrets:

- [`scripts/pi/disk_alert.sh`](../scripts/pi/disk_alert.sh) → installed at
  `/usr/local/bin/disk_alert.sh`. Checks `/` and `/mnt/data`; on breach
  (default ≥ 85 %) logs a `user.warning` to journald under tag `disk-alert` and
  writes `/tmp/disk_alert.breached`; clears the marker when back under
  threshold. Always exits 0 so cron never treats a full disk as a script error.
- cron: `/etc/cron.d/disk-alert`, every 15 min as root.

Inspect with: `journalctl -t disk-alert`. Tested with a forced
`DISK_ALERT_THRESHOLD=1` run (emitted the warnings) and a normal run (logged
`ok`, cleared the marker).

**Follow-up:** a Telegram-based disk alert is tracked separately (reuses the
SP-395 / SP-357 bot-token-in-`.env` + chat-id-in-config approach). When that
lands it can watch the same `disk-alert` journald output rather than
re-implementing the check.

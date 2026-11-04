# SP-487 — Monitor aborts whole run on a single transient DNS failure

Two-part fix for a bug found in the Betfair Trader 0.8 scope: the monitor
service crashed its entire run the first time one Betfair API call hit a
*transient* DNS resolution failure, throwing away a monitor loop that had been
running healthily for hundreds of iterations.

- **Jira:** [SP-487](https://amainit.atlassian.net/browse/SP-487) (Bug)
- **Part 1 — application retry (this repo):** done, tested.
- **Part 2 — Pi Docker DNS hardening (host config):** **APPLIED 2026-11-04
  ~12:30 GMT** (no capture running at the time). Verified: Docker active,
  `my_postgres` / `my_pgadmin` back up, container `resolv.conf` shows
  `ExtServers: [192.168.0.1 1.1.1.1 8.8.8.8]`, and a fresh busybox container
  resolves `api.betfair.com`. Whether it reduces blip frequency is **not yet
  measured** — watch Rundeck failures over the next few days of in-play.

## The bug

Rundeck Betfair job failed with exit code 1. The traceback ended in:

```
socket.gaierror: [Errno -3] Temporary failure in name resolution
Failed to resolve 'api.betfair.com'
```

wrapped up through `CallException` → `MonitorServiceException` → process exit 1.

Diagnosis from the Rundeck execution logs (2026-11-03): every failed run
completed many successful monitor loops and then died on the **first** DNS
hiccup. The `Step 1 Complete` loop counter immediately before each death:

| Exec | Time | Loops completed before death | Fatal cause |
|------|------|------------------------------|-------------|
| 24855 | 18:16 | 374 | transient `gaierror -3` |
| 24856 | 19:08 | 462 | transient `gaierror -3` |
| 24857 | 19:19 | 54 | transient `gaierror -3` |
| 24858 | 19:54 | 320 | transient `gaierror -3` |
| 24859 | 21:15 | 854 | transient `gaierror -3` |

The host and containers resolved `api.betfair.com` fine on demand throughout —
the failure is a genuine one-in-a-thousand transient, not a hard outage. It
correlated with matches going **in-play**: more call volume per second raises
the odds of hitting the transient, it is not a different root cause.

**Root cause:** the Pi has no local DNS cache, so every lookup goes through
Docker's embedded resolver (127.0.0.11) and out to the consumer router's DNS
forwarder (192.168.0.1 / 192.168.1.1), which occasionally drops a query under
load. The *defect* that turned a dropped query into an outage was in the app:
`api/call.py` `Call.call()` caught every exception and re-raised a fatal
`CallException` with **zero retry**, so a single failed lookup discarded an
800+ iteration loop.

## 1. Application retry — DONE (this repo)

`api/call.py` `Call.call()` now wraps the HTTP request in a bounded
retry-with-backoff:

- Retries **only** transient transport errors — `requests.exceptions.ConnectionError`
  and `requests.exceptions.Timeout`. `ConnectionError` is where the DNS
  `gaierror` ("Temporary failure in name resolution") surfaces.
- Does **not** retry a real HTTP response (any status code is returned as-is for
  the caller to interpret) or a non-transport error (e.g. a malformed URL still
  raises `CallException` on the first attempt — preserves the existing
  `test_call` contract in `tests/unit_tests_betfair_objects.py`).
- Defaults (module-level constants, mirroring the `scripts/check_quality.py`
  `CONNECT_ATTEMPTS` precedent), all overridable via the `Call` constructor so
  tests drive them fast:
  - `RETRY_ATTEMPTS = 4` — first try + up to 3 retries.
  - `RETRY_BACKOFF_BASE_S = 0.5` — exponential backoff 0.5s → 1s → 2s.
  - `REQUEST_TIMEOUT_S = 30` — per-request timeout so a hung connection cannot
    stall forever (also makes `Timeout` reachable).

**This part alone stops the crashes:** a momentary blip is now waited out and
retried instead of killing the run. Tests: `tests/unit_tests_call_retry.py`
(retry-then-succeed, exhaustion raises after N, no-retry on non-transient,
no-retry on real response, backoff growth).

## 2. Pi Docker DNS hardening — APPLIED (host config)

Independent safety net for the *other* failure mode: the first DNS server going
properly unhealthy (not just a momentary blip). Adds fallback resolvers so the
container's resolver can fail over off the router instead of depending on it
alone.

### Why router-first, public-DNS-as-backup

Docker's embedded resolver (127.0.0.11) forwards to the `dns` list in order. The
stub resolver fails over to the next server on a **timeout / no-response** (the
"router dropped the query" case — the one we hit), but generally **not** on an
authoritative failure answer (SERVFAIL / NXDOMAIN). Failover also costs a
per-server resolver timeout before it moves on.

Given `api.betfair.com` is behind Cloudflare (short TTLs ⇒ constant re-lookups)
and in-play drives high lookup volume, sending **all** lookups to a public
resolver first would (a) put every resolution on the internet path and (b) risk
public-resolver rate-limiting of an aggressive client. The router is on the LAN,
sub-millisecond, and works the large majority of the time. So:

```json
"dns": ["192.168.0.1", "1.1.1.1", "8.8.8.8"]
```

Router first (fast, local, normally fine); public resolvers as genuine backup
for when the router forwarder drops or is slow.

**Honest limitation:** this adds fallback *resolvers*, not a local *cache* — it
does not reduce lookup volume. The app-level retry (§1) is what actually stops
the outages; this just reduces how often a lookup has to fail over. If blips
persist after this, the next step is a caching resolver on the Pi (dnsmasq is
already installed, or systemd-resolved with caching) — that is a bigger change
with more blast radius and should be its **own** follow-up ticket, not bolted
onto this one.

### The interaction between the two parts (so a future reader is not surprised)

The app retry has no visibility of which DNS server is used — that is entirely
below `requests`. On a retry the OS/Docker resolver decides the server; if it
does not rotate, a retry may re-hit the same (first) server, so the retry's
value is "give the transient time to clear," while the multi-server `dns` list
is the separate mechanism that handles a server being down. They are
complementary, not redundant.

### Apply steps (run on the Pi; `sudo`)

A backup of the original file was already taken during investigation:
`/etc/docker/daemon.json.bak.sp487`. Re-take it if applying on a later day.

1. Confirm **no capture container is running** first (do not restart Docker
   mid-capture — a daemon restart kills the container outright; the §1 retry
   does NOT save an in-flight run from a daemon restart):

   ```bash
   docker ps --format '{{.Names}}' | grep bf_capture && echo "CAPTURE RUNNING - WAIT" || echo "clear to proceed"
   ```

2. Add the `dns` key to `/etc/docker/daemon.json` (keep the existing keys —
   `data-root`, `log-driver`, `log-opts`). Resulting file:

   ```json
   {
     "data-root": "/mnt/data/docker",
     "log-driver": "json-file",
     "log-opts": { "max-size": "10m", "max-file": "3" },
     "dns": ["192.168.0.1", "1.1.1.1", "8.8.8.8"]
   }
   ```

3. Validate the JSON before restarting (a malformed `daemon.json` stops Docker
   from starting):

   ```bash
   sudo python3 -c "import json,sys; json.load(open('/etc/docker/daemon.json')); print('daemon.json OK')"
   ```

4. Restart the daemon (this **restarts all containers** — `my_postgres`,
   `my_pgadmin` — brief outage):

   ```bash
   sudo systemctl restart docker
   ```

5. Verify containers came back and a fresh container resolves via the new list:

   ```bash
   docker ps --format '{{.Names}} {{.Status}}'
   docker run --rm busybox nslookup api.betfair.com
   docker exec my_postgres cat /etc/resolv.conf   # ExtServers should list the new dns servers
   ```

### Rollback

```bash
sudo cp -av /etc/docker/daemon.json.bak.sp487 /etc/docker/daemon.json
sudo systemctl restart docker
```

## Definition of done (per the deployable-work DoD)

- [x] Retry/backoff in `call.py` + unit tests (fast suite green).
- [x] Pi `daemon.json` DNS hardening applied and verified (§2) — 2026-11-04.
- [ ] `bf_trader_py` code deployed to the Pi and a monitor run observed surviving
      past the point earlier runs died (a transient blip retried, not fatal).
- [ ] Completion comment on SP-487 noting the deploy was done and verified; then
      move to Done. Until deployed, SP-487 stays In Progress / Mostly Done.

---
inclusion: auto
name: pi-access
description: How to SSH into the always-on Raspberry Pi to run diagnostics or (when directed) remediation. Use when a task involves the Pi, the deployed Betfair capture, checking Pi disk/containers/logs, or running commands on the Pi.
---

# Raspberry Pi Access (SP-337)

The always-on Raspberry Pi hosts the deployed Betfair capture stack and other
scheduled jobs. The assistant has direct, key-based SSH access to it over the
local network so diagnostics can be run without relaying commands by hand.

## Connection

- **Auth:** key-based only (ed25519), no password. Private key on the operator
  machine at `~/.ssh/pi_kiro_assistant` (Windows: `C:\Users\<you>\.ssh\pi_kiro_assistant`).
- **Host / user:** not hardcoded here. They live in `bf_trader_py/.env` (gitignored)
  as `PI_HOST` and `PI_USER`. Read those values rather than assuming them.
- **Client alias:** an `~/.ssh/config` entry named `pi` is set up, so connections
  are simply `ssh pi "<command>"`.

```powershell
# Preferred (uses the ~/.ssh/config alias)
ssh pi "df -h"

# Explicit form (if the alias is missing) — substitute values from bf_trader_py/.env
ssh -i "$env:USERPROFILE\.ssh\pi_kiro_assistant" <PI_USER>@<PI_HOST> "df -h"
```

Note (Windows/PowerShell): `ssh` writes its "known hosts" first-connect warning to
stderr, which PowerShell surfaces as an error even on success. Judge success by the
command's actual stdout, not by the presence of that warning.

## Permission boundary — IMPORTANT

- **Read-only by default.** Run only diagnostic / read-only commands (e.g. `df -h`,
  `docker ps`, `du`, reading logs, `psql` SELECTs).
- **Remediation requires explicit permission.** Do NOT run anything that changes
  state on the Pi — `docker prune`/`rm`/`restart`, writes, service restarts,
  deploys, package installs, `sudo` — unless the operator explicitly directs or
  permits it for that action.
- **Local network only.** Access works on the LAN; there is no remote/tunnelled
  path. If the Pi is unreachable, it is likely off-network, not a new access route
  to create.
- The key sits on the normal login user, so a full shell is technically available;
  the read-only limit is a behavioural rule, not an OS restriction. Respect it.

## Revoking / troubleshooting

- If SSH is refused on port 22, the daemon may be off: it is normally enabled
  always-on (`sudo systemctl enable --now ssh`), but Raspberry Pi Connect is the
  fallback console.
- To revoke assistant access: remove the `kiro-assistant@raspberrypi` line from the
  Pi user's `~/.ssh/authorized_keys`, or disable SSH entirely.

## Reference

- Confluence runbook (setup, verification, revocation):
  https://amainit.atlassian.net/wiki/spaces/JE/pages/460980226
- Ticket: SP-337.

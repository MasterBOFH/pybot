# pybot

Asyncio IRC bot with hot-reloadable modules, YAML config, user/channel journaling, and a GitHub webhook module.

## Requirements

- Python 3.11+
- Dependencies: `aiohttp`, `PyYAML`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# optional module deps, e.g. gardena:
# pip install -r pybot/modules/gardena/requirements.txt
cp config.yaml.example config.yaml
# edit config.yaml
python -m pybot config.yaml
```

Root `requirements.txt` is core-only. Modules that need extra packages ship
`pybot/modules/<name>/requirements.txt` — install those for the modules you enable.
Disabled modules are never imported, so unused module deps are not required.

Module authors: see **[docs/module-api.md](docs/module-api.md)** (BotAPI, events, lifecycle).

## Architecture

- **`pybot/irc/`** — connection (TLS/plain), line protocol, CAP, SASL (PLAIN + SCRAM-SHA-256), ISUPPORT, flood token-bucket, modes, WHO/WHOX, state journal
- **`pybot/core/`** — Bot orchestrator, EventBus, TimerEngine, BotAPI, HTTP server, module loader, hot-reload
- **`pybot/modules/`** — loadable modules (first: `github_webhook`)

```
IRC socket ──► IRCClient ──► EventBus ──► Modules
                  │              ▲
                  ▼              │
             StateJournal    BotAPI (privmsg, timers, http, log, …)
                  │
             TokenBucket (all outbound lines)
```

### IRC features

- CAP negotiation (`sasl`, `account-tag`, `account-notify`, `extended-join`, `message-tags`, …)
- SASL `PLAIN` / `SCRAM-SHA-256` (`mechanism: auto` prefers SCRAM when advertised)
- ISUPPORT: `CHANMODES` (A/B/C/D), `CASEMAPPING`, `PREFIX`, `WHOX`, …
- NAMES + MODE tracking; WHOX on join (`%tcuhnar`, serialized, no token)
- If account caps / extended-join are missing, periodic WHO/WHOX poll keeps accounts current
- Nick collision: try `nick` → `altnick` → `nick-` / `nick--` / …; ISON poll reclaims primary (and alt) when free
- Auto-reconnect on drop: wait 10s, then +10s each try up to 60s, then every 60s (`irc.reconnect`)
- Outbound flood control via token bucket (`irc.flood.burst` / `rate`)

### Logging

- Console logger `pybot` with severity levels
- Separate raw logger `pybot.raw` (`<<` / `>>` lines)
- Modules use `api.log` → `pybot.modules.<name>`

### Timers

Modules schedule work with `api.every(...)` / `api.after(...)`. Timers are owned by the module and cancelled on unload/reload. Do not spin bare `asyncio` loops for periodic work.

## Modules

Only modules with `enabled: true` in config are loaded. Core never imports the
others, so you can run a lean install (core requirements only) and add module
deps when you turn a module on.

### github_webhook

Receives GitHub webhooks and reports to an IRC channel.

1. Set `modules.github_webhook.secret` and `channel` in config
2. Point GitHub webhook at `http://<host>:8080/github` (or your `path`)
3. Content type: `application/json`; secret must match
4. Events: `push` (commits + tags), `release`, `issues`, `pull_request` (configurable; enable the same events on the GitHub webhook)

Signature: `X-Hub-Signature-256` (HMAC SHA-256).

### gardena

Husqvarna/Gardena Smart System mower events + `~devices` / `~weather`.

```bash
pip install -r pybot/modules/gardena/requirements.txt
```

Configure under `modules.gardena` (credentials, optional nested `weather:`).
Announcements go to each entry under `channels:`; the bot auto-joins those
channels when the module is enabled. Set `debug: true` on a channel to also
receive noisy state lines (default `false`).

### medialink

LiveKit video rooms: `$join` / `$rooms` / `$createroom` / `$deleteroom`, webhooks, periodic announcements.

```bash
pip install -r pybot/modules/medialink/requirements.txt
```

Configure under `modules.medialink` (API credentials, `token_url`, room `channels`).
Point LiveKit webhooks at the bot HTTP path (default `POST /livekit/webhook`).
Room names match IRC channel names for `$join`. Optional per-channel `debug: true`
for noisy track/debug lines (default `false`). The bot auto-joins enabled module
channels so room announcements can work immediately.

Join URL shortening is configurable with `modules.medialink.shortener.mode`:
`none` (default), `tinyurl` (public API), `isgd` (public API),
or `local` (self-hosted redirect endpoint).
Local mode serves `GET <path>?c=<code>` from the bot and redirects to the full
`token_url + token` link.

## Hot reload

Reconnects are disruptive; the bot keeps the TCP connection, negotiated caps, and state journal across reloads.

| What | Hot-reloadable? |
|------|-----------------|
| Module code + config | Yes — teardown → reload import → setup |
| Core logic (parsers/handlers) | Yes — sticky connection/state/timers/flood kept |
| Logger levels / flood rate | Yes — config reload |
| `irc.channels` | Yes — admin channels; JOIN/PART follows core + enabled module lists (`~reload config` / SIGHUP) |
| HTTP routes | Yes — dynamic mount table |
| HTTP bind host/port | Restarts HTTP only |
| IRC host/TLS/SASL/nick/caps/bindhost | No — use `~reconnect` |

### Triggers

- **IRC admin** (configured `irc.admin.hosts` hostmasks and/or `accounts`, prefix `~`):
  - `~reload modules`
  - `~reload module github_webhook`
  - `~reload config`
  - `~reload core`
  - `~reconnect`
  - `~modules`
- **SIGHUP** — same as `~reload config` (config + modules)

## Config

See [`config.yaml.example`](config.yaml.example).

## Testing

### Unit tests (no Docker)

```bash
pip install -r requirements-dev.txt
pytest tests/unit -v
```

### Integration harness (Docker + ircu2 + fake clients)

Spins up [Undernet ircu2](https://github.com/UndernetIRC/ircu2) on `localhost:6667`, connects the real `IRCClient` plus lightweight fake clients, and asserts parsing of JOIN/NAMES/MODE/NICK/PART/QUIT/PRIVMSG/WHO into the state journal.

```bash
# one-shot: build ircu2, run all tests, tear down
./scripts/run-harness.sh

# or manually:
docker compose -f docker/docker-compose.yml up -d --build
pytest tests/ --integration -v
docker compose -f docker/docker-compose.yml down
```

Environment overrides: `PYBOT_IRC_HOST`, `PYBOT_IRC_PORT`. Set `PYBOT_KEEP_IRCU=1` with the script to leave the server running after tests.

Harness layout:

- [`docker/ircu2/`](docker/ircu2/) — Dockerfile + minimal `ircd.conf`
- [`tests/harness/fake_client.py`](tests/harness/fake_client.py) — asyncio fake IRC clients
- [`tests/integration/`](tests/integration/) — live-server scenarios (skipped unless `--integration`)

## Admin note

Admin commands are only accepted from listed hostmasks (`nick!user@host` with `*`/`?`) or services accounts (when known via SASL/WHOX/caps), and only when sent in a core `irc.channels` admin channel.

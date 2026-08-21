---
mode: delegated
complexity: complex
type: feature
playwright: false
frontend-design: false
spec-version: 1
created: 2026-08-22T00:00:00
---

# Plan: hubstats module — hub topology/bandwidth reporting + oper-gated backchan

## Task Description

Add a new pybot module, `hubstats`, that opers the bot up on an ircu2 hub server and
reports hub health to a dedicated backchannel on a 5-minute timer: currently-linked
leaves and hub uplink(s), configured-but-not-currently-linked servers (from `STATS c`),
per-link bandwidth (kbps/pps), and reachability checks via `RPING` (linked servers) and
`UPING` (not-currently-linked servers). The backchannel itself is access-controlled:
only the bot and IRC operators may be seated in it, opers who join are auto-opped,
non-opers who join are kicked, and (optionally) users who oper-up elsewhere on the
network are auto-invited into it.

If the bot cannot OPER, it must not silently run degraded — it logs the failure and
shuts the whole process down with a non-zero exit code, so a process supervisor
(systemd `Restart=on-failure`, Docker restart policy, etc.) notices.

## Objective

A running pybot instance, opered on its ircu2 hub, posts one consolidated report to a
configured backchannel every 5 minutes covering leaf/uplink link health (RPING),
unreachable-but-configured servers (UPING), and bandwidth (kbps/pps) per link — and
enforces that the backchannel only contains the bot and currently-opered users.

## Problem Statement

pybot today has no way to run oper-only server-to-server diagnostic commands
(`OPER`, `STATS`, `RPING`, `UPING`, `KICK`) — `BotAPI` only exposes client-level
commands (`privmsg`, `notice`, `join`, `part`, `mode`, `who`). There is also no
concept of "this channel is oper-only, enforce it," and no way for a module to trigger
a hard process shutdown when a precondition it depends on fails. All of this must be
built from scratch, and several pieces of it (RPING/UPING reply text, the exact
`STATS V`/`STATS c`/`STATS l` field layout, and the "user X is now an operator" server
notice) are specific to this ircu2 build and are not discoverable from the pybot
codebase or from memory — they must be captured by actually running the protocol
against a live, linked pair of ircu2 servers.

## Solution Approach

1. Add a small number of generic, reusable primitives to core (`BotAPI` /
   `pybot/irc/client.py` / `pybot/core/bot.py`): `raw()`, `kick()`, `oper()`,
   `stats()`, `shutdown()`. These are protocol-generic — any future module could use
   them unchanged — so they belong in core, per `docs/module-api.md`'s scoping rules.
2. Extend the local docker ircu2 test harness with a second, linked ircu2 node (a real
   leaf) and use it — via the existing `tests/harness/fake_client.py` — to capture the
   *actual* raw wire text for `STATS V`, `STATS c`, `STATS l`, `RPING`, `UPING`, the
   `OVERRIDE` join, self-op `MODE`, and the oper-up server notice. This capture is the
   dependency every subsequent parsing task builds against — no format is guessed.
3. Build the module (`pybot/modules/hubstats/`) as several small, mostly-independent
   files (topology/bandwidth parsing, ping dispatch, report formatting, backchan
   guard), wired together by a final `module.py` that owns the OPER lifecycle, the
   report timer (never started before OPER succeeds), and the admin trigger command.

## Relevant Files

- `pybot/core/api.py` — `BotAPI`; add `raw()`, `kick()`, `oper()`, `stats()`,
  `shutdown()`. Existing methods (`privmsg`, `join`, `mode`, `who`, `every`,
  `register_channels`) are the patterns to follow.
- `pybot/irc/client.py` — `IRCClient`; the `_dispatch()` numeric/`raw_message`/`notice`
  handling that `oper()`/`stats()` piggyback on; the `who_end`/`registered`/`disconnect`
  events the module hooks.
- `pybot/irc/who.py` — `WhoManager`; pattern for a serialized "send request, await a
  reply, timeout" helper (used as the template for `IRCClient.stats()`/`oper()`).
- `pybot/core/bot.py` — `Bot.start()`/`Bot.stop()`; add an exit-code field so a module
  can request a specific process exit code on shutdown.
- `pybot/__main__.py` — `main()`; read the requested exit code after `asyncio.run(bot.start())`
  returns.
- `pybot/core/module.py` — `Module` base class / `@on` decorator; the module lifecycle
  every other module (`gardena`, `github`, `medialink`) already follows.
- `pybot/modules/gardena/module.py` — closest existing example of: config parsing,
  `register_channels`, admin-gated commands, `api.every()` timers, `get_state()`/
  `load_state()` for hot-reload continuity.
- `docker/ircu2/Dockerfile`, `docker/ircu2/ircd.conf`, `docker/docker-compose.yml` —
  the existing single-node ircu2 test harness; extend to a linked hub+leaf pair.
- `tests/harness/fake_client.py` — minimal raw IRC client already used by integration
  tests; reuse for OPER/STATS/RPING/UPING capture (it already exposes `send()` and
  `wait_for()` for arbitrary commands/replies).
- `tests/conftest.py` — `irc_server`/`irc_host`/`irc_port` fixtures, `--integration`
  flag; the harness the new integration test plugs into.
- `config.yaml.example` — add a documented `modules.hubstats` block, matching the style
  of the existing `github`/`gardena`/`medialink` blocks.
- `docs/module-api.md` — `BotAPI reference` table; document the five new methods.

### New Files

- `pybot/modules/hubstats/__init__.py` — exports `Module = HubStatsModule`.
- `pybot/modules/hubstats/module.py` — lifecycle, OPER-and-shutdown-on-failure, report
  timer, admin command, event wiring.
- `pybot/modules/hubstats/stats.py` — `STATS V`/`STATS c` topology classification and
  `STATS l` bandwidth snapshot/delta.
- `pybot/modules/hubstats/pings.py` — RPING/UPING dispatch + reply correlation.
- `pybot/modules/hubstats/report.py` — pure report-text formatting.
- `pybot/modules/hubstats/backchan.py` — OVERRIDE join, self-op, configurable chanmodes,
  join-time WHO-gated op/kick, oper-up-notice auto-invite.
- `docker/ircu2/ircd-leaf.conf` — a second ircu2 node's config, linked to the hub via a
  `Connect{}` block.
- `scripts/capture-hubstats-fixtures.py` — one-off script (using `FakeClient`) that
  connects to the docker harness, opers up, and dumps the raw lines for every command
  this module needs, to a fixtures file.
- `tests/fixtures/hubstats/captured_ircu2_replies.txt` — the captured raw protocol
  output produced by the script above; annotated with `#`-prefixed comments naming
  which command produced which block. This is the ground truth every parser in
  `stats.py`/`pings.py`/`backchan.py` is built and unit-tested against.
- `tests/unit/test_core_protocol_primitives.py`, `tests/unit/test_hubstats_stats.py`,
  `tests/unit/test_hubstats_pings.py`, `tests/unit/test_hubstats_report.py`,
  `tests/unit/test_hubstats_backchan.py` — unit tests, one per module file, built
  against the captured fixtures.
- `tests/integration/test_hubstats_module.py` — full-cycle integration test against the
  linked hub+leaf docker harness.

## Team Members

- Protocol Builder
  - **Role**: Core IRC primitives (`raw`, `kick`, `oper`, `stats`) and the graceful
    shutdown exit-code path.
  - **Agent Type**: builder
- Harness Builder
  - **Role**: Extends the docker ircu2 harness to a linked hub+leaf pair and captures
    the real wire format for every ircu2-specific command this module depends on.
  - **Agent Type**: builder
- Stats Builder
  - **Role**: `STATS V`/`STATS c`/`STATS l` parsing — topology classification and
    bandwidth deltas.
  - **Agent Type**: builder
- Pings Builder
  - **Role**: RPING/UPING dispatch, reply correlation, fire-all-collect-window logic.
  - **Agent Type**: builder
- Report Builder
  - **Role**: Pure report-text formatting from topology/bandwidth/ping results.
  - **Agent Type**: builder
- Backchan Builder
  - **Role**: OVERRIDE join, self-op, configurable chanmodes, join-time oper gate,
    oper-up auto-invite.
  - **Agent Type**: builder
- Module Integrator
  - **Role**: Final `module.py` wiring — config, OPER lifecycle, report timer, admin
    command, `config.yaml.example`.
  - **Agent Type**: builder
- QA Tester
  - **Role**: Integration test for a full report cycle against the linked docker
    harness, plus adversarial tests for the backchan join gate.
  - **Agent Type**: tester
- Code Reviewer
  - **Role**: Reviews every builder task, then a final pass across the whole diff.
  - **Agent Type**: reviewer
- Final Validator
  - **Role**: Runs validation commands and checks every acceptance criterion.
  - **Agent Type**: validator

## Review Policy
- **Review After**: each task
- **Fix Loop Trigger**: Critical and Important
- **Max Retries**: 3
- **Skip Review For**: researcher, validator

## Step by Step Tasks

### 1. Core protocol primitives: raw, kick, oper, stats
- **Task ID**: core-protocol-primitives
- **Depends On**: none
- **Description**:
  - In `pybot/core/api.py`, add to `BotAPI`:
    - `async def raw(self, command: str, *params: str) -> None` — guarded by the same
      `_irc_connected()` check as `privmsg`/`join`/etc., calls
      `await self._bot.irc.send(command, *params)`.
    - `async def kick(self, channel: str, nick: str, reason: str | None = None) -> None`
      — sends `KICK channel nick [reason]` (omit the reason param entirely when
      `reason` is falsy, matching how `part()` already handles an optional message).
    - `async def oper(self, name: str, password: str, timeout: float = 10.0) -> tuple[bool, str]`
      — delegates to `self._bot.irc.oper(name, password, timeout=timeout)` (see below).
    - `async def stats(self, letter: str, timeout: float = 10.0) -> list[tuple[int, list[str]]]`
      — delegates to `self._bot.irc.stats(letter, timeout=timeout)` (see below).
  - In `pybot/irc/client.py`, add to `IRCClient`:
    - `async def oper(self, name: str, password: str, timeout: float = 10.0) -> tuple[bool, str]`
      — sends `OPER name password`; awaits the first numeric in `{381, 464, 491}` using
      an `asyncio.Event`-based waiter set on the instance and checked at the top of
      `_dispatch()` for those three codes specifically (mirror the existing pattern in
      `_handle_nick_unavailable`/registration flow — a single pending waiter, not a
      general pub/sub). `381` → `(True, msg.trailing or "")`. `464`/`491` →
      `(False, msg.trailing or msg.command)`. On `asyncio.TimeoutError` →
      `(False, "timed out waiting for OPER reply")`. Only one `oper()` call may be in
      flight at a time (module code will only ever call it once per registration, so a
      simple "replace any prior pending waiter" is sufficient — no queue needed).
    - `async def stats(self, letter: str, timeout: float = 10.0) -> list[tuple[int, list[str]]]`
      — sends `STATS letter`; collects every numeric received after the send into a
      list of `(code, params)` until numeric `219` (`RPL_ENDOFSTATS`) arrives or
      `timeout` elapses (whichever first), then returns the collected list (excluding
      the `219` line itself). Implement as a dedicated collector similar in shape to
      `WhoManager` (queue/current-target/done-event) rather than reusing `WhoManager`
      directly — `STATS` is not WHO/WHOX and has its own reply codes. Numerics that
      arrive while a `stats()` call is pending must still also flow through the normal
      `emit("numeric", ...)` path (don't swallow them) — a `stats()` in flight only
      *additionally* appends to its own collector.
  - In `pybot/core/api.py`, add:
    - `async def shutdown(self, reason: str, exit_code: int = 1) -> None` — calls
      `self._bot.request_exit_code(exit_code)` then `await self._bot.stop(reason)`
      (the `request_exit_code` method is added in Task 2 — this task's `api.py` edit
      just calls it; Task 2 runs immediately after and adds the method it depends on).
- **Files**:
  - modifies: `pybot/core/api.py`
  - modifies: `pybot/irc/client.py`
- **Tests**:
  - `tests/unit/test_core_protocol_primitives.py`:
    - `oper()` resolves `(True, ...)` on a synthetic `381` reply.
    - `oper()` resolves `(False, ...)` on synthetic `464` and `491` replies.
    - `oper()` returns `(False, "timed out...")` when no reply numeric arrives before
      timeout (use a short timeout in the test, e.g. `0.05`).
    - `stats()` collects multiple numerics between the send and a synthetic `219`,
      returns them in order, excludes the `219` line.
    - `stats()` returns whatever was collected so far (possibly empty) on timeout if
      `219` never arrives.
    - A `numeric` event still fires via `emit()` for lines received while a `stats()`
      call is pending (i.e. `stats()` doesn't swallow the generic event).
    - `raw()` sends the exact formatted line for an arbitrary command/params tuple.
    - `kick()` omits the trailing reason param when `reason` is `None`/empty, includes
      it when provided.
  - Use the existing pattern in `tests/unit/test_nick_retry.py` / `test_ping_pong.py`
    (construct an `IRCClient` with a fake `emit`, feed it lines via `_on_line()`, no
    real socket needed) rather than the docker harness — these are pure unit tests.
- **Assigned To**: Protocol Builder
- **Agent Type**: builder
- **Background**: true

### 2. Graceful shutdown with a real process exit code
- **Task ID**: shutdown-exit-code
- **Depends On**: core-protocol-primitives
- **Description**:
  - In `pybot/core/bot.py`, add `self._exit_code: int = 0` in `Bot.__init__`, and
    `def request_exit_code(self, code: int) -> None: self._exit_code = code`. Do not
    change `stop()`'s existing shutdown sequence — it should still gracefully unload
    modules, cancel timers, disconnect IRC, and stop HTTP exactly as it does today;
    only the exit code changes.
  - Expose the final code to the caller of `start()`: change `Bot.start()`'s return
    type from `None` to `int`, and have it `return self._exit_code` after
    `await self._stop_event.wait()`.
  - In `pybot/__main__.py`, change `asyncio.run(bot.start())` (currently its result is
    discarded) to capture the returned exit code and use it as `main()`'s return value
    instead of the hardcoded `0`, e.g.:
    ```python
    try:
        return asyncio.run(bot.start())
    except KeyboardInterrupt:
        return 0
    ```
  - Confirm `api.shutdown()` from Task 1 now works end-to-end: `request_exit_code(1)`
    then `stop("reason")` → `start()` returns `1` → `main()` returns `1` →
    `raise SystemExit(main())` exits the process with code 1.
- **Files**:
  - modifies: `pybot/core/bot.py`
  - modifies: `pybot/__main__.py`
- **Tests**:
  - `tests/unit/test_core_protocol_primitives.py` (extend, or a new
    `tests/unit/test_shutdown_exit_code.py`):
    - `Bot.start()` returns `0` when `stop()` is called with no prior
      `request_exit_code()` call (normal/signal shutdown keeps today's behavior).
    - `Bot.start()` returns whatever code was last passed to `request_exit_code()`
      before `stop()` was called.
    - Calling `request_exit_code()` does not itself trigger shutdown — `stop()` must
      still be called separately (mirrors how `api.shutdown()` calls both).
  - No integration test needed — this is pure control-flow, covered by unit tests
    constructing a `Bot` against the existing test config pattern used in
    `tests/unit/test_config.py`.
- **Assigned To**: Protocol Builder
- **Agent Type**: builder
- **Background**: false

### 3. Docker harness: linked leaf node + live protocol capture
- **Task ID**: docker-harness-live-capture
- **Depends On**: none
- **Description**:
  - Extend `docker/ircu2/` to support a second, linked ircu2 node:
    - Add `docker/ircu2/ircd-leaf.conf`, a copy of `docker/ircu2/ircd.conf` adapted for
      a leaf: different `General { name = ...; numeric = 2; }` (hub keeps `numeric = 1`),
      its own `Operator{}` block (reuse `testoper`/`operpass` for simplicity), and a
      `Connect{}` block pointing back at the hub. Add a matching `Connect{}` block to
      `docker/ircu2/ircd.conf` pointing at the leaf, plus a `Port { ...; server = yes; }`
      on whichever side accepts the inbound server link. Use the actual
      `UndernetIRC/ircu2` source cloned during the Docker build (see the existing
      `Dockerfile`) — check `doc/example.conf` (and, if needed, `ircd/s_conf.c` /
      `ircd/parse.c`) in that checkout for the exact `Connect{}`/`Port{}` directive
      names and semantics rather than guessing; iterate against real container logs
      until the two nodes report a successful link.
    - The `Operator{}` block(s) likely need additional oper privileges beyond what's
      configured today for `WALK_LC` (channel-override join — confirm the exact priv
      name from the ircu2 source/docs) and for `RPING`/`UPING`/`STATS` to work for a
      non-local (`local = no`) oper; add whatever privileges empirical testing shows
      are required. Document exactly which ones were needed as comments in the conf
      file(s) (this is config, not project documentation, so it's fine here).
    - Update `docker/docker-compose.yml` to add a second service (e.g. `ircu2-leaf`)
      built from the same `Dockerfile` but with `ircd-leaf.conf` mounted/copied in
      (parameterize the Dockerfile's `COPY ircd.conf` with a build arg, or add a
      leaf-specific Dockerfile stage/variant — either is fine). The leaf does not need
      its client port exposed to the host; only the hub's `6667` must stay
      host-exposed (existing tests depend on that).
  - Add `scripts/capture-hubstats-fixtures.py`: a standalone script using
    `tests/harness/fake_client.py`'s `FakeClient` to connect to the hub
    (`127.0.0.1:6667` by default, overridable via `PYBOT_IRC_HOST`/`PYBOT_IRC_PORT` env
    vars matching `tests/conftest.py`'s convention), `OPER testoper operpass`, then in
    sequence: `STATS V`, `STATS c`, `STATS l`, `RPING <leaf-server-name>` (try the
    syntax `RPING <server>` first; if it errors, try `RPING <server> <server>` and
    `RPING <server> <comment>`, recording whichever actually works), `UPING <leaf-server-name>`
    (and `UPING <some-not-linked-name> <port>` for a target that intentionally isn't
    linked, to see the failure/no-route case too), `JOIN &capturetest OVERRIDE`,
    `MODE &capturetest +o <ownnick>`. Also spawn a second `FakeClient`, have the first
    (already opered) client set `MODE <ownnick> +s` (and any other snomask the ircu2
    source's `doc/`/`ircd/s_user.c` indicates is needed for oper-up notices — check the
    source, don't guess blindly), then have the second client `OPER testoper operpass`
    too, and record whatever notice (if any) the first client receives as a result.
    For every step, print/collect the raw line(s) received, each block prefixed with a
    `#`-comment naming the command that produced it.
  - Run the script against `docker compose -f docker/docker-compose.yml up -d --build`
    (reuse `scripts/run-harness.sh`'s compose invocation pattern) and save its full
    output to `tests/fixtures/hubstats/captured_ircu2_replies.txt`.
- **Files**:
  - creates: `docker/ircu2/ircd-leaf.conf`
  - modifies: `docker/ircu2/ircd.conf`
  - modifies: `docker/docker-compose.yml`
  - modifies: `docker/ircu2/Dockerfile` (only if needed to parametrize which conf is
    copied in for the leaf variant)
  - creates: `scripts/capture-hubstats-fixtures.py`
  - creates: `tests/fixtures/hubstats/captured_ircu2_replies.txt`
- **Tests**: N/A — this task produces test *fixtures* and infrastructure, not
  application code. Its correctness is verified by the fact that Tasks 4/5/6/7's unit
  tests (which consume the fixture file) pass.
- **Assigned To**: Harness Builder
- **Agent Type**: builder
- **Background**: true

### 4. Topology + bandwidth parsing (STATS V / STATS c / STATS l)
- **Task ID**: stats-parsing
- **Depends On**: docker-harness-live-capture
- **Description**:
  - Read `tests/fixtures/hubstats/captured_ircu2_replies.txt` first — every parser
    below must match the *actual* captured field layout and numeric codes, not a
    guessed RFC-standard shape.
  - In `pybot/modules/hubstats/stats.py`, implement:
    - `@dataclass class LinkedServer: name: str; is_hub: bool` — `is_hub=True` when the
      captured `STATS V` line for that server carries the `H` flag (confirm the exact
      flag field position/character from the fixture), `False` otherwise (a leaf).
    - `def parse_stats_v(lines: list[tuple[int, list[str]]]) -> list[LinkedServer]`
    - `def parse_stats_c(lines: list[tuple[int, list[str]]]) -> list[str]` — returns
      configured connect-block server names.
    - `@dataclass class Topology: leaves: list[LinkedServer]; uplinks: list[LinkedServer]; not_linked: list[str]`
    - `def build_topology(stats_v_lines, stats_c_lines) -> Topology` — `leaves` = STATS V
      entries with `is_hub=False`; `uplinks` = STATS V entries with `is_hub=True`;
      `not_linked` = STATS c names with no case-insensitive (`str.lower()`) match among
      the STATS V names.
    - `@dataclass class LinkBandwidth: name: str; kbps: float; pps: float`
    - `def parse_stats_l(lines: list[tuple[int, list[str]]]) -> dict[str, dict[str, int]]`
      — one entry per link name, mapping to whatever raw cumulative counters the
      fixture actually shows (e.g. sent/received bytes and message counts) — name the
      dict keys after the fixture's real fields and add a one-line comment stating the
      units (bytes vs kilobytes) as confirmed from the fixture, since that directly
      affects the kbps math below.
    - `def compute_bandwidth(prev: dict[str, dict[str, int]], curr: dict[str, dict[str, int]], elapsed_seconds: float) -> dict[str, LinkBandwidth]`
      — for each link present in both `prev` and `curr`, delta the byte/message
      counters, convert to `kbps = delta_bits / elapsed_seconds / 1000` and
      `pps = delta_messages / elapsed_seconds`. Links present in `curr` but not `prev`
      (first cycle, or a link that just came up) are omitted from the result, not
      reported as a bogus spike. Guard `elapsed_seconds <= 0` by returning `{}`.
- **Files**:
  - creates: `pybot/modules/hubstats/stats.py`
- **Tests**:
  - `tests/unit/test_hubstats_stats.py`, using literal `(code, params)` tuples
    transcribed from the fixture file (not re-parsing the raw text — transcribe once,
    hardcode as test data):
    - `parse_stats_v` correctly splits hub-flagged vs non-hub-flagged entries.
    - `parse_stats_c` extracts all configured server names from the fixture.
    - `build_topology` correctly computes `not_linked` as the set difference.
    - `parse_stats_l` extracts per-link counters matching the fixture's real fields.
    - `compute_bandwidth`: given two synthetic snapshots and a known elapsed time,
      asserts exact `kbps`/`pps` values (hand-computed from the deltas).
    - `compute_bandwidth` omits a link that only appears in `curr`.
    - `compute_bandwidth` returns `{}` for `elapsed_seconds=0`.
- **Assigned To**: Stats Builder
- **Agent Type**: builder
- **Background**: true

### 5. RPING/UPING dispatch and reply correlation
- **Task ID**: rping-uping-pings
- **Depends On**: core-protocol-primitives, docker-harness-live-capture
- **Description**:
  - Read `tests/fixtures/hubstats/captured_ircu2_replies.txt` for the exact RPING/UPING
    command syntax that worked and the exact reply text/event type (numeric vs
    `NOTICE` vs other) captured by Task 3.
  - In `pybot/modules/hubstats/pings.py`, implement:
    - `@dataclass class PingResult: server: str; kind: Literal["rping", "uping"]; ok: bool; rtt_ms: float | None; detail: str`
      (`ok=False, rtt_ms=None` for "no response" / a parsed failure line; `detail`
      holds the raw reply text or a `"no response"` marker).
    - `async def run_pings(api: BotAPI, rping_targets: list[str], uping_targets: list[str], collect_window_seconds: float) -> list[PingResult]`
      — sends the confirmed RPING command (via `api.raw(...)`) for every entry in
      `rping_targets` and the confirmed UPING command for every entry in
      `uping_targets`, all up front (no waiting between sends). Subscribes to whichever
      event(s) the fixture showed carry the replies (`api.on("notice", ...)` and/or
      `api.on("numeric", ...)`) for the duration of `collect_window_seconds`, matching
      each incoming line against the target server name via a regex built from the
      captured format (define the regex(es) as module-level constants with a comment
      quoting the fixture line they were derived from). Unsubscribe when the window
      closes. Any target with no matched reply by the deadline becomes a `PingResult`
      with `ok=False, detail="no response"`.
  - Keep the regex/parsing constants isolated at the top of the file so they're the
    single place to update if the live fixture format needs a follow-up correction.
- **Files**:
  - creates: `pybot/modules/hubstats/pings.py`
- **Tests**:
  - `tests/unit/test_hubstats_pings.py`:
    - The RPING/UPING reply regex(es), fed literal lines transcribed from the fixture,
      extract the correct server name and RTT/status.
    - `run_pings` against a fake `api` (a small stub object recording `.raw()` calls and
      letting the test manually invoke the registered `notice`/`numeric` handlers)
      returns a matched `PingResult(ok=True, ...)` when a matching reply is delivered
      before the window closes.
    - `run_pings` returns `PingResult(ok=False, detail="no response")` for a target
      whose reply never arrives within the window (use a short window in the test).
    - `run_pings` correctly disambiguates two concurrent targets' replies (doesn't
      cross-assign one target's reply to another).
- **Assigned To**: Pings Builder
- **Agent Type**: builder
- **Background**: true

### 6. Report formatting
- **Task ID**: report-formatting
- **Depends On**: stats-parsing, rping-uping-pings
- **Description**:
  - In `pybot/modules/hubstats/report.py`, implement:
    - `def format_report(topology: Topology, bandwidth: dict[str, LinkBandwidth], pings: list[PingResult], uping_leaves_enabled: bool) -> str`
      — builds one single consolidated line of text (per the earlier decision: a single
      PRIVMSG per cycle, not one line per server) summarizing, in order: uplink(s)
      (name, RPING result, bandwidth if known), leaves (name, RPING result, bandwidth
      if known, plus UPING result too when `uping_leaves_enabled` is true), and
      not-linked servers (name, UPING result). Use `"↑"` to prefix uplink entries,
      `"·"` to prefix leaf entries, `"✗"` to prefix not-linked entries, `"ok Nms"` for a
      successful ping (`N` = `round(rtt_ms)`), `"FAIL"` for a ping with a reply that
      indicated failure, `"no response"` verbatim for a timed-out ping, and omit the
      bandwidth suffix entirely for a server with no bandwidth entry (first cycle).
      Join server entries with `" | "`. Keep the whole thing on one line even if long —
      IRC clients wrap; do not truncate here.
    - Exact template per server entry: `"{prefix}{name} rping={ping_text} {kbps:.1f}kbps/{pps:.1f}pps"`
      for a linked server with known bandwidth, `"{prefix}{name} rping={ping_text}"`
      when bandwidth is unknown, `"{prefix}{name} uping={ping_text}"` for a not-linked
      server (no bandwidth, no RPING).
    - `def format_no_oper_report() -> str` — not used in the timed cycle (the timer
      never starts without OPER) but used by the admin `~hubstats now` command if
      invoked before OPER has succeeded: returns
      `"hubstats: not opered yet, no report available"`.
- **Files**:
  - creates: `pybot/modules/hubstats/report.py`
- **Tests**:
  - `tests/unit/test_hubstats_report.py`:
    - `format_report` with one uplink (ok RPING + bandwidth), one leaf (ok RPING + no
      bandwidth yet), one not-linked server (uping ok) → exact string match against a
      hand-written expected string using the templates above.
    - `format_report` with `uping_leaves_enabled=True` includes the leaf's UPING result
      too (exact string match).
    - `format_report` with a `PingResult(ok=False, detail="no response")` renders
      `"no response"` verbatim in that server's entry.
    - `format_no_oper_report()` returns the exact string above.
- **Assigned To**: Report Builder
- **Agent Type**: builder
- **Background**: true

### 7. Backchan guard: OVERRIDE join, self-op, oper-only enforcement
- **Task ID**: backchan-guard
- **Depends On**: core-protocol-primitives, docker-harness-live-capture
- **Description**:
  - Read `tests/fixtures/hubstats/captured_ircu2_replies.txt` for the exact oper-up
    server notice text/format captured by Task 3 (and confirm the OVERRIDE-join /
    self-op sequence worked as expected there too).
  - In `pybot/modules/hubstats/backchan.py`, implement:
    - `async def join_backchan(api: BotAPI, channel: str, modes: str | None) -> None`
      — if `channel` starts with `&`: `await api.join(channel, key="OVERRIDE")`, then
      `await api.mode(channel, "+o", api's own current nick)` (read the bot's nick via
      whatever `BotAPI`/state accessor already exposes it — check
      `pybot/irc/state.py`'s `StateJournal.nick` / how other modules read the bot's own
      nick; if nothing exposes it today, add a minimal `api.get_own_nick() -> str | None`
      to `BotAPI` in this task, wrapping `self._bot.irc.nick`). If `channel` does not
      start with `&`: log a warning that OVERRIDE/self-op only applies to local
      channels and just `await api.join(channel)` (best-effort, no override, no
      self-op attempt). If `modes` is set (non-empty), `await api.mode(channel, modes)`
      after the join/self-op.
    - `async def on_backchan_join(api: BotAPI, channel: str, joiner_nick: str, own_nick: str, cfg: dict) -> None`
      — no-op if `joiner_nick == own_nick`. Otherwise `await api.who(joiner_nick)`. The
      caller (module.py, Task 8) is responsible for invoking the matching
      `handle_backchan_who_result` below when the corresponding `who_end` fires — this
      function only issues the WHO.
    - `async def handle_backchan_who_result(api: BotAPI, channel: str, nick: str, own_nick: str, cfg: dict) -> None`
      — no-op if `nick == own_nick`. Look up `user = api.get_user(nick)`. If
      `user is not None and user.oper`: `await api.mode(channel, "+o", nick)` (only if
      `cfg.get("auto_op_opers", True)`). Else (`user is None` or `not user.oper`): if
      `cfg.get("kick_non_opers", True)`, `await api.kick(channel, nick, "backchan is oper-only")`.
      This same function handles both triggers (an individual joiner's WHO, and the
      channel-wide WHO that already fires automatically after the bot's own JOIN/366)
      — for the channel-wide case, module.py loops it over every member returned by
      `api.get_members(channel)`.
    - `def match_oper_notice(text: str) -> str | None` — given a `NOTICE` body, returns
      the nick that just opered if `text` matches the exact pattern captured in the
      fixture, else `None`. Define the regex as a module-level constant with a comment
      quoting the fixture line it was derived from.
    - `async def maybe_invite_opering_user(api: BotAPI, channel: str, notice_text: str, cfg: dict) -> None`
      — no-op unless `cfg.get("auto_invite_opers", False)`. If `match_oper_notice`
      matches, `await api.raw("INVITE", nick, channel)` (no existing `api.invite()` —
      use `raw()` from Task 1 rather than adding a dedicated wrapper for a single
      call site).
- **Files**:
  - creates: `pybot/modules/hubstats/backchan.py`
  - modifies: `pybot/core/api.py` (only if `get_own_nick()` does not already exist —
    check first; add it here if missing, one line, following the pattern of
    `get_user`/`get_channel`)
- **Tests**:
  - `tests/unit/test_hubstats_backchan.py`:
    - `join_backchan` on a `&channel` sends `JOIN &channel OVERRIDE` then
      `MODE &channel +o <ownnick>` then the configured mode string, in that order
      (assert on a fake `api` recording calls).
    - `join_backchan` on a `#channel` sends only `JOIN #channel` — no MODE/override
      call — and logs a warning.
    - `handle_backchan_who_result` ops a nick whose tracked `User.oper` is `True`.
    - `handle_backchan_who_result` kicks a nick whose tracked `User.oper` is `False`.
    - `handle_backchan_who_result` kicks a nick with no tracked `User` at all
      (`api.get_user()` returns `None` — default-deny).
    - `handle_backchan_who_result` is a no-op for the bot's own nick.
    - `handle_backchan_who_result` respects `auto_op_opers=False` and
      `kick_non_opers=False` config overrides (no MODE/KICK call issued).
    - `match_oper_notice` matches the exact fixture-derived notice text and extracts
      the nick; returns `None` for unrelated notice text.
    - `maybe_invite_opering_user` sends `INVITE nick channel` only when
      `auto_invite_opers=True` and the text matches.
- **Assigned To**: Backchan Builder
- **Agent Type**: builder
- **Background**: true

### 8. Module wiring: config, OPER lifecycle, timer, admin command
- **Task ID**: module-wiring
- **Depends On**: core-protocol-primitives, shutdown-exit-code, stats-parsing, rping-uping-pings, report-formatting, backchan-guard
- **Description**:
  - `pybot/modules/hubstats/__init__.py`: `from .module import HubStatsModule as Module`.
  - `pybot/modules/hubstats/module.py`, `class HubStatsModule(Module)`, `name = "hubstats"`:
    - Config shape (all under `modules.hubstats`):
      ```yaml
      oper:
        name: ""
        password: ""
      backchan:
        channel: "&opers"
        modes: "+sn"
        auto_op_opers: true
        kick_non_opers: true
        auto_invite_opers: false
      testing:
        uping_leaves: true
      report:
        interval_seconds: 300
        ping_collect_window_seconds: 45
      command_prefix: null   # defaults to irc.admin.prefix, same pattern as gardena
      ```
    - `setup(api)`: call `super().setup(api)`, store config, `api.register_channels([backchan_channel])`,
      resolve `command_prefix` the same way `gardena`/`medialink` do
      (`cfg.get("command_prefix") or admin.get("prefix") or "~"`), register the
      `registered`, `disconnect`, `user_join`, `who_end`, `notice`, and `privmsg`
      handlers via `@on`.
    - `@on("registered")` handler: `success, message = await self.api.oper(name, password)`.
      On failure: `self.api.log.error("hubstats: OPER failed: %s", message)`, then
      `await self.api.shutdown("hubstats: OPER failed", exit_code=1)` and return — do
      **not** proceed to join the backchan or start the timer. On success: log info,
      `await join_backchan(self.api, channel, modes)`, then cancel any existing report
      timer handle (defensive, in case of a reconnect) and create a new one via
      `self.api.every(interval_seconds, self._report_cycle, name="hubstats_report")`.
      Store the returned `TimerHandle` on `self`.
    - `@on("disconnect")` handler: cancel the stored report-timer handle (if any) via
      `self.api.cancel_timer(...)` and clear the stored reference — the bot is no
      longer opered once disconnected, so the timer must not keep firing; it is
      recreated by the next `registered` handler after a successful re-OPER.
    - `@on("user_join")` handler: if `channel` (case-insensitive-equal via
      `self.api.casefold`) is the configured backchan, call
      `on_backchan_join(self.api, channel, nick, self.api's own nick, backchan_cfg)`
      from `backchan.py`.
    - `@on("who_end")` handler: if the WHO `target` is the joiner's nick from a prior
      `on_backchan_join` call, or is the backchan channel name itself (the automatic
      post-366 WHO that fires when the bot's own JOIN completes), call
      `handle_backchan_who_result` — for the channel-target case, loop it over
      `self.api.get_members(backchan_channel)`.
    - `@on("notice")` handler: call `maybe_invite_opering_user(self.api, backchan_channel, text, backchan_cfg)`.
    - `@on("privmsg")` handler: admin-gated `~hubstats now` (using resolved
      `command_prefix`, checking `self.api.is_admin(...)`) triggers `self._report_cycle()`
      immediately, off-cycle, without touching the timer.
    - `_report_cycle()`: if not currently opered (guard with a simple `self._opered: bool`
      flag set/cleared alongside the OPER success/disconnect handlers — the timer
      shouldn't be running unopered anyway, but the admin command can be invoked at any
      time), reply with `report.format_no_oper_report()` to the backchan and return.
      Otherwise: `stats_v = await self.api.stats("V")`, `stats_c = await self.api.stats("c")`,
      `stats_l = await self.api.stats("l")`; build `Topology` via `stats.build_topology`;
      compute bandwidth via `stats.compute_bandwidth` against the previous snapshot
      stored on `self` (store the new snapshot + timestamp for next cycle, regardless
      of whether a delta was computable this time); build the RPING target list (all
      `topology.leaves` + `topology.uplinks` names) and UPING target list
      (`topology.not_linked` names, plus `topology.leaves` names too when
      `testing.uping_leaves` is true); `pings.run_pings(...)`; format via
      `report.format_report(...)`; `await self.api.privmsg(backchan_channel, text)`.
    - `get_state()`/`load_state()`: persist the previous `STATS l` snapshot + its
      timestamp across a hot-reload, same pattern as `gardena`'s device cache, so a
      `~reload config` mid-cycle doesn't produce a bogus first-cycle-after-reload
      bandwidth spike.
    - `reload_config()`: override per the `docs/module-api.md` pattern (default
      unregisters channels) — re-register the (possibly changed) backchan channel.
    - `teardown()`: `self.api.unregister_channels()`, cancel the report timer handle if
      set, call `super().teardown()`.
  - Update `config.yaml.example`: add a `hubstats:` block under `modules:` matching the
    shape above, commented in the same style as the existing `gardena`/`medialink`
    blocks (each key gets a short trailing `#` comment; mark `testing.uping_leaves`
    with a comment noting it's temporary and should be removed once validated).
- **Files**:
  - creates: `pybot/modules/hubstats/__init__.py`
  - creates: `pybot/modules/hubstats/module.py`
  - modifies: `config.yaml.example`
- **Tests**:
  - `tests/unit/test_hubstats_module.py`:
    - `setup()` registers the configured backchan channel.
    - The `registered` handler calls `api.oper()`; on success it calls `join_backchan`
      and creates a timer; on failure it calls `api.shutdown()` and does **not** call
      `join_backchan` or create a timer (assert against a fake `api` recording calls —
      no real timer/event loop needed if the fake `api.every`/`api.shutdown` are plain
      recording stubs).
    - The `disconnect` handler cancels the stored timer handle.
    - `~hubstats now` from an admin triggers an immediate `_report_cycle()` call
      without touching the timer; from a non-admin, does nothing (mirror the ACL check
      pattern already tested in `tests/unit/test_admin_hostmask.py`).
    - `_report_cycle()` before OPER has succeeded sends `format_no_oper_report()`'s
      exact text instead of attempting `api.stats()` calls.
    - `get_state()`/`load_state()` round-trip the bandwidth snapshot (set state, create
      a fresh module instance, load it, confirm the next `compute_bandwidth` call uses
      it as `prev` rather than treating the cycle as the first one).
- **Assigned To**: Module Integrator
- **Agent Type**: builder
- **Background**: false

### 9. Integration + adversarial tests
- **Task ID**: hubstats-integration-tests
- **Depends On**: module-wiring
- **Description**:
  - `tests/integration/test_hubstats_module.py` (marked `@pytest.mark.integration`,
    following the exact fixture pattern in `tests/integration/test_ircd_matrix.py` —
    `irc_server`, a real `IRCClient` built the same way `bot_client` is there): load the
    `hubstats` module against the linked hub+leaf docker harness from Task 3, drive one
    full report cycle (call `_report_cycle()` directly, or trigger it via the admin
    command through a `FakeClient` PRIVMSG), and assert the resulting backchan message
    mentions the leaf node by name and contains both an `rping=` entry and a `kbps`/`pps`
    figure (don't assert exact numbers — the real link's bandwidth is nondeterministic —
    assert the *shape* of the report).
  - Adversarial cases for the backchan guard, in the same file or a dedicated
    `tests/unit/test_hubstats_backchan_guard.py` if better isolated from the docker
    dependency (prefer unit-level with a fake `api` where the docker harness isn't
    strictly needed — only use the live harness where real oper state/WHO semantics
    genuinely need verifying):
    - A `FakeClient` (not opered) joining the backchan gets kicked.
    - A `FakeClient` that OPERs up first, then joins the backchan, gets opped and is
      not kicked.
    - The bot's own join to the backchan never triggers a self-kick/self-op-loop.
- **Files**: N/A (test-only task; see Depends On task's Files for what's under test)
- **Tests**:
  - `tests/integration/test_hubstats_module.py` — full-cycle integration test, as
    described above.
  - `tests/unit/test_hubstats_backchan_guard.py` (or folded into the integration file)
    — the three adversarial cases above.
- **Assigned To**: QA Tester
- **Agent Type**: tester
- **Background**: false

### 10. Review: core-protocol-primitives
- **Task ID**: review-core-protocol-primitives
- **Depends On**: core-protocol-primitives
- **Description**: Review Task 1's diff for correctness, edge cases, and style —
  particularly the `oper()`/`stats()` waiter lifecycle (does a stale waiter ever
  resolve incorrectly on a later, unrelated numeric? is the generic `numeric` emit
  path still exercised while a `stats()` call is pending?).
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 11. Review: shutdown-exit-code
- **Task ID**: review-shutdown-exit-code
- **Depends On**: shutdown-exit-code
- **Description**: Review Task 2's diff — confirm `stop()`'s existing shutdown
  sequence is unchanged, and the exit code correctly propagates from
  `request_exit_code()` through `start()`'s return value to `main()`'s process exit
  code.
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 12. Review: docker-harness-live-capture
- **Task ID**: review-docker-harness-live-capture
- **Depends On**: docker-harness-live-capture
- **Description**: Review Task 3's docker/config changes and capture script for
  correctness and reproducibility (does `scripts/capture-hubstats-fixtures.py` run
  cleanly against a freshly-built harness? are the fixtures file's comments clear
  enough for the parsing tasks to build against without re-running the script?).
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 13. Review: stats-parsing
- **Task ID**: review-stats-parsing
- **Depends On**: stats-parsing
- **Description**: Review Task 4's diff — confirm the parsers actually match the
  captured fixture (spot-check a few lines by hand), and that `compute_bandwidth`'s
  unit math (bits vs bytes, kbps vs KBps) is correct and documented.
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 14. Review: rping-uping-pings
- **Task ID**: review-rping-uping-pings
- **Depends On**: rping-uping-pings
- **Description**: Review Task 5's diff — confirm the fire-all-then-collect-window
  logic can't leak subscriptions (handlers must be unsubscribed even on exception/
  timeout), and that concurrent-target correlation is actually correct, not just
  correct in the single-target happy path.
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 15. Review: report-formatting
- **Task ID**: review-report-formatting
- **Depends On**: report-formatting
- **Description**: Review Task 6's diff — confirm the exact string templates match
  what's specified, and that a missing bandwidth/ping entry degrades gracefully
  (no `None`/`KeyError` leaking into the output string).
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 16. Review: backchan-guard
- **Task ID**: review-backchan-guard
- **Depends On**: backchan-guard
- **Description**: Security-focused review of Task 7's diff — this is the piece that
  kicks real users off a live network. Confirm: the bot's own nick is exempt in every
  code path, an unresolvable `User` (WHO never populated it) default-denies (kicks)
  rather than default-allows, the `#`-channel path never attempts OVERRIDE/self-op, and
  config toggles (`auto_op_opers`/`kick_non_opers`/`auto_invite_opers`) are actually
  honored everywhere they should gate behavior.
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 17. Review: module-wiring
- **Task ID**: review-module-wiring
- **Depends On**: module-wiring
- **Description**: Review Task 8's diff — confirm the report timer genuinely cannot
  fire before a successful OPER (trace every path that could create the timer), that
  OPER failure always reaches `api.shutdown()` with no code path that logs-and-continues,
  and that the reconnect path (`disconnect` → later `registered` again) correctly
  cancels and recreates the timer rather than leaking a duplicate.
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 18. Final code review
- **Task ID**: review-all
- **Depends On**: core-protocol-primitives, shutdown-exit-code, docker-harness-live-capture, stats-parsing, rping-uping-pings, report-formatting, backchan-guard, module-wiring
- **Description**: Review all code changes for correctness, style, edge cases, and
  security across the full diff. Report issues by severity (Critical, Important,
  Minor). Pay particular attention to the seams between tasks — does `module.py`
  actually call every other file's functions with the signatures those files'
  own tests assume?
- **Assigned To**: Code Reviewer
- **Agent Type**: reviewer

### 19. Final Validation
- **Task ID**: validate-all
- **Depends On**: review-all
- **Description**: Run all validation commands below, verify every acceptance
  criterion is met.
- **Assigned To**: Final Validator
- **Agent Type**: validator

## Documentation Requirements

- `docs/module-api.md`: add rows to the `BotAPI reference` → `IRC outbound` table for
  `raw()`, `kick()`, `oper()`, `stats()`, `shutdown()`, and `get_own_nick()` (if added),
  matching the existing table's style (method signature + one-line description).
- `README.md`: add a `### hubstats` subsection under `## Modules`, matching the
  style/depth of the existing `github` subsection — what it does, required config
  (`oper.name`/`oper.password`, `backchan.channel`), and a callout that
  `testing.uping_leaves` is a temporary validation flag meant to be turned off.
- `config.yaml.example`: the `modules.hubstats` block itself (added in Task 8) is the
  primary "documentation" for operators — every key needs an inline `#` comment.

## Acceptance Criteria

- `BotAPI` exposes `raw()`, `kick()`, `oper()`, `stats()`, `shutdown()` (and
  `get_own_nick()` if it didn't already exist), each with passing unit tests.
- A failed `OPER` (wrong password, no O-line) causes the bot process to exit with a
  non-zero exit code, and the 5-minute report timer is never created in that case.
- After a successful `OPER`, the bot joins the configured backchan (`JOIN &chan OVERRIDE`
  for a local channel) and ops itself (`MODE &chan +o <nick>`), applying any configured
  chanmodes afterward. A `#`-prefixed backchan is joined without OVERRIDE/self-op and
  logs a warning that those features don't apply.
- A user joining the backchan is immediately WHO'd; a confirmed IRC operator is opped,
  everyone else (including an unresolvable WHO) is kicked — except the bot itself,
  which is never kicked or WHO'd in response to its own join.
- Every 5 minutes (configurable), while opered, the bot posts one consolidated message
  to the backchan covering: hub uplink(s) with RPING result and bandwidth, leaves with
  RPING result and bandwidth (plus UPING when `testing.uping_leaves` is enabled), and
  not-currently-linked `STATS c` servers with UPING result.
- `~hubstats now` (admin-only) triggers an immediate off-cycle report; a non-admin
  invocation does nothing.
- Disconnecting cancels the report timer; a subsequent successful re-OPER after
  reconnect recreates exactly one timer (no duplicates).
- `docker compose -f docker/docker-compose.yml up -d --build` brings up a hub *and* a
  linked leaf node, and `tests/fixtures/hubstats/captured_ircu2_replies.txt` contains
  real captured output (not placeholder text) for `STATS V`, `STATS c`, `STATS l`,
  `RPING`, `UPING`, the OVERRIDE join/self-op sequence, and the oper-up server notice.
- All new and existing unit tests pass (`pytest tests/unit -v`).
- The new integration test passes against the extended docker harness
  (`./scripts/run-harness.sh` or equivalent manual `docker compose up` + `pytest --integration`).
- `docs/module-api.md` and `README.md` are updated per the Documentation Requirements
  section.

## Validation Commands

- `pytest tests/unit -v`
- `./scripts/run-harness.sh` (builds the extended docker harness, runs
  `pytest tests/ --integration -v` against it, tears down afterward)
- `python3 scripts/capture-hubstats-fixtures.py` against a running harness, to confirm
  the capture script itself still runs cleanly (re-run, don't just trust the committed
  fixture file)
- `python -m pybot config.yaml` with an intentionally wrong `modules.hubstats.oper.password`
  in a scratch config, manually confirmed to exit non-zero and log the OPER failure
  clearly (a manual smoke check, not automatable in CI, but explicitly worth doing once
  before calling this done)

## Cleanup

- `docker compose -f docker/docker-compose.yml down` — tear down the hub+leaf test
  containers after validation.
- Remove any scratch config file used for the manual OPER-failure smoke check.

## Notes

- Every ircu2-protocol-specific detail in this spec that isn't a well-known RFC1459
  numeric (`381`/`464`/`491` for OPER; `219` for end-of-STATS) is explicitly marked as
  "confirm from the captured fixture" rather than asserted as fact — this was a
  deliberate call made during brainstorming after discovering `docker/ircu2` builds
  *stock* Undernet ircu2, and a couple of assumed-nonstandard features (`OVERRIDE`
  join, self-op via `MODE`) turned out to be real stock behavior the user confirmed
  directly. Trust the fixture file over any inference in this spec if they disagree.
- Task 7 (backchan guard) is the most security-sensitive piece of this feature — it
  kicks real users from a real channel based on live oper-status data. The
  build skill automatically injects a security review after all builder tasks
  complete; no manual security-reviewer task is added here, but Task 16's review
  should already be treating this file with that level of scrutiny.
- `testing.uping_leaves` is explicitly a temporary flag (per the original request) to
  validate UPING behavior against already-linked servers during rollout — it is not
  meant to ship long-term as `true`. The docs/config comments should make that clear,
  but no automatic expiry/removal is part of this spec.

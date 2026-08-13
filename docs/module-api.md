# Module developer guide

pybot is an asyncio IRC bot. Core owns the IRC connection, state journal, event
bus, timers, and HTTP server. Feature code lives in **modules** under
`pybot/modules/<name>/`. Modules talk to core only through **`BotAPI`**.

## Optional dependencies (install without unused modules)

There is nothing to compile — pybot is pure Python. Dependencies are split so
you only install what you enable.

| Package set | Path | When to install |
|-------------|------|-----------------|
| **Core** | [`requirements.txt`](../requirements.txt) | Always (`aiohttp`, `PyYAML`) |
| **Dev / tests** | [`requirements-dev.txt`](../requirements-dev.txt) | Optional |
| **Per module** | `pybot/modules/<name>/requirements.txt` | Only if you enable that module |

Minimal install (no Gardena, etc.):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
# leave modules.gardena.enabled: false (or omit the block)
python -m pybot config.yaml
```

Enable a module that has extra deps:

```bash
pip install -r pybot/modules/gardena/requirements.txt
# set modules.gardena.enabled: true in config.yaml
```

Rules:

1. **Root `requirements.txt` must stay core-only.** Never put module-only
   packages there.
2. Modules that need third-party libraries ship their own
   `pybot/modules/<name>/requirements.txt`.
3. Modules that only use the stdlib + core deps (e.g. `github_webhook`) need no
   extra requirements file.
4. Core **imports a module package only when it is enabled** in config
   (`modules.<name>.enabled: true`). Disabled modules are not loaded, so their
   imports (and missing packages) never run.
5. Import heavy SDKs **lazily inside the module** (or behind a function) so a
   mistaken enable fails at setup with a clear error, not at bot process import.

Example layout for a module with extras:

```
pybot/modules/mything/
  __init__.py          # Module = MyThingModule
  module.py
  requirements.txt     # e.g. some-sdk>=1.0
```

---

## Creating a module

1. Create package `pybot/modules/<name>/`.
2. Export `Module` from `__init__.py` (alias of your class).
3. Subclass `pybot.core.module.Module`.
4. Enable under `modules.<name>` in YAML (`enabled: true` plus your settings).

```python
# pybot/modules/hello/__init__.py
from .module import HelloModule

Module = HelloModule
```

```python
# pybot/modules/hello/module.py
from pybot.core.api import BotAPI
from pybot.core.module import Module, on


class HelloModule(Module):
    name = "hello"

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        channel = self.config.get("channel") or "#dev"
        self.api.log.info("hello module ready → %s", channel)

    async def teardown(self) -> None:
        # Optional: close sockets, threads, etc.
        # Timers, bus handlers, and HTTP routes owned by this module
        # are cleared by core after teardown.
        await super().teardown()

    @on("privmsg")
    async def on_privmsg(self, nick: str | None = None, text: str | None = None, **_kw) -> None:
        assert self.api is not None
        if (text or "").strip() == "!hi":
            await self.api.privmsg(nick, f"hi {nick}")
```

Config:

```yaml
modules:
  hello:
    enabled: true
    channel: "#dev"
```

---

## Lifecycle

| Phase | What happens |
|-------|----------------|
| **Load** | `load_module_class(name)` imports `pybot.modules.<name>`, instantiates class, optional `load_state()`, `setup(api)`, then `@on` handlers registered on the bus |
| **Run** | Core emits IRC events; handlers and timers run on the bot event loop |
| **Unload / reload** | `teardown()` → bus handlers / timers / HTTP routes for `module:<name>` removed → package re-imported on reload → `get_state()` / `load_state()` bridge opaque state |

Override when needed:

- `async def setup(api)` — start clients, mount HTTP, schedule timers (`await super().setup(api)` first)
- `async def teardown()` — stop background work
- `async def reload_config(config)` — default replaces `self.config`
- `get_state()` / `load_state(state)` — preserve in-memory state across hot reload

---

## BotAPI reference

Constructed by core as `BotAPI(bot, module_name)`. Available as `self.api` after
`setup()`. Do **not** reach into `api._bot` from modules.

### Identity / logging

| Attribute / method | Description |
|--------------------|-------------|
| `api.module_name` | Module name string |
| `api.owner` | Owner tag, `module:<name>` (timers, bus, HTTP) |
| `api.log` | `logging.Logger` → `pybot.modules.<name>` |

### Config

| Method | Returns |
|--------|---------|
| `api.get_config()` | Copy of `modules.<name>` from YAML |
| `api.get_bot_config()` | Full bot config dict (read carefully; prefer module config) |

After `setup()`, `self.config` is the same module mapping (updated on config reload if you keep using `reload_config`).

### Admin ACL

```python
api.is_admin(payload) -> bool
```

`payload` should include what the `privmsg` event provides: `nick`, `user`,
`host`, `account`. Matches `irc.admin.hosts` (hostmasks) and/or
`irc.admin.accounts`.

### IRC outbound

All outbound traffic goes through the flood token bucket.

| Method | Description |
|--------|-------------|
| `await api.privmsg(target, text)` | Channel or nick |
| `await api.notice(target, text)` | NOTICE |
| `await api.join(channel, key=None)` | JOIN |
| `await api.part(channel, message=None)` | PART |
| `await api.mode(target, *args)` | MODE |
| `await api.who(target)` | WHO / WHOX query via core |

### State journal (read-only helpers)

| Method | Description |
|--------|-------------|
| `api.get_user(nick)` | `User \| None` from the journal |
| `api.get_channel(name)` | `Channel \| None` |
| `api.get_members(channel)` | list of nick strings |
| `api.casefold(name)` | Casefold via ISUPPORT CASEMAPPING |
| `api.nicks_equal(a, b)` | Case-insensitive nick/channel equality |
| `api.is_channel_op(channel, nick)` | True if nick has `+o` in that channel |

Prefer events + these helpers over re-parsing IRC yourself.

### Events

```python
@on("privmsg")
async def handler(self, **payload): ...

# or at runtime:
api.on("user_join", handler)
api.off("user_join", handler)  # or off by owner on unload
```

Handlers may be sync or async. Exceptions are isolated per handler.

Decorator handlers are collected after `setup()` from public methods with
`_pybot_event` set by `@on`.

### Timers

Prefer these over bare `asyncio.create_task` loops. Owned by the module; cancelled on unload.

| Method | Description |
|--------|-------------|
| `api.every(interval, callback, *, name=None, immediate=False)` | Repeating; returns `TimerHandle` |
| `api.after(delay, callback, *, name=None)` | One-shot |
| `api.cancel_timer(handle_or_name)` | Cancel by handle or `name` |

`callback` is `() -> None` or awaitable with no arguments.

### HTTP

Shared aiohttp app (host/port from top-level `http:`).

| Method | Description |
|--------|-------------|
| `api.mount_route(method, path, handler)` | `handler(request) -> StreamResponse` |
| `api.unmount_routes()` | Remove all routes for this module (also done on unload) |

### Thread → loop bridge

Gardena-style SDKs that call from another thread:

```python
api.schedule(coro) -> concurrent.futures.Future
```

Schedules `coro` on the bot event loop via `run_coroutine_threadsafe`. Raises if
the loop is not running. Example: `api.schedule(api.privmsg("#chan", "hi"))`.

---

## Core events

Handlers receive **keyword arguments** (plus unused `**kwargs` is fine). Payload
keys depend on the event.

### Connection / registration

| Event | Typical keys |
|-------|----------------|
| `registered` | `nick` |
| `disconnect` | `error` (str or None) |
| `isupport` | `isupport` (ISupport object) |
| `nick_unavailable` | `code`, `nick`, `reason`, `registered` |

### Messages

| Event | Typical keys |
|-------|----------------|
| `privmsg` | `nick`, `user`, `host`, `target`, `text`, `tags`, `account` |
| `notice` | same shape as `privmsg` |

### Channel / user presence

| Event | Typical keys |
|-------|----------------|
| `user_join` | `nick`, `channel`, `account`, `realname` |
| `user_part` | `nick`, `channel`, `reason` |
| `user_quit` | `nick`, `reason`, `channels` |
| `user_nick` | `old`, `new` |
| `user_kick` | `channel`, `nick`, `kicker`, `reason` |
| `user_account` | `nick`, `account` |
| `channel_mode` | `channel`, `nick`, `changes` |
| `user_mode` | `target`, `nick`, `changes` |
| `names_end` | `channel` |
| `who_end` | `target` |

### Catch-alls

| Event | Typical keys |
|-------|----------------|
| `numeric` | `code`, `params`, `tags`, `prefix` |
| `raw_message` | `command`, `params`, `tags`, `nick` |

Admin commands (`irc.admin.prefix`, default `~`) are handled in core **before**
module `privmsg` handlers when the sender matches the ACL. They are accepted in
core admin channels and in private messages to the bot.

---

## Hot reload notes for authors

- Keep long-lived sockets/threads in the module instance; restore via
  `get_state` / `load_state` if reload must not drop them.
- Do not store the raw `Bot` object; keep `BotAPI` only.
- Changing IRC host / TLS / SASL / nick / bindhost still needs `~reconnect`.
- Changing `irc.channels` is applied on `~reload config` / SIGHUP (JOIN/PART sync).

---

## Checklist for a new module

- [ ] Package under `pybot/modules/<name>/` exporting `Module`
- [ ] Extra deps in `pybot/modules/<name>/requirements.txt` (if any)
- [ ] Document config keys under `modules.<name>` in `config.yaml.example`
- [ ] Lazy-import optional heavy libraries
- [ ] Use `api.every` / `api.after` for periodic work
- [ ] Use `api.schedule` only from non-asyncio threads
- [ ] Clean up external resources in `teardown`

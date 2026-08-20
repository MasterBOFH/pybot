# pybot tests

## Unit

No server required:

```bash
pytest tests/unit -v
```

## Integration (ircu2 harness)

Requires Docker. Starts Undernet [ircu2](https://github.com/UndernetIRC/ircu2) and drives it with:

- the real `IRCClient` under test
- `tests/harness/fake_client.py` peers (JOIN/MODE/NICK/PRIVMSG/PART/QUIT)

```bash
./scripts/run-harness.sh
# or:
docker compose -f docker/docker-compose.yml up -d --build
pytest tests/ --integration -v
```

| Env | Default | Meaning |
|-----|---------|---------|
| `PYBOT_IRC_HOST` | `127.0.0.1` | ircu2 host |
| `PYBOT_IRC_PORT` | `6667` | ircu2 client port |
| `PYBOT_KEEP_IRCU` | unset | set `1` to leave compose up after `run-harness.sh` |

Integration tests are skipped unless `--integration` is passed.

## IRCd matrix (parser interop)

Requires Docker. Starts eight prebuilt IRCd implementations
([irccom](https://github.com/irccom) images: `ircd-irc2`, `unreal4`,
`ircd-hybrid`, `ircu2`, `bahamut`, `ngircd`, `charybdis`, `inspircd`) and
drives each one with the real `IRCClient`, catching parsing/state
assumptions that only ircu2 happens to satisfy: registration, ISUPPORT,
JOIN/NAMES, WHO/WHOX (whichever the server advertises — 352 vs 354 use
different field layouts), PRIVMSG, KICK, NICK, PART, and QUIT. Separate
from — and can run alongside — the ircu2 harness above.

```bash
./scripts/run-ircd-matrix.sh
# or:
docker compose -f docker/ircd-matrix/docker-compose.yml up -d --pull missing
pytest tests/integration/test_ircd_matrix.py --ircd-matrix -v
```

Most `irccom` images are amd64-only; on non-amd64 hosts Docker needs
QEMU/Rosetta emulation to run them. `ircd-irc2` and `bahamut` do
ident/reverse-DNS lookups on connect and only give up after their own
internal timeout (observed: ~70s and ~38s respectively) before completing
registration, so the full run takes a few minutes. `charybdis` is marked
`xfail`: `irccom/charybdis:latest` stops accepting new connections shortly
after startup in this harness (verified by hand — not a pybot bug); drop
that `xfail` mark once an updated image fixes it.

| Env | Default | Meaning |
|-----|---------|---------|
| `PYBOT_IRCD_MATRIX_HOST` | `127.0.0.1` | ircd matrix host |
| `PYBOT_KEEP_IRCD_MATRIX` | unset | set `1` to leave compose up after `run-ircd-matrix.sh` |

Matrix tests are skipped unless `--ircd-matrix` is passed. This target is
Docker-heavy (8 images, some via emulation) — run it deliberately, not as
part of the default test loop.

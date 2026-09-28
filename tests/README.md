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

Requires Docker. Starts ten prebuilt IRCd implementations from
[MasterBOFH/test-servers](https://github.com/MasterBOFH/test-servers)
(`ircd-irc2`, `unrealircd-6`, `ircd-hybrid-8`, `ircu2`, `bahamut`,
`ngircd`, `ircd-ratbox`, `solanum`, `inspircd-4`, `ergo`) and drives each
one with the real `IRCClient`, catching parsing/state assumptions that only
ircu2 happens to satisfy: registration, ISUPPORT, JOIN/NAMES, WHO/WHOX
(whichever the server advertises — 352 vs 354 use different field
layouts), PRIVMSG, MODE +o, KICK, NICK, PART, and QUIT. Each server runs
over plaintext and, where it has native TLS (all but `ircd-irc2` and
`ircu2`), again over TLS with the certificate verified against the test CA
in `docker/ircd-matrix/certs/ca.crt` (via `irc.tls_ca_file`). Separate
from — and can run alongside — the ircu2 harness above.

```bash
./scripts/run-ircd-matrix.sh
# or:
docker compose -f docker/ircd-matrix/docker-compose.yml up -d --pull missing
pytest tests/integration/test_ircd_matrix.py --ircd-matrix -v
```

The images register clients in well under a second (no ident, DNS or
proxy checks), give a channel's first joiner ops, and don't throttle
connections, so every step is a hard assertion and a full run takes under
a minute. Server ports live in `IRCD_MATRIX_SERVERS` in `tests/conftest.py`
(the run script reads them from there); keep it in sync with the compose
file. To pick up newly published images, run
`docker compose -f docker/ircd-matrix/docker-compose.yml pull`.

| Env | Default | Meaning |
|-----|---------|---------|
| `PYBOT_IRCD_MATRIX_HOST` | `127.0.0.1` | ircd matrix host |
| `PYBOT_KEEP_IRCD_MATRIX` | unset | set `1` to leave compose up after `run-ircd-matrix.sh` |

Matrix tests are skipped unless `--ircd-matrix` is passed. This target is
Docker-heavy (10 images, amd64) — run it deliberately, not as
part of the default test loop.

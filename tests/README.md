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

# brief

A URL shortener you run yourself. One Python file, stdlib only, JSON on disk.

```bash
python3 server.py
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787).

Paste a URL. Optional custom slug. Copy the short link. `/abc12` 302s to the target and bumps a hit counter.

## Why this instead of bit.ly

Nothing is posted to a third party. The map lives in `links.json`. You can read it, diff it, commit it, or throw it away.

## Use it

```bash
python3 server.py
```

```bash
curl -s -X POST http://127.0.0.1:8787/api/shorten \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/some/very/long/path","slug":"ex"}'
```

```json
{"slug":"ex","short":"http://127.0.0.1:8787/ex","url":"https://example.com/some/very/long/path","hits":0}
```

```bash
curl -I http://127.0.0.1:8787/ex
```

## Config

| env | default | what |
| --- | --- | --- |
| `BRIEF_HOST` | `127.0.0.1` | bind address |
| `BRIEF_PORT` | `8787` | port |
| `BRIEF_BASE` | `http://$HOST:$PORT` | prefix printed on short links |

Bind to the LAN:

```bash
BRIEF_HOST=0.0.0.0 BRIEF_BASE=http://192.168.1.20:8787 python3 server.py
```

## Rules

- only `http` and `https`
- slugs are 2–32 letters, numbers, dashes
- the same destination reuses the existing slug
- reserved paths stay reserved: `api`, `health`, …

## Files

```
server.py        the whole server
public/index.html
links.json       created on first run
```

No packages. No Docker. No account.

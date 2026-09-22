#!/usr/bin/env python3
"""Brief — a zero-dependency URL shortener."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "links.json"
PUBLIC = ROOT / "public"
HOST = os.environ.get("BRIEF_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRIEF_PORT", "8787"))
BASE = os.environ.get("BRIEF_BASE", f"http://{HOST}:{PORT}").rstrip("/")
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{1,31}$")
RESERVED = {
    "api",
    "static",
    "public",
    "health",
    "favicon.ico",
    "index.html",
    "robots.txt",
}

ALLOWED_SCHEMES = {"http", "https"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_store() -> dict:
    if not STORE.exists():
        return {"links": {}}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"links": {}}
    data.setdefault("links", {})
    return data


def save_store(data: dict) -> None:
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STORE)


def mint_slug(existing: dict, length: int = 5) -> str:
    for size in range(length, length + 4):
        for _ in range(32):
            slug = "".join(secrets.choice(ALPHABET) for _ in range(size))
            if slug not in existing and slug.lower() not in RESERVED:
                return slug
    raise RuntimeError("could not mint a free slug")


HOST_RE = re.compile(
    r"^(?:localhost|127\.0\.0\.1|\[::1\]|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}|(\d{1,3}\.){3}\d{1,3})$",
    re.I,
)


def normalize_url(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text or len(text) > 2048 or any(ch.isspace() for ch in text):
        return None
    lowered = text.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:", "file:", "about:")):
        return None
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").strip(".").lower()
    if not host or not HOST_RE.match(host):
        return None
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        return None
    return parsed.geturl()


def json_bytes(payload: dict, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    server_version = "Brief/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 32_000:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in {"/", "/index.html"}:
            return self._file(PUBLIC / "index.html", "text/html; charset=utf-8")
        if path == "/health":
            status, body, ctype = json_bytes({"ok": True})
            return self._send(status, body, ctype)
        if path == "/api/links":
            return self._list_links()
        if path.startswith("/api/"):
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")

        slug = path.lstrip("/")
        if "/" in slug or not slug:
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        self._redirect(slug)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/shorten":
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        self._shorten()

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api" or parts[1] != "links":
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        self._delete(parts[2])

    def _file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        data = path.read_bytes()
        extra = {"Cache-Control": "no-cache"}
        self._send(200, data, content_type, extra)

    def _list_links(self) -> None:
        data = load_store()
        items = []
        for slug, row in data["links"].items():
            items.append(
                {
                    "slug": slug,
                    "url": row["url"],
                    "hits": row.get("hits", 0),
                    "created": row.get("created"),
                    "short": f"{BASE}/{slug}",
                }
            )
        items.sort(key=lambda row: row.get("created") or "", reverse=True)
        status, body, ctype = json_bytes({"links": items})
        self._send(status, body, ctype)

    def _shorten(self) -> None:
        payload = self._read_json()
        url = normalize_url(str(payload.get("url") or ""))
        if not url:
            status, body, ctype = json_bytes({"error": "need a real http(s) url"}, 400)
            return self._send(status, body, ctype)

        data = load_store()
        links = data["links"]

        wanted = str(payload.get("slug") or "").strip()
        if wanted:
            if wanted.lower() in RESERVED or not SLUG_RE.match(wanted):
                status, body, ctype = json_bytes(
                    {"error": "slug must be 2–32 letters, numbers, dashes"}, 400
                )
                return self._send(status, body, ctype)
            if wanted in links:
                status, body, ctype = json_bytes({"error": "that slug is taken"}, 409)
                return self._send(status, body, ctype)
            slug = wanted
        else:
            existing = {row["url"]: key for key, row in links.items()}
            if url in existing:
                slug = existing[url]
                row = links[slug]
                status, body, ctype = json_bytes(
                    {
                        "slug": slug,
                        "url": url,
                        "short": f"{BASE}/{slug}",
                        "hits": row.get("hits", 0),
                        "created": row.get("created"),
                        "reused": True,
                    }
                )
                return self._send(status, body, ctype)
            slug = mint_slug(links)

        row = {"url": url, "hits": 0, "created": utc_now()}
        links[slug] = row
        save_store(data)
        status, body, ctype = json_bytes(
            {
                "slug": slug,
                "url": url,
                "short": f"{BASE}/{slug}",
                "hits": 0,
                "created": row["created"],
                "reused": False,
            },
            201,
        )
        self._send(status, body, ctype)

    def _delete(self, slug: str) -> None:
        data = load_store()
        if slug not in data["links"]:
            status, body, ctype = json_bytes({"error": "not found"}, 404)
            return self._send(status, body, ctype)
        del data["links"][slug]
        save_store(data)
        status, body, ctype = json_bytes({"ok": True})
        self._send(status, body, ctype)

    def _redirect(self, slug: str) -> None:
        data = load_store()
        row = data["links"].get(slug)
        if not row:
            html = (
                "<!doctype html><meta charset=utf-8>"
                "<title>missing</title>"
                "<body style='background:#0b0b10;color:#c8c4b8;"
                "font:16px/1.4 ui-monospace,monospace;padding:48px'>"
                "no link for <b>%s</b></body>"
            ) % slug
            return self._send(404, html.encode("utf-8"), "text/html; charset=utf-8")
        row["hits"] = int(row.get("hits") or 0) + 1
        row["last_hit"] = utc_now()
        save_store(data)
        target = row["url"]
        html = (
            "<!doctype html><meta charset=utf-8>"
            f'<meta http-equiv="refresh" content="0;url={target}">'
            f'<link rel="canonical" href="{target}">'
            f'<script>location.replace({json.dumps(target)})</script>'
        )
        self._send(302, html.encode("utf-8"), "text/html; charset=utf-8", {"Location": target})


def main() -> int:
    PUBLIC.mkdir(exist_ok=True)
    if not STORE.exists():
        save_store({"links": {}})
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"brief on {BASE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

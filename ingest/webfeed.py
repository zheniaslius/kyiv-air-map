"""Stateless source: read the channel's public web preview (t.me/s/<channel>) and parse it on the fly.

Used by the Vercel deployment, where there is no background poller and no disk. Also reused by the
local poller's "web" source. A small in-process TTL cache keeps warm serverless instances from
hitting Telegram more than once per TTL; the HTTP layer adds s-maxage so the CDN shares one fetch
across all visitors.
"""
from __future__ import annotations

import html
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .parser import Parser

_parser: Parser | None = None
_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL_SEC = 10.0


def parser() -> Parser:
    global _parser
    if _parser is None:
        _parser = Parser()
    return _parser


def fetch_page(channel: str, before: int | None = None) -> list[dict]:
    """One page (~20 posts) of the public preview, newest last. Each: {id, ts, text}."""
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    s = urllib.request.urlopen(req, timeout=20).read().decode()
    out = []
    for b in re.findall(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|$)', s, re.S):
        m = re.search(rf'data-post="{channel}/(\d+)"', b)
        if not m:
            continue
        t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        tm = re.search(r'<time datetime="([^"]+)"', b)
        txt = ""
        if t:
            txt = html.unescape(re.sub(r"<br\s*/?>", "\n", t.group(1)))
            txt = re.sub(r"<[^>]+>", "", txt).strip()
        if tm:
            out.append({"id": int(m.group(1)), "ts": tm.group(1), "text": txt})
    return sorted(out, key=lambda x: x["id"])


def fetch_recent(channel: str, hours: float = 3.0, max_pages: int = 4) -> list[dict]:
    """Pages backwards until the window is covered or max_pages is hit. Cached for CACHE_TTL_SEC."""
    key = f"{channel}:{hours}:{max_pages}"
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    msgs: dict[int, dict] = {}
    before = None
    for _ in range(max_pages):
        page = fetch_page(channel, before)
        if not page:
            break
        for m in page:
            msgs[m["id"]] = m
        before = page[0]["id"]
        if page[0]["ts"] < cutoff:
            break
    result = sorted(msgs.values(), key=lambda x: x["id"])
    _cache[key] = (time.monotonic(), result)
    return result


def events_for(messages: list[dict], since_iso: str | None = None) -> list[dict]:
    """Parse messages into event dicts with stable numeric ids (msg_id * 100 + n)."""
    p = parser()
    out = []
    for m in messages:
        for n, e in enumerate(p.parse(m["id"], m["ts"], m["text"])):
            if since_iso and e.ts < since_iso:
                continue
            out.append({"id": m["id"] * 100 + n, "msg_id": e.msg_id, "ts": e.ts, "raion": e.raion, "kind": e.kind,
                        "role": e.role, "weight": e.weight, "count": e.count, "text": e.text})
    out.sort(key=lambda e: (e["ts"], e["id"]))
    return out

"""FastAPI app shared by the local server (SQLite + poller) and the Vercel function (stateless)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse

from ingest.parser import ROLE_WEIGHT, TAU_MIN

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = os.environ.get("TG_CHANNEL", "war_monitor")
CDN_CACHE = "public, s-maxage=10, stale-while-revalidate=30"
# Alerts are shown only for these KATOTTG oblast prefixes; the rest of the map stays grey ("coming soon").
ENABLED_OBLASTS = [c.strip() for c in os.environ.get("ENABLED_OBLASTS", "UA80,UA32,UA12,UA23,UA48,UA51,UA59,UA63,UA74").split(",") if c.strip()]
ENABLED_LABEL = os.environ.get("ENABLED_LABEL", "Київ і 8 областей")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(stateless: bool) -> FastAPI:
    app = FastAPI(title="air-targets-map")

    @app.get("/api/config")
    def config():
        return {"tau_min": TAU_MIN, "role_weight": ROLE_WEIGHT, "channel": CHANNEL, "mode": "stateless" if stateless else "store",
                "enabled_oblasts": ENABLED_OBLASTS, "enabled_label": ENABLED_LABEL}

    if stateless:
        from ingest import webfeed

        @app.get("/api/events")
        def events(since: str | None = Query(None), hours: float = Query(3.0)):
            msgs = webfeed.fetch_recent(CHANNEL, hours=min(hours, 6.0))
            if not since:
                since = (_now() - timedelta(hours=hours)).isoformat()
            body = {"now": _now().isoformat(), "events": webfeed.events_for(msgs, since)}
            return JSONResponse(body, headers={"Cache-Control": CDN_CACHE})

        @app.get("/api/status")
        def status():
            try:
                msgs = webfeed.fetch_recent(CHANNEL)
                last = msgs[-1] if msgs else None
                body = {"last_message_ts": last["ts"] if last else None, "last_message_id": last["id"] if last else None,
                        "last_poll": _now().isoformat(), "source": "web", "now": _now().isoformat()}
            except Exception as e:  # noqa: BLE001
                body = {"last_message_ts": None, "last_message_id": None, "last_poll": None, "source": "web",
                        "now": _now().isoformat(), "error": str(e)[:200]}
            return JSONResponse(body, headers={"Cache-Control": CDN_CACHE})

    else:
        from ingest.store import Store

        store = Store()
        app.state.store = store

        @app.get("/api/events")
        def events(since: str | None = Query(None), hours: float = Query(3.0)):
            if not since:
                since = (_now() - timedelta(hours=hours)).isoformat()
            return {"now": _now().isoformat(), "events": store.events_since(since)}

        @app.get("/api/messages")
        def messages(hours: float = Query(3.0)):
            return store.messages_since((_now() - timedelta(hours=hours)).isoformat())

        @app.get("/api/status")
        def status():
            row = store.conn.execute("SELECT MAX(ts) AS ts, MAX(id) AS id FROM messages").fetchone()
            return {"last_message_ts": row["ts"], "last_message_id": row["id"], "last_poll": store.get_meta("last_poll"),
                    "source": os.environ.get("SOURCE", "auto"), "now": _now().isoformat()}

    return app



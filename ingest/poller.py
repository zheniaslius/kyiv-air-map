"""Poll @war_monitor and feed new messages into the store.

Sources:
  telethon  - MTProto user session (needs TG_API_ID / TG_API_HASH in env; first run asks for phone+code)
  web       - scrape https://t.me/s/<channel> (no credentials, ~20 latest posts, public channels only)
Chosen by env SOURCE, default: telethon if creds are set, else web.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .store import Store

log = logging.getLogger("poller")
CHANNEL = os.environ.get("TG_CHANNEL", "war_monitor")
POLL_SEC = float(os.environ.get("POLL_SEC", "10"))
DATA = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------- web preview source
from .webfeed import fetch_page as fetch_web  # noqa: E402


async def run_web(store: Store):
    log.info("web source: t.me/s/%s every %ss", CHANNEL, POLL_SEC)
    while True:
        try:
            msgs = await asyncio.to_thread(fetch_web, CHANNEL)
            new = 0
            for m in sorted(msgs, key=lambda x: x["id"]):
                if m["ts"]:
                    evs = store.add_message(m["id"], m["ts"], m["text"])
                    if evs:
                        new += 1
                        log.info("msg %s -> %d events: %s", m["id"], len(evs), m["text"][:80].replace("\n", " | "))
            store.set_meta("last_poll", datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            log.warning("web poll failed: %s", e)
        await asyncio.sleep(POLL_SEC)


# ---------------------------------------------------------------- telethon source
async def run_telethon(store: Store):
    from telethon import TelegramClient  # imported lazily so the web source works without telethon

    client = TelegramClient(str(DATA / "tg"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.start()
    entity = await client.get_entity(CHANNEL)
    log.info("telethon source: %s every %ss", CHANNEL, POLL_SEC)
    backfill = int(os.environ.get("BACKFILL", "300"))
    if store.last_msg_id() == 0 and backfill:
        msgs = await client.get_messages(entity, limit=backfill)
        for m in sorted(msgs, key=lambda x: x.id):
            if m.message:
                store.add_message(m.id, m.date.astimezone(timezone.utc).isoformat(), m.message)
        log.info("backfilled %d messages", len(msgs))
    while True:
        try:
            msgs = await client.get_messages(entity, min_id=store.last_msg_id(), limit=100)
            for m in sorted(msgs, key=lambda x: x.id):
                if m.message:
                    evs = store.add_message(m.id, m.date.astimezone(timezone.utc).isoformat(), m.message)
                    log.info("msg %s -> %d events: %s", m.id, len(evs), m.message[:80].replace("\n", " | "))
            store.set_meta("last_poll", datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            log.warning("telethon poll failed: %s", e)
            await asyncio.sleep(30)
        await asyncio.sleep(POLL_SEC)


def source_name() -> str:
    s = os.environ.get("SOURCE")
    if s:
        return s
    return "telethon" if os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH") else "web"


async def run(store: Store):
    if source_name() == "telethon":
        await run_telethon(store)
    else:
        await run_web(store)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(Store()))

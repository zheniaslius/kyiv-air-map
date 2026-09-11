"""Local server: FastAPI + SQLite store + Telegram poller, serving public/ as the site.

    .venv/bin/python -m server.main            # http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from ingest import poller
from server.app import ROOT, create_app

app = create_app(stateless=False)


@app.on_event("startup")
async def _start_poller():
    if not os.environ.get("NO_POLL"):
        asyncio.create_task(poller.run(app.state.store))


app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="site")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), log_level="info")

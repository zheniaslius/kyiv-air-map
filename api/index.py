"""Vercel serverless entrypoint. All /api/* requests are rewritten here (see vercel.json)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.app import create_app  # noqa: E402

app = create_app(stateless=True)

"""Vercel serverless entrypoint.

vercel.json rewrites /api/<anything> to /api/index?__path=<anything>. Vercel hands the function the
rewrite destination path, not the original one, so this wrapper restores the original path from the
__path query parameter before FastAPI routes the request.
"""
import os
import sys
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.app import create_app  # noqa: E402

_fastapi = create_app(stateless=True)


class _RestorePath:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            params = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
            path = next((v for k, v in params if k == "__path"), None)
            if path is not None:
                scope = dict(scope)
                scope["path"] = "/api/" + path.lstrip("/")
                scope["raw_path"] = scope["path"].encode()
                scope["query_string"] = urlencode([(k, v) for k, v in params if k != "__path"]).encode()
        await self.inner(scope, receive, send)


app = _RestorePath(_fastapi)

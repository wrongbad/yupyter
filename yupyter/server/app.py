"""Starlette application: static frontend, token auth, doc sync websocket, run RPC."""
from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from .. import agent as _agent  # noqa: F401  (registers agent:claude)
from .. import config
from ..doc.store import DocStore
from ..scheduler import RunScheduler

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

PLACEHOLDER = """<!doctype html><meta charset=utf-8><title>yupyter</title>
<body style="font-family:sans-serif;max-width:40em;margin:4em auto">
<h1>yupyter</h1><p>The frontend bundle is missing. Build it with
<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p>"""


class TokenAuth:
    """Pure-ASGI middleware: ``?token=`` (sets a cookie) or the cookie, for http + ws."""

    def __init__(self, app, token: str, cookie_name: str) -> None:
        self.app, self.token, self.cookie_name = app, token, cookie_name

    def _ok(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, self.token)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        qs = parse_qs(scope.get("query_string", b"").decode())
        q_token = (qs.get("token") or [None])[0]
        headers = dict(scope.get("headers") or [])
        jar = SimpleCookie(headers.get(b"cookie", b"").decode())
        c_token = jar[self.cookie_name].value if self.cookie_name in jar else None
        if self._ok(c_token) or (scope["type"] == "websocket" and self._ok(q_token)):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and self._ok(q_token) and scope["method"] in ("GET", "HEAD"):
            # first visit with the token: set the cookie and strip it from the URL
            rest = "&".join(f"{k}={v[0]}" for k, v in qs.items() if k != "token")
            loc = scope["path"] + (f"?{rest}" if rest else "")
            resp = RedirectResponse(loc or "/", status_code=303)
            resp.set_cookie(self.cookie_name, self.token, httponly=True, samesite="lax", path="/")
            return await resp(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        resp = JSONResponse({"error": "missing or invalid token"}, status_code=401)
        await resp(scope, receive, send)


def create_app(
    notebook_path: str | Path,
    token: str,
    *,
    port: int | None = None,
    store: DocStore | None = None,
    scheduler: RunScheduler | None = None,
) -> Starlette:
    notebook_path = Path(notebook_path).resolve()
    store = store or DocStore()
    scheduler = scheduler or RunScheduler(store)
    store.open(notebook_path)

    async def ws_endpoint(ws: WebSocket) -> None:
        # Room name is ignored for now: one notebook per server (y-websocket
        # clients connect to ``/ws/<room>``).
        await ws.accept()
        await store.open(notebook_path).serve(ws)

    async def api_run(request: Request) -> JSONResponse:
        body = await request.json()
        cell_id = body.get("cell_id")
        if not cell_id:
            return JSONResponse({"error": "cell_id required"}, status_code=400)
        author = body.get("author")
        if body.get("wait"):
            try:
                outs = await scheduler.run_cell(
                    notebook_path, cell_id, author=author, wait_for_cell=5
                )
            except KeyError as e:
                return JSONResponse({"error": str(e)}, status_code=404)
            return JSONResponse({"cell_id": cell_id, "outputs": outs})

        async def go() -> None:
            try:
                await scheduler.run_cell(notebook_path, cell_id, author=author, wait_for_cell=5)
            except KeyError:
                pass

        task = asyncio.create_task(go())
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
        return JSONResponse({"cell_id": cell_id, "queued": True}, status_code=202)

    async def api_interrupt(request: Request) -> JSONResponse:
        body = await request.json()
        kernel = body.get("kernel")
        if not kernel:
            return JSONResponse({"error": "kernel required"}, status_code=400)
        await scheduler.interrupt(kernel)
        return JSONResponse({"ok": True})

    async def api_info(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "path": str(notebook_path),
                "name": notebook_path.name,
                "room": "main",
                "aliases": config.aliases(),
                "default_kernel": "python3",
            }
        )

    @asynccontextmanager
    async def lifespan(app):
        app.state.tasks = set()
        try:
            yield
        finally:
            await scheduler.shutdown()
            await store.close()

    routes = [
        Route("/api/run", api_run, methods=["POST"]),
        Route("/api/interrupt", api_interrupt, methods=["POST"]),
        Route("/api/info", api_info, methods=["GET"]),
        WebSocketRoute("/ws/{room}", ws_endpoint),
    ]
    if (STATIC_DIR / "index.html").exists():
        routes.append(Mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static"))
    else:
        routes.append(Route("/", lambda r: HTMLResponse(PLACEHOLDER)))

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.store, app.state.scheduler, app.state.tasks = store, scheduler, set()
    app.state.notebook_path = notebook_path
    cookie = f"yupyter_token_{port}" if port else "yupyter_token"
    return TokenAuth(app, token, cookie)  # type: ignore[return-value]

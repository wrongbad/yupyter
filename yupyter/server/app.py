"""Starlette application: static frontend, token auth, doc sync websocket, run RPC."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from .. import agent as _agent  # noqa: F401  (registers agent:claude)
from .. import config
from ..agent.tools import installed_kernelspecs
from ..doc.store import DocStore
from ..kernels.registry import agent_names
from ..scheduler import RunScheduler
from .paths import PathError, clean_filename, hidden_name, rel_of, resolve_rel
from .undo_routes import undo_routes

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

PLACEHOLDER = """<!doctype html><meta charset=utf-8><title>yupyter</title>
<body style="font-family:sans-serif;max-width:40em;margin:4em auto">
<h1>yupyter</h1><p>The frontend bundle is missing. Build it with
<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p>"""


class AppStatic(StaticFiles):
    """Static files where index.html is always revalidated (assets are content-hashed)."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".html"):
            resp.headers["cache-control"] = "no-cache"
        return resp


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


def _is_notebook_file(p: Path) -> bool:
    """A ``.md`` with ``yupyter:`` in its front matter."""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(1024)
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    return "yupyter:" in (head if end < 0 else head[:end])


def create_app(
    root: str | Path,
    token: str,
    initial: str | Path | None = None,
    *,
    port: int | None = None,
    store: DocStore | None = None,
    scheduler: RunScheduler | None = None,
) -> Starlette:
    """One server for every notebook under ``root``.

    ``initial`` is a notebook path (relative to ``root``) the CLI opened.
    For backwards compatibility ``root`` may itself be a notebook file: its
    directory becomes the root and it becomes ``initial``.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        if initial is None:
            initial = root.name
        root = root.parent
    initial_rel = None
    if initial is not None:
        ip = Path(initial)
        initial_rel = rel_of(root, resolve_rel(root, ip.as_posix(), notebook=True))
    store = store or DocStore(root)
    store.root = store.root or root
    scheduler = scheduler or RunScheduler(store)
    if initial_rel:
        store.open(root / initial_rel)

    def notebook(request: Request) -> Path:
        return resolve_rel(root, request.path_params.get("path"), notebook=True)

    def directory(rel: str | None) -> Path:
        d = resolve_rel(root, rel or "")
        if not d.is_dir():
            raise PathError(404, "not a directory")
        return d

    async def ws_endpoint(ws: WebSocket) -> None:
        try:
            nb = resolve_rel(root, ws.path_params.get("path"), notebook=True)
            if not nb.parent.is_dir():
                raise PathError(404, "no such directory")
        except PathError:
            await ws.close(code=4403)
            return
        await ws.accept()
        await store.open(nb).serve(ws)

    async def api_run(request: Request) -> JSONResponse:
        nb = notebook(request)
        body = await request.json()
        cell_id = body.get("cell_id")
        if not cell_id:
            return JSONResponse({"error": "cell_id required"}, status_code=400)
        author = body.get("author")
        run_id = body.get("run_id")  # client-chosen so it can track the run for undo
        if not (isinstance(run_id, str) and re.fullmatch(r"[0-9a-zA-Z_-]{1,32}", run_id)):
            run_id = None
        if body.get("wait"):
            try:
                outs = await scheduler.run_cell(nb, cell_id, author=author, run_id=run_id, wait_for_cell=5)
            except KeyError as e:
                return JSONResponse({"error": str(e)}, status_code=404)
            return JSONResponse({"cell_id": cell_id, "outputs": outs})

        async def go() -> None:
            try:
                await scheduler.run_cell(nb, cell_id, author=author, run_id=run_id, wait_for_cell=5)
            except KeyError:
                pass

        task = asyncio.create_task(go())
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
        return JSONResponse({"cell_id": cell_id, "queued": True}, status_code=202)

    async def api_interrupt(request: Request) -> JSONResponse:
        nb = notebook(request)
        body = await request.json()
        kernel = body.get("kernel")
        if not kernel:
            return JSONResponse({"error": "kernel required"}, status_code=400)
        await scheduler.interrupt(kernel, nb)
        return JSONResponse({"ok": True})

    async def api_info_global(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "root": root.name,
                "initial": initial_rel,
                "aliases": config.aliases(),
                "default_kernel": "python3",
                "kernels": [*installed_kernelspecs(), *agent_names()],
            }
        )

    async def api_info(request: Request) -> JSONResponse:
        nb = notebook(request)
        info = json.loads((await api_info_global(request)).body)
        info.update(path=rel_of(root, nb), name=nb.name, room=rel_of(root, nb))
        return JSONResponse(info)

    async def api_resolve(request: Request) -> JSONResponse:
        nb = notebook(request)
        body = await request.json()
        store.open(nb)
        try:
            store.resolve(nb, body.get("action"))
        except (ValueError, FileNotFoundError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
        return JSONResponse({"ok": True, "status": store.status(nb)})

    async def api_rename(request: Request) -> JSONResponse:
        nb = notebook(request)
        body = await request.json()
        to = str(body.get("to") or "").strip()
        if not to:
            return JSONResponse({"error": "to required"}, status_code=400)
        parent, _, leaf = to.rpartition("/")
        leaf = clean_filename(leaf)
        new = resolve_rel(root, (parent + "/" if parent else "") + leaf, notebook=True)
        if not new.parent.is_dir():
            return JSONResponse({"error": "no such directory"}, status_code=404)
        if new == nb:
            return JSONResponse({"ok": True, "path": rel_of(root, nb)})
        if scheduler.busy(nb):
            return JSONResponse({"error": "notebook is running cells; interrupt them first"}, status_code=409)
        try:
            store.rename(nb, new)
        except FileExistsError:
            return JSONResponse({"error": f"{new.name} already exists"}, status_code=409)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
        scheduler.rename(nb, new)
        return JSONResponse({"ok": True, "path": rel_of(root, new)})

    async def api_tree(request: Request) -> JSONResponse:
        d = directory(request.query_params.get("dir"))
        entries = []
        for child in sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if hidden_name(child.name):
                continue
            try:
                real = child.resolve()
            except OSError:
                continue
            if not real.is_relative_to(root):
                continue  # symlink out of the root
            e: dict = {"name": child.name, "path": (d.relative_to(root) / child.name).as_posix()}
            if real.is_dir():
                e["type"] = "dir"
            elif real.suffix.lower() == ".md":
                e["type"] = "notebook" if _is_notebook_file(real) else "markdown"
                e["open_by"] = store.peers(real)
                e["kernel_running"] = scheduler.kernel_running(real)
            else:
                e["type"] = "other"
            entries.append(e)
        return JSONResponse({"dir": rel_of(root, d) if d != root else "", "root": root.name, "entries": entries})

    async def api_new(request: Request) -> JSONResponse:
        body = await request.json()
        d = directory(body.get("dir"))
        name = clean_filename(str(body.get("name") or ""))
        target = resolve_rel(root, (d.relative_to(root) / name).as_posix(), notebook=True)
        if target.exists() or store.assets_dir(target).exists():
            return JSONResponse({"error": f"{name} already exists"}, status_code=409)
        from ..doc import markdown_io, schema

        text = markdown_io.dump_markdown(schema.new_doc(), store.assets_dir(target))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
        return JSONResponse({"path": rel_of(root, target)}, status_code=201)

    async def spa(request: Request):
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index, headers={"cache-control": "no-cache"})
        return HTMLResponse(PLACEHOLDER)

    @asynccontextmanager
    async def lifespan(app):
        app.state.tasks = set()
        store.start()
        try:
            yield
        finally:
            await scheduler.shutdown()
            await store.close()

    def undo_resolve(request: Request) -> Path:
        return notebook(request)

    routes = [
        Route("/api/run/{path:path}", api_run, methods=["POST"]),
        Route("/api/interrupt/{path:path}", api_interrupt, methods=["POST"]),
        Route("/api/info", api_info_global, methods=["GET"]),
        Route("/api/info/{path:path}", api_info, methods=["GET"]),
        Route("/api/resolve/{path:path}", api_resolve, methods=["POST"]),
        Route("/api/rename/{path:path}", api_rename, methods=["POST"]),
        Route("/api/tree", api_tree, methods=["GET"]),
        Route("/api/new", api_new, methods=["POST"]),
        WebSocketRoute("/ws/{path:path}", ws_endpoint),
        Route("/n/{path:path}", spa, methods=["GET"]),
        *undo_routes(store, undo_resolve),
    ]
    if (STATIC_DIR / "index.html").exists():
        routes.append(Mount("/", AppStatic(directory=STATIC_DIR, html=True), name="static"))
    else:
        routes.append(Route("/", lambda r: HTMLResponse(PLACEHOLDER)))

    async def path_error(request: Request, exc: PathError):
        return JSONResponse({"error": exc.msg}, status_code=exc.status)

    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={PathError: path_error})
    app.state.store, app.state.scheduler, app.state.tasks = store, scheduler, set()
    app.state.root, app.state.initial = root, initial_rel
    cookie = f"yupyter_token_{port}" if port else "yupyter_token"
    return TokenAuth(app, token, cookie)  # type: ignore[return-value]

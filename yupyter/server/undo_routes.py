"""``POST /api/undo_run/{path} {run_id}`` / ``POST /api/redo_run/{path} {run_id}``: revert / re-apply
one agent run (see yupyter.doc.undo);
``POST /api/save/{path}``: flush the notebook to disk now."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..doc import undo


def undo_routes(store, resolve) -> list[Route]:
    """``resolve(request) -> Path`` maps the request's ``{path}`` to a notebook (may raise PathError)."""

    def run_action(fn, key: str):
        async def handler(request: Request) -> JSONResponse:
            nb = resolve(request)
            body = await request.json()
            run_id = body.get("run_id")
            if not run_id:
                return JSONResponse({"error": "run_id required"}, status_code=400)
            try:
                changed = fn(store.get_doc(nb), run_id)
            except KeyError:
                return JSONResponse({"error": f"unknown run: {run_id}"}, status_code=404)
            return JSONResponse({"run_id": run_id, key: changed})

        return handler

    async def api_save(request: Request) -> JSONResponse:
        nb = resolve(request)
        store.open(nb)
        try:
            store.save(nb)
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}", "status": store.status(nb)}, status_code=500)
        return JSONResponse({"ok": True, "status": store.status(nb)})

    return [
        Route("/api/undo_run/{path:path}", run_action(undo.undo_run, "undone"), methods=["POST"]),
        Route("/api/redo_run/{path:path}", run_action(undo.redo_run, "redone"), methods=["POST"]),
        Route("/api/save/{path:path}", api_save, methods=["POST"]),
    ]

"""``POST /api/undo_run {run_id}``: revert one agent run (see yupyter.doc.undo)."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..doc import undo


def undo_routes(store, notebook_path) -> list[Route]:
    async def api_undo_run(request: Request) -> JSONResponse:
        body = await request.json()
        run_id = body.get("run_id")
        if not run_id:
            return JSONResponse({"error": "run_id required"}, status_code=400)
        doc = store.get_doc(notebook_path)
        try:
            changed = undo.undo_run(doc, run_id)
        except KeyError:
            return JSONResponse({"error": f"unknown run: {run_id}"}, status_code=404)
        return JSONResponse({"run_id": run_id, "undone": changed})

    return [Route("/api/undo_run", api_undo_run, methods=["POST"])]

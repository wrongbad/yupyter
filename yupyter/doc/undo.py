"""Server-side "undo this agent run".

pycrdt origins don't reach clients, so undo for agent runs lives here. The
agent calls ``track_run(doc, run_id)`` *before* writing and passes
``origin=run_origin(run_id)`` (``"agent-run:<run_id>"``) on its schema writes.
A pycrdt ``UndoManager`` scoped to the cells map and including only that
origin records the run; ``undo_run`` reverts it. Server writes of
``exec``/``outputs`` use other origins, so they are never reverted.

The agent cell's ``exec.undone`` flag (shared with every collaborator, written
with ``SERVER_ORIGIN`` so the run's own manager never captures it) says whether
the run is currently undone; ``exec.summary`` holds what it changed.

Concurrent human edits merge CRDT-style (a human's typing inside an
agent-inserted span is kept where the span's own text is removed).
"""
from __future__ import annotations

from pycrdt import Doc, UndoManager

from . import schema

_TRACKERS: dict[int, dict[str, UndoManager]] = {}  # id(doc) -> run_id -> manager


def run_origin(run_id: str) -> str:
    return f"agent-run:{run_id}"


def track_run(doc: Doc, run_id: str) -> UndoManager:
    runs = _TRACKERS.setdefault(id(doc), {})
    um = runs.get(run_id)
    if um is None:
        # huge capture timeout: the whole run is one undo step
        um = UndoManager(scopes=[schema.cells(doc)], capture_timeout_millis=10**12)
        um.include_origin(run_origin(run_id))
        runs[run_id] = um
    return um


def has_run(doc: Doc, run_id: str) -> bool:
    return run_id in _TRACKERS.get(id(doc), {})


def _set_flag(doc: Doc, run_id: str, undone: bool) -> None:
    """Mirror undone/redone onto the agent cell(s) that list ``run_id`` in ``exec.runs``."""
    cs = schema.cells(doc)
    for cid in list(cs.keys()):
        ex = (cs.get(cid) or {}).get("exec")
        if ex is not None and run_id in (ex.get("runs") or []):
            with doc.transaction(origin=schema.SERVER_ORIGIN):
                ex["undone"] = undone


def _tracker(doc: Doc, run_id: str) -> UndoManager:
    um = _TRACKERS.get(id(doc), {}).get(run_id)
    if um is None:
        raise KeyError(run_id)
    return um


def undo_run(doc: Doc, run_id: str) -> bool:
    """Revert everything the run did. False if there was nothing to undo; KeyError if untracked."""
    um = _tracker(doc, run_id)
    did = False
    while um.can_undo():
        um.undo()
        did = True
    if did:
        _set_flag(doc, run_id, True)
    return did


def redo_run(doc: Doc, run_id: str) -> bool:
    """Re-apply an undone run. False if there was nothing to redo; KeyError if untracked."""
    um = _tracker(doc, run_id)
    did = False
    while um.can_redo():
        um.redo()
        did = True
    if did:
        _set_flag(doc, run_id, False)
    return did

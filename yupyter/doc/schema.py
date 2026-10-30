"""The shared notebook document (pycrdt) and helpers to manipulate it.

Layout (see plans/poc.md section 4)::

    doc
    ├─ meta:    Map                 title, aliases, ...
    ├─ cells:   Map<cellId, Map>    order, kind, kernel, source(Text), author,
    │                               created_by_run, exec(Map), outputs(Array)
    └─ kernels: Map<name, dict>     {"status": starting|idle|busy|dead, "updated": ts}

Rules: clients may create/edit/delete cells (order, kind, kernel, source,
author); only the server writes ``exec`` and ``outputs``.

All mutating helpers accept ``origin`` (any hashable, e.g. ``"run:<id>"``)
that is attached to the transaction so UndoManagers / observers can tell
who made a change.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Hashable

from pycrdt import Array, Doc, Map, Text

from .order import between

Output = dict[str, Any]

EXEC_STATES = ("idle", "queued", "running", "done", "error")
SERVER_ORIGIN = "server"


def new_doc() -> Doc:
    """Create a document with the root types initialised."""
    doc = Doc()
    meta = doc.get("meta", type=Map)
    doc.get("cells", type=Map)
    doc.get("kernels", type=Map)
    with doc.transaction(origin=SERVER_ORIGIN):
        if "yupyter" not in meta:
            meta["yupyter"] = 1
    return doc


def meta(doc: Doc) -> Map:
    return doc.get("meta", type=Map)


def cells(doc: Doc) -> Map:
    return doc.get("cells", type=Map)


def kernels(doc: Doc) -> Map:
    return doc.get("kernels", type=Map)


def new_cell_id() -> str:
    return secrets.token_hex(2) + secrets.token_hex(1)  # 6 hex chars


def _cell(doc: Doc, cell_id: str) -> Map:
    c = cells(doc).get(cell_id)
    if c is None:
        raise KeyError(f"no such cell: {cell_id}")
    return c


def has_cell(doc: Doc, cell_id: str) -> bool:
    return cells(doc).get(cell_id) is not None


def get_cell(doc: Doc, cell_id: str) -> Map:
    return _cell(doc, cell_id)


def ordered_ids(doc: Doc) -> list[str]:
    cs = cells(doc)
    items = []
    for cid in list(cs.keys()):
        c = cs.get(cid)
        if c is not None:
            items.append((str(c.get("order") or ""), cid))
    items.sort()
    return [cid for _, cid in items]


def ordered_cells(doc: Doc) -> list[dict[str, Any]]:
    """Plain-python snapshot of all cells in document order."""
    return [cell_snapshot(doc, cid) for cid in ordered_ids(doc)]


def cell_snapshot(doc: Doc, cell_id: str) -> dict[str, Any]:
    c = _cell(doc, cell_id)
    ex = c.get("exec")
    outs = c.get("outputs")
    return {
        "id": cell_id,
        "order": c.get("order"),
        "kind": c.get("kind"),
        "kernel": c.get("kernel"),
        "source": str(c["source"]) if c.get("source") is not None else "",
        "author": c.get("author"),
        "created_by_run": c.get("created_by_run"),
        "exec": ex.to_py() if ex is not None else {},
        "outputs": outs.to_py() if outs is not None else [],
    }


def insert_cell(
    doc: Doc,
    after_id: str | None,
    kind: str,
    kernel: str,
    source: str,
    author: str | None,
    created_by_run: str | None = None,
    origin: Hashable | None = None,
    *,
    before_id: str | None = None,
    cell_id: str | None = None,
) -> str:
    """Insert a cell and return its id.

    ``after_id=None`` appends at the end (or use ``before_id`` to insert
    before a cell, or ``before_id=<first id>`` to insert at the top).
    """
    ids = ordered_ids(doc)
    cs = cells(doc)
    order_of = lambda i: str(cs[i].get("order"))  # noqa: E731
    if before_id is not None:
        idx = ids.index(before_id)
        lo = order_of(ids[idx - 1]) if idx > 0 else None
        hi = order_of(before_id)
    elif after_id is not None:
        idx = ids.index(after_id)
        lo = order_of(after_id)
        hi = order_of(ids[idx + 1]) if idx + 1 < len(ids) else None
    else:
        lo = order_of(ids[-1]) if ids else None
        hi = None
    cid = cell_id or new_cell_id()
    with doc.transaction(origin=origin):
        c = Map()
        cs[cid] = c  # integrate first, then fill, in one transaction
        c["order"] = between(lo, hi)
        c["kind"] = kind
        c["kernel"] = kernel
        c["source"] = Text(source)
        c["author"] = author
        c["created_by_run"] = created_by_run
        c["exec"] = Map({"state": "idle", "count": None})
        c["outputs"] = Array()
    return cid


def delete_cell(doc: Doc, cell_id: str, origin: Hashable | None = None) -> None:
    with doc.transaction(origin=origin):
        del cells(doc)[cell_id]


def get_source(doc: Doc, cell_id: str) -> str:
    return str(_cell(doc, cell_id)["source"])


def set_source(doc: Doc, cell_id: str, source: str, origin: Hashable | None = None) -> None:
    """Replace the whole source (prefer ``edit_source`` for merge-friendly edits)."""
    text = _cell(doc, cell_id)["source"]
    with doc.transaction(origin=origin):
        n = len(str(text))
        if n:
            del text[0:n]
        if source:
            text.insert(0, source)


def edit_source(
    doc: Doc, cell_id: str, old: str, new: str, origin: Hashable | None = None
) -> None:
    """Search/replace ``old`` -> ``new`` in the cell's shared text.

    ``old`` must occur exactly once. Works as a minimal CRDT delete+insert so
    concurrent typing elsewhere in the cell is preserved.
    """
    text = _cell(doc, cell_id)["source"]
    s = str(text)
    n = s.count(old)
    if not old or n != 1:
        raise ValueError(f"`old` must match exactly once in cell {cell_id} (found {n})")
    i = s.index(old)
    with doc.transaction(origin=origin):
        del text[i : i + len(old)]
        if new:
            text.insert(i, new)


# ---- server-only writers: exec + outputs + kernel status -------------------


def _outputs(doc: Doc, cell_id: str) -> Array:
    return _cell(doc, cell_id)["outputs"]


def get_outputs(doc: Doc, cell_id: str) -> list[Output]:
    return _outputs(doc, cell_id).to_py()


def clear_outputs(doc: Doc, cell_id: str, origin: Hashable | None = SERVER_ORIGIN) -> None:
    arr = _outputs(doc, cell_id)
    with doc.transaction(origin=origin):
        n = len(arr)
        if n:
            del arr[0:n]


def set_outputs(
    doc: Doc, cell_id: str, outputs: list[Output], origin: Hashable | None = SERVER_ORIGIN
) -> None:
    arr = _outputs(doc, cell_id)
    with doc.transaction(origin=origin):
        n = len(arr)
        if n:
            del arr[0:n]
        for o in outputs:
            arr.append(o)


def update_output(
    doc: Doc, cell_id: str, index: int, output: Output, origin: Hashable | None = SERVER_ORIGIN
) -> None:
    """Replace the output at ``index`` in place (streaming displays that grow)."""
    arr = _outputs(doc, cell_id)
    with doc.transaction(origin=origin):
        arr[index] = output


def append_output(
    doc: Doc, cell_id: str, output: Output, origin: Hashable | None = SERVER_ORIGIN
) -> None:
    """Append an output; consecutive ``stream`` outputs of the same name merge."""
    arr = _outputs(doc, cell_id)
    with doc.transaction(origin=origin):
        n = len(arr)
        if n and output.get("output_type") == "stream":
            last = arr[n - 1]
            if last.get("output_type") == "stream" and last.get("name") == output.get("name"):
                arr[n - 1] = {**last, "text": last.get("text", "") + output.get("text", "")}
                return
        arr.append(output)


def set_exec_state(
    doc: Doc,
    cell_id: str,
    state: str,
    *,
    count: int | None = None,
    run_id: str | None = None,
    origin: Hashable | None = SERVER_ORIGIN,
) -> None:
    """Update ``exec``. ``started`` / ``finished`` timestamps are set from state."""
    if state not in EXEC_STATES:
        raise ValueError(state)
    ex = _cell(doc, cell_id)["exec"]
    now = time.time()
    with doc.transaction(origin=origin):
        ex["state"] = state
        if count is not None:
            ex["count"] = count
        if run_id is not None:
            ex["runId"] = run_id
        if state == "queued":
            ex["queued"] = now
        elif state == "running":
            ex["started"] = now
            ex["finished"] = None
        elif state in ("done", "error"):
            ex["finished"] = now


def set_kernel_status(
    doc: Doc, name: str, status: str, origin: Hashable | None = SERVER_ORIGIN
) -> None:
    """status: starting | idle | busy | dead"""
    with doc.transaction(origin=origin):
        kernels(doc)[name] = {"status": status, "updated": time.time()}


# ---- reload from an externally edited file ----------------------------------


def replace_from(doc: Doc, other: Doc, origin: Hashable | None = None) -> None:
    """Make ``doc`` hold the content of ``other`` in ONE transaction.

    Cells present in both are updated in place (source via a minimal text diff,
    so editor bindings survive); missing ones are created, extra ones deleted.
    A cell that is queued/running in ``doc`` keeps its live ``exec`` state.
    """
    import difflib

    with doc.transaction(origin=origin):
        m, om = meta(doc), meta(other)
        for k in list(m.keys()):
            if k not in om:
                del m[k]
        for k in list(om.keys()):
            v = om.get(k)
            v = v.to_py() if hasattr(v, "to_py") else v
            if m.get(k) != v:
                m[k] = v
        cs = cells(doc)
        want = {cid: cell_snapshot(other, cid) for cid in ordered_ids(other)}
        for cid in list(cs.keys()):
            if cid not in want:
                del cs[cid]
        for cid, snap in want.items():
            c = cs.get(cid)
            if c is None:
                c = Map()
                cs[cid] = c
                c["source"] = Text(snap["source"])
                c["exec"] = Map(dict(snap["exec"] or {"state": "idle", "count": None}))
                c["outputs"] = Array()
                for o in snap["outputs"]:
                    c["outputs"].append(o)
            else:
                text = c["source"]
                old, new = str(text), snap["source"]
                if old != new:
                    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
                    for tag, i1, i2, j1, j2 in reversed(sm.get_opcodes()):
                        if tag == "equal":
                            continue
                        if i2 > i1:
                            del text[i1:i2]
                        if j2 > j1:
                            text.insert(i1, new[j1:j2])
                outs = c["outputs"]
                if outs.to_py() != snap["outputs"]:
                    n = len(outs)
                    if n:
                        del outs[0:n]
                    for o in snap["outputs"]:
                        outs.append(o)
                ex = c.get("exec")
                live_state = ex.get("state") if ex is not None else None
                if live_state not in ("queued", "running"):
                    c["exec"] = Map(dict(snap["exec"] or {"state": "idle", "count": None}))
            for k in ("order", "kind", "kernel", "author", "created_by_run"):
                if c.get(k) != snap[k]:
                    c[k] = snap[k]

"""DocStore: one pycrdt Doc per open notebook, plus websocket client sync.

Wire protocol on the websocket is the standard y-websocket one (binary):
  byte 0 = message type: 0 sync, 1 awareness (2 queryAwareness is accepted)
so the browser can use y-websocket's ``WebsocketProvider`` unchanged, plus two
extensions (var-string JSON payloads, server -> client only):
  100 ``MSG_IDENTITY`` on connect: ``{"name": "A", "color": "#..."}``, a letter and
      colour unique among the live connections.
  101 ``MSG_STATUS`` on connect and on every change: file status
      ``{"state": "saved"|"saving"|"error"|"conflict", "path", "saved_at", "error"}``.
      A side channel on purpose: it is never stored in the Y doc.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from pycrdt import (
    Awareness,
    Doc,
    Encoder,
    YMessageType,
    YSyncMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
    handle_sync_message,
)

from . import schema

log = logging.getLogger("yupyter.store")

SAVE_DEBOUNCE_S = 2.0

MSG_IDENTITY = 100
MSG_STATUS = 101
EXTERNAL_ORIGIN = "external"
EXTERNAL_POLL_S = 1.0
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
COLORS = ["#4c8dd6", "#2fa36b", "#9b59d0", "#d4a017", "#d6456f", "#17a2b8", "#7a8b2f", "#5562d6", "#b0479c", "#8d6e63"]


def pick_identity(taken: list[dict]) -> dict:
    """First letter and colour not used by ``taken`` (letters get a digit past Z)."""
    names = {t["name"] for t in taken}
    colors = {t["color"] for t in taken}
    n = 0
    while (name := LETTERS[n % 26] + (str(n // 26 + 1) if n >= 26 else "")) in names:
        n += 1
    color = next((c for c in COLORS if c not in colors), COLORS[len(taken) % len(COLORS)])
    return {"name": name, "color": color}


def identity_message(identity: dict) -> bytes:
    enc = Encoder()
    enc.write_var_string(json.dumps(identity))
    return bytes([MSG_IDENTITY]) + enc.to_bytes()


def status_message(status: dict) -> bytes:
    enc = Encoder()
    enc.write_var_string(json.dumps(status))
    return bytes([MSG_STATUS]) + enc.to_bytes()


def _sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class Connection:
    """One websocket client of an OpenDoc."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.awareness_ids: set[int] = set()
        self.identity: dict = {}

    def send(self, data: bytes) -> None:
        self.queue.put_nowait(data)


class OpenDoc:
    def __init__(self, path: Path, doc: Doc, on_change: Callable[["OpenDoc"], None]) -> None:
        self.path = path
        self.doc = doc
        self.awareness = Awareness(doc)
        self.awareness.set_local_state(None)  # server has no presence of its own
        self.connections: set[Connection] = set()
        self._on_change = on_change
        self.quiet = False  # True while applying an external reload: don't mark dirty
        self.status_fn: Callable[[], dict] | None = None
        self._sub = doc.observe(self._on_update)

    def _on_update(self, event) -> None:
        msg = create_update_message(event.update)
        origin_hash = event.transaction.origin()  # pycrdt exposes hash(origin)
        for conn in list(self.connections):
            if origin_hash == hash(conn):
                continue
            conn.send(msg)
        if not self.quiet:
            self._on_change(self)

    # -- websocket session -------------------------------------------------
    async def serve(self, ws) -> None:
        """Run the sync protocol for one accepted Starlette WebSocket."""
        conn = Connection(ws)
        conn.identity = pick_identity([c.identity for c in self.connections])
        self.connections.add(conn)

        async def sender() -> None:
            while True:
                data = await conn.queue.get()
                if data is None:
                    return
                await ws.send_bytes(data)

        send_task = asyncio.create_task(sender())
        try:
            # initial handshake: identity, our state vector (step1) + current awareness
            conn.send(identity_message(conn.identity))
            if self.status_fn:
                conn.send(status_message(self.status_fn()))
            conn.send(create_sync_message_step1(self.doc))
            if self.awareness.states:
                conn.send(
                    create_awareness_message(
                        self.awareness.encode_awareness_update(list(self.awareness.states))
                    )
                )
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    self._handle(conn, data)
        finally:
            self.connections.discard(conn)
            conn.queue.put_nowait(None)
            send_task.cancel()
            if conn.awareness_ids:
                ids = [i for i in conn.awareness_ids if i in self.awareness.states]
                self.awareness.remove_awareness_states(ids, "disconnect")
                if ids:
                    payload = create_awareness_message(
                        self.awareness.encode_awareness_update(ids)
                    )
                    for other in list(self.connections):
                        other.send(payload)

    def _handle(self, conn: Connection, data: bytes) -> None:
        mtype = data[0]
        if mtype == YMessageType.SYNC:
            with_origin = _ApplyWithOrigin(self.doc, conn)
            reply = with_origin.handle(data[1:])
            if reply is not None:
                conn.send(reply)
        elif mtype == YMessageType.AWARENESS:
            from pycrdt import read_message

            update = read_message(data[1:])
            self.awareness.apply_awareness_update(update, conn)
            # track which client ids this connection owns, for cleanup
            from pycrdt import Decoder

            d = Decoder(update)
            for _ in range(d.read_var_uint()):
                cid = d.read_var_uint()
                d.read_var_uint()
                d.read_var_string()
                conn.awareness_ids.add(cid)
            raw = data
            for other in list(self.connections):
                if other is not conn:
                    other.send(raw)
        elif mtype == 3:  # queryAwareness
            conn.send(
                create_awareness_message(
                    self.awareness.encode_awareness_update(list(self.awareness.states))
                )
            )


def create_sync_message_step1(doc: Doc) -> bytes:
    return create_sync_message(doc)


class _ApplyWithOrigin:
    """Apply a sync message so resulting doc updates carry ``conn`` as origin."""

    def __init__(self, doc: Doc, conn: Connection) -> None:
        self.doc, self.conn = doc, conn

    def handle(self, msg: bytes) -> bytes | None:
        if msg[0] == YSyncMessageType.SYNC_STEP1:
            return handle_sync_message(msg, self.doc)  # read-only
        with self.doc.transaction(origin=self.conn):
            return handle_sync_message(msg, self.doc)


class DocStore:
    """Holds the open documents. Single event loop; not thread-safe.

    ``root`` (optional) is only used to report paths relative to it in status
    messages. Save state per path: pending (``_dirty``), last error, a conflict
    (file changed on disk while we hold unsaved changes: saving is paused).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self._docs: dict[Path, OpenDoc] = {}
        self._dirty: set[Path] = set()
        self._last_saved: dict[Path, str] = {}  # text of our last write / last read of the file
        self._disk_sig: dict[Path, tuple[int, int] | None] = {}  # (mtime_ns, size) after that
        self._saved_at: dict[Path, float | None] = {}
        self._errors: dict[Path, str] = {}
        self._conflict: set[Path] = set()
        self._emitted: dict[Path, str] = {}
        self._save_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None

    # -- status -------------------------------------------------------------------
    def rel(self, path: Path) -> str:
        if self.root is not None:
            try:
                return path.relative_to(self.root).as_posix()
            except ValueError:
                pass
        return path.name

    def status(self, path: Path) -> dict:
        if path in self._conflict:
            state = "conflict"
        elif path in self._errors:
            state = "error"
        elif path in self._dirty:
            state = "saving"
        else:
            state = "saved"
        return {
            "state": state,
            "path": self.rel(path),
            "saved_at": self._saved_at.get(path),
            "error": self._errors.get(path),
        }

    def _emit(self, path: Path) -> None:
        """Push the status to the doc's clients if it changed since the last push."""
        od = self._docs.get(path)
        if od is None:
            return
        st = self.status(path)
        key = json.dumps(st, sort_keys=True)
        if self._emitted.get(path) == key:
            return
        self._emitted[path] = key
        msg = status_message(st)
        for conn in list(od.connections):
            conn.send(msg)

    def peers(self, path: Path) -> list[dict]:
        od = self._docs.get(path)
        return [dict(c.identity) for c in od.connections] if od else []

    # -- persistence ------------------------------------------------------------
    @staticmethod
    def assets_dir(path: Path) -> Path:
        """``<dir>/<name>.assets`` next to the notebook (``a.md`` -> ``a.assets``)."""
        return path.with_name(path.stem + ".assets")

    def load(self, path: Path) -> Doc:
        """Return a Doc for ``path``: parsed from the file, or empty if absent."""
        from . import markdown_io

        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return markdown_io.load_markdown(text, self.assets_dir(path))
        return schema.new_doc()

    def save(self, path: Path, force: bool = False) -> None:
        """Atomically write the open doc for ``path`` plus its assets dir.

        Unless ``force``: does nothing while the file is in conflict, and first
        checks whether the file changed on disk behind our back (which either
        reloads it, or enters conflict if we have unsaved changes).
        """
        od = self._docs.get(path)
        if od is None:
            return
        if not force:
            if path in self._conflict:
                return
            if self.check_external(path, pending=path in self._dirty) == "conflict":
                return
        try:
            self._write(path, od)
        except Exception as e:
            self._dirty.discard(path)
            self._errors[path] = f"{type(e).__name__}: {e}"
            self._emit(path)
            raise
        self._dirty.discard(path)
        self._errors.pop(path, None)
        self._conflict.discard(path)
        self._saved_at[path] = time.time()
        self._emit(path)

    def _write(self, path: Path, od: OpenDoc) -> None:
        from . import markdown_io

        assets = self.assets_dir(path)
        # Render into a scratch dir first so a failure leaves the old files alone.
        tmp_assets = assets.with_name(assets.name + ".tmp")
        shutil.rmtree(tmp_assets, ignore_errors=True)
        try:
            text = markdown_io.dump_markdown(od.doc, tmp_assets)
            # The text links to ``<assets.name>/...``; tmp dir only differs in name.
            text = text.replace(quote(tmp_assets.name) + "/", quote(assets.name) + "/")
            if tmp_assets.exists():
                old = assets.with_name(assets.name + ".old")
                shutil.rmtree(old, ignore_errors=True)
                if assets.exists():
                    os.replace(assets, old)
                os.replace(tmp_assets, assets)
                shutil.rmtree(old, ignore_errors=True)
            else:
                shutil.rmtree(assets, ignore_errors=True)  # no rich outputs: drop stale files
            prev = self._last_saved.get(path)
            if prev is None and path.exists():
                prev = path.read_text(encoding="utf-8")
            if prev == text and path.exists():
                self._last_saved[path] = text
                return
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            self._last_saved[path] = text
            self._disk_sig[path] = _sig(path)
        finally:
            shutil.rmtree(tmp_assets, ignore_errors=True)

    # -- external edits -----------------------------------------------------------
    def check_external(self, path: Path, pending: bool | None = None) -> str | None:
        """Notice that ``path`` was changed on disk by someone else.

        Returns ``"reloaded"`` (no unsaved changes: the live doc now matches the
        file), ``"conflict"`` (unsaved changes: saving is paused until
        ``resolve``), or None. Our own writes are recognised by (mtime, size)
        and by content, so they never trigger this.
        """
        od = self._docs.get(path)
        if od is None:
            return None
        sig = _sig(path)
        if sig == self._disk_sig.get(path):
            return None
        self._disk_sig[path] = sig
        if sig is None:  # deleted: keep our copy, the next save recreates it
            return None
        text = path.read_text(encoding="utf-8")
        if text == self._last_saved.get(path):
            return None
        if pending is None:
            pending = path in self._dirty
        if pending or path in self._conflict:
            self._dirty.discard(path)
            self._conflict.add(path)
            self._emit(path)
            return "conflict"
        self._apply_external(od, text)
        return "reloaded"

    def _apply_external(self, od: OpenDoc, text: str) -> None:
        from . import markdown_io

        path = od.path
        new = markdown_io.load_markdown(text, self.assets_dir(path)) if text.strip() else schema.new_doc()
        od.quiet = True
        try:
            schema.replace_from(od.doc, new, origin=EXTERNAL_ORIGIN)
        finally:
            od.quiet = False
        self._last_saved[path] = text
        self._disk_sig[path] = _sig(path)
        self._dirty.discard(path)
        self._errors.pop(path, None)
        self._conflict.discard(path)
        self._saved_at[path] = time.time()
        self._emit(path)

    def resolve(self, path: Path, action: str) -> None:
        """Settle a conflict: ``"reload"`` takes the file, ``"keep"`` overwrites it with ours."""
        od = self.open(path)
        if action == "reload":
            if not path.exists():
                raise FileNotFoundError(path.name)
            self._apply_external(od, path.read_text(encoding="utf-8"))
        elif action == "keep":
            self.save(path, force=True)
        else:
            raise ValueError(f"unknown action {action!r}")

    def rename(self, old: Path, new: Path) -> None:
        """Move an open notebook (and its assets dir) to ``new``.

        Re-renders the doc at the new location (so asset links are rewritten),
        then removes the old file and assets. Raises FileExistsError on collision
        and RuntimeError while the file is in conflict.
        """
        od = self.open(old)
        if old in self._conflict:
            raise RuntimeError("resolve the external change first")
        if new.exists() or self.assets_dir(new).exists() or new in self._docs:
            raise FileExistsError(new.name)
        old_assets = self.assets_dir(old)
        self._docs.pop(old)
        self._docs[new] = od
        od.path = new
        moved = {
            "_last_saved": self._last_saved.pop(old, None),
            "_saved_at": self._saved_at.pop(old, None),
        }
        was_dirty = old in self._dirty
        self._dirty.discard(old)
        self._disk_sig.pop(old, None)
        self._errors.pop(old, None)
        self._emitted.pop(old, None)
        self._last_saved.pop(new, None)
        try:
            self.save(new, force=True)
        except Exception:
            self._docs.pop(new)
            self._docs[old] = od
            od.path = old
            if moved["_last_saved"] is not None:
                self._last_saved[old] = moved["_last_saved"]
            self._saved_at[old] = moved["_saved_at"]
            if was_dirty:
                self._dirty.add(old)
            self._disk_sig[old] = _sig(old)
            raise
        with contextlib.suppress(OSError):
            old.unlink()
        shutil.rmtree(old_assets, ignore_errors=True)
        self._emit(new)

    # -- lifecycle ------------------------------------------------------------
    def open(self, path: str | Path) -> OpenDoc:
        p = Path(path).resolve()
        od = self._docs.get(p)
        if od is None:
            od = OpenDoc(p, self.load(p), self._mark_dirty)
            od.status_fn = lambda od=od: self.status(od.path)
            self._docs[p] = od
            sig = _sig(p)
            self._disk_sig[p] = sig
            if sig:
                self._last_saved[p] = p.read_text(encoding="utf-8")
            self._saved_at[p] = sig[0] / 1e9 if sig else None
        return od

    def get_doc(self, path: str | Path) -> Doc:
        return self.open(path).doc

    def paths(self) -> list[Path]:
        return list(self._docs)

    def _mark_dirty(self, od: OpenDoc) -> None:
        if od.path in self._conflict:
            return  # paused until the user picks Reload / Keep mine
        self._dirty.add(od.path)
        self._emit(od.path)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._save_task is None or self._save_task.done():
            self._save_task = loop.create_task(self._debounced_save())

    async def _debounced_save(self) -> None:
        while self._dirty:
            await asyncio.sleep(SAVE_DEBOUNCE_S)
            for p in list(self._dirty):
                try:
                    self.save(p)
                except Exception:
                    log.exception("save failed for %s", p)
                self._dirty.discard(p)  # failures wait for the next edit or an explicit save

    def start(self) -> None:
        """Start the external-edit watcher (needs a running loop)."""
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(EXTERNAL_POLL_S)
            for p in list(self._docs):
                try:
                    self.check_external(p)
                except Exception:
                    log.exception("external check failed for %s", p)

    def save_all(self) -> None:
        for p in list(self._docs):
            try:
                self.save(p)
            except Exception:
                log.exception("save failed for %s", p)
        self._dirty.clear()

    async def close(self) -> None:
        for t in (self._save_task, self._watch_task):
            if t:
                t.cancel()
        for p in self._conflict:
            log.warning("not saving %s: it changed on disk and the conflict was not resolved", p)
        self.save_all()

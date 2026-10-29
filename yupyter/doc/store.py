"""DocStore: one pycrdt Doc per open notebook, plus websocket client sync.

Wire protocol on the websocket is the standard y-websocket one (binary):
  byte 0 = message type: 0 sync, 1 awareness (2 queryAwareness is accepted)
so the browser can use y-websocket's ``WebsocketProvider`` unchanged.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from pycrdt import (
    Awareness,
    Doc,
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


class Connection:
    """One websocket client of an OpenDoc."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.awareness_ids: set[int] = set()

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
        self._sub = doc.observe(self._on_update)

    def _on_update(self, event) -> None:
        msg = create_update_message(event.update)
        origin_hash = event.transaction.origin()  # pycrdt exposes hash(origin)
        for conn in list(self.connections):
            if origin_hash == hash(conn):
                continue
            conn.send(msg)
        self._on_change(self)

    # -- websocket session -------------------------------------------------
    async def serve(self, ws) -> None:
        """Run the sync protocol for one accepted Starlette WebSocket."""
        conn = Connection(ws)
        self.connections.add(conn)

        async def sender() -> None:
            while True:
                data = await conn.queue.get()
                if data is None:
                    return
                await ws.send_bytes(data)

        send_task = asyncio.create_task(sender())
        try:
            # initial handshake: our state vector (step1) + current awareness
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
    """Holds the open documents. Single event loop; not thread-safe."""

    def __init__(self) -> None:
        self._docs: dict[Path, OpenDoc] = {}
        self._dirty: set[Path] = set()
        self._last_saved: dict[Path, str] = {}
        self._save_task: asyncio.Task | None = None

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

    def save(self, path: Path) -> None:
        """Atomically write the open doc for ``path`` plus its assets dir."""
        from . import markdown_io

        od = self._docs.get(path)
        if od is None:
            return
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
            if prev == text:
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
        finally:
            shutil.rmtree(tmp_assets, ignore_errors=True)

    # -- lifecycle ------------------------------------------------------------
    def open(self, path: str | Path) -> OpenDoc:
        p = Path(path).resolve()
        od = self._docs.get(p)
        if od is None:
            od = OpenDoc(p, self.load(p), self._mark_dirty)
            self._docs[p] = od
        return od

    def get_doc(self, path: str | Path) -> Doc:
        return self.open(path).doc

    def paths(self) -> list[Path]:
        return list(self._docs)

    def _mark_dirty(self, od: OpenDoc) -> None:
        self._dirty.add(od.path)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._save_task is None or self._save_task.done():
            self._save_task = loop.create_task(self._debounced_save())

    async def _debounced_save(self) -> None:
        while self._dirty:
            await asyncio.sleep(SAVE_DEBOUNCE_S)
            dirty, self._dirty = self._dirty, set()
            for p in dirty:
                try:
                    self.save(p)
                except Exception:
                    log.exception("save failed for %s", p)

    def save_all(self) -> None:
        for p in list(self._docs):
            try:
                self.save(p)
            except Exception:
                log.exception("save failed for %s", p)
        self._dirty.clear()

    async def close(self) -> None:
        if self._save_task:
            self._save_task.cancel()
        self.save_all()

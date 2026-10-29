import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn
import websockets
from pycrdt import (
    Doc,
    YMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
    handle_sync_message,
)

from yupyter.doc import schema
from yupyter.server.app import create_app

TOKEN = "tok"


@pytest.fixture
def server(tmp_path):
    app = create_app(tmp_path / "nb.md", TOKEN, port=0)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield port
    srv.should_exit = True
    t.join(15)


class Client:
    """A minimal y-websocket client around a pycrdt Doc."""

    def __init__(self, ws):
        self.ws, self.doc = ws, Doc()
        self.awareness_msgs = []
        self.doc.observe(self._on_update)

    def _on_update(self, ev):
        if ev.transaction.origin() != hash("remote"):
            asyncio.get_event_loop().create_task(self.ws.send(create_update_message(ev.update)))

    async def pump(self, until, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if until():
                return
            try:
                data = await asyncio.wait_for(self.ws.recv(), 0.2)
            except asyncio.TimeoutError:
                continue
            if data[0] == YMessageType.SYNC:
                with self.doc.transaction(origin="remote"):
                    reply = handle_sync_message(data[1:], self.doc)
                if reply:
                    await self.ws.send(reply)
            elif data[0] == YMessageType.AWARENESS:
                self.awareness_msgs.append(data)
        raise AssertionError("timeout")


async def connect(port, token=TOKEN):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws/main?token={token}")
    c = Client(ws)
    await ws.send(create_sync_message(c.doc))
    return c


async def test_auth(server):
    async with httpx.AsyncClient() as http:
        r = await http.get(f"http://127.0.0.1:{server}/api/info")
        assert r.status_code == 401
        r = await http.get(f"http://127.0.0.1:{server}/api/info?token={TOKEN}", follow_redirects=True)
        assert r.status_code == 200  # cookie set, redirected
        assert (await http.get(f"http://127.0.0.1:{server}/api/info")).status_code == 200
    with pytest.raises(Exception):
        await connect(server, "bad")


async def test_sync_run_and_awareness(server):
    c1 = await connect(server)
    c2 = await connect(server)
    await c1.pump(lambda: True)
    # client 1 creates a cell; client 2 sees it
    cid = schema.insert_cell(c1.doc, None, "code", "python3", "2 + 2", "u1")
    await c2.pump(lambda: cid in schema.cells(c2.doc))
    # run via RPC; both clients see output written by the server
    # token cookie name is yupyter_token when create_app(port=None/0)
    async with httpx.AsyncClient() as http:
        http.cookies.set("yupyter_token", TOKEN, domain="127.0.0.1")
        r = await http.post(f"http://127.0.0.1:{server}/api/run", json={"cell_id": cid})
        assert r.status_code == 202
    def has_out(c):
        return lambda: len(schema.get_outputs(c.doc, cid)) > 0
    await c1.pump(has_out(c1), 30)
    await c2.pump(has_out(c2), 30)
    assert schema.get_outputs(c2.doc, cid)[0]["data"]["text/plain"] == "4"
    # awareness is relayed
    from pycrdt import Awareness
    aw = Awareness(c1.doc)
    aw.set_local_state({"user": {"name": "a"}})
    await c1.ws.send(create_awareness_message(aw.encode_awareness_update([aw.client_id])))
    await c2.pump(lambda: bool(c2.awareness_msgs))
    await c1.ws.close()
    await c2.ws.close()

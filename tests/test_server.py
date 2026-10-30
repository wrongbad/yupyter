import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn
import websockets
from pycrdt import (
    Decoder,
    Doc,
    YMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
    handle_sync_message,
)

from yupyter.doc import schema
from yupyter.doc.store import MSG_IDENTITY, MSG_STATUS, DocStore, pick_identity
from yupyter.server.app import STATIC_DIR, create_app

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
        self.identity = None
        self.statuses = []
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
            elif data[0] == MSG_STATUS:
                self.statuses.append(json.loads(Decoder(data[1:]).read_var_string()))
            elif data[0] == MSG_IDENTITY:
                self.identity = json.loads(Decoder(data[1:]).read_var_string())
        raise AssertionError("timeout")


async def connect(port, token=TOKEN, path="nb.md"):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws/{path}?token={token}")
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


async def test_info_lists_kernels(server):
    async with httpx.AsyncClient() as http:
        r = await http.get(f"http://127.0.0.1:{server}/api/info?token={TOKEN}", follow_redirects=True)
        kernels = r.json()["kernels"]
        assert "agent:claude" in kernels
        assert all(isinstance(k, str) for k in kernels)


@pytest.mark.skipif(not (STATIC_DIR / "index.html").exists(), reason="frontend not built")
async def test_index_not_cached(server):
    async with httpx.AsyncClient() as http:
        r = await http.get(f"http://127.0.0.1:{server}/?token={TOKEN}", follow_redirects=True)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"


async def test_connections_get_unique_identities(server):
    a, b = await connect(server), await connect(server)
    await a.pump(lambda: a.identity)
    await b.pump(lambda: b.identity)
    assert a.identity["name"] == "A" and b.identity["name"] == "B"
    assert a.identity["color"] != b.identity["color"]
    await a.ws.close()
    await asyncio.sleep(0.3)
    c = await connect(server)  # A is free again
    await c.pump(lambda: c.identity)
    assert c.identity["name"] == "A"


def test_pick_identity_past_z():
    taken = [pick_identity([]) for _ in range(1)]
    while len(taken) < 27:
        taken.append(pick_identity(taken))
    assert [t["name"] for t in taken[24:]] == ["Y", "Z", "A2"]


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
        r = await http.post(f"http://127.0.0.1:{server}/api/run/nb.md", json={"cell_id": cid})
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


# ---------------------------------------------------------------------------
# multi-notebook server, file status, external edits, rename
# ---------------------------------------------------------------------------
from starlette.testclient import TestClient  # noqa: E402

import yupyter.doc.store as store_mod  # noqa: E402
from yupyter.cli import resolve_target  # noqa: E402
from yupyter.scheduler import RunScheduler  # noqa: E402
from yupyter.server.paths import PathError, resolve_rel  # noqa: E402

PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, TOKEN)
    with TestClient(app, cookies={"yupyter_token": TOKEN}) as c:
        c.root = tmp_path
        yield c


def test_traversal_rejected(tmp_path):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (tmp_path / "secret.md").write_text("x")
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    (root / "flink.md").symlink_to(tmp_path / "secret.md")
    for bad in ["../secret.md", "a/../../secret.md", "/etc/passwd", str(tmp_path / "secret.md"), "link/secret.md", "flink.md"]:
        with pytest.raises(PathError) as ei:
            resolve_rel(root, bad, notebook=True)
        assert ei.value.status == 403, bad
    with pytest.raises(PathError):
        resolve_rel(root, ".git/x.md", notebook=True)
    with pytest.raises(PathError):
        resolve_rel(root, "x.txt", notebook=True)
    assert resolve_rel(root, "a/b.md", notebook=True) == root / "a" / "b.md"


def test_routes_reject_traversal(client):
    (client.root.parent / "outside.md").write_text("x")
    for url in ["/api/info/%2e%2e/outside.md", "/api/info//etc/hosts.md", "/api/tree?dir=..", "/api/tree?dir=/etc"]:
        assert client.get(url).status_code in (403, 404), url
    assert client.post("/api/new", json={"dir": "..", "name": "x"}).status_code == 403
    assert client.post("/api/new", json={"dir": "", "name": "../x"}).status_code == 400
    assert client.post("/api/save/%2e%2e/outside.md").status_code == 403
    assert client.post("/api/run/%2e%2e/outside.md", json={"cell_id": "a"}).status_code == 403
    (client.root / "esc").symlink_to(client.root.parent, target_is_directory=True)
    assert client.get("/api/tree?dir=esc").status_code == 403
    assert "esc" not in [e["name"] for e in client.get("/api/tree").json()["entries"]]
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/../outside.md"):
            pass


def test_tree_and_new(client):
    root = client.root
    (root / "sub").mkdir()
    (root / "sub" / "deep.md").write_text("---\nyupyter: 1\n---\n")
    (root / "plain.md").write_text("# hi\n")
    (root / "data.csv").write_text("a,b")
    (root / ".hidden.md").write_text("x")
    (root / "nb.assets").mkdir()
    (root / "x.md.tmp").write_text("x")
    (root / "y.assets.old").mkdir()
    r = client.post("/api/new", json={"dir": "sub", "name": "fresh"})
    assert r.status_code == 201 and r.json()["path"] == "sub/fresh.md"
    assert "yupyter: 1" in (root / "sub" / "fresh.md").read_text()
    assert client.post("/api/new", json={"dir": "sub", "name": "fresh.md"}).status_code == 409
    top = {e["name"]: e for e in client.get("/api/tree").json()["entries"]}
    assert set(top) == {"sub", "plain.md", "data.csv"}
    assert top["sub"]["type"] == "dir" and top["plain.md"]["type"] == "markdown" and top["data.csv"]["type"] == "other"
    sub = {e["name"]: e for e in client.get("/api/tree?dir=sub").json()["entries"]}
    assert sub["deep.md"]["type"] == "notebook" and sub["deep.md"]["path"] == "sub/deep.md"
    assert sub["fresh.md"]["type"] == "notebook" and sub["fresh.md"]["kernel_running"] is False
    assert list(top)[0] == "sub"  # dirs first


def test_create_app_back_compat_and_initial(tmp_path):
    app = create_app(tmp_path / "old.md", TOKEN)
    with TestClient(app, cookies={"yupyter_token": TOKEN}) as c:
        info = c.get("/api/info").json()
        assert info["initial"] == "old.md"
        assert c.get("/api/info/old.md").json()["name"] == "old.md"
        assert c.get("/n/whatever/x.md").status_code == 200  # SPA fallback (placeholder or index)


def test_resolve_target(tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    assert resolve_target(None, tmp_path) == (tmp_path.resolve(), None)
    assert resolve_target("d", tmp_path) == ((tmp_path / "d").resolve(), None)
    assert resolve_target("notes.md", tmp_path) == (tmp_path.resolve(), "notes.md")
    assert resolve_target("d/n", tmp_path) == (tmp_path.resolve(), "d/n.md")
    other = tmp_path.parent / (tmp_path.name + "-o")
    other.mkdir()
    assert resolve_target(str(other / "z.md"), tmp_path) == (other.resolve(), "z.md")


@pytest.fixture
def fast_server(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "SAVE_DEBOUNCE_S", 0.1)
    monkeypatch.setattr(store_mod, "EXTERNAL_POLL_S", 0.1)
    (tmp_path / "sub").mkdir()
    app = create_app(tmp_path, TOKEN, port=0)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield srv.servers[0].sockets[0].getsockname()[1]
    srv.should_exit = True
    t.join(15)


def _http(port):
    h = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
    h.cookies.set("yupyter_token", TOKEN, domain="127.0.0.1")
    return h


async def test_save_status_sequence_and_force_flush(fast_server, tmp_path):
    c = await connect(fast_server, path="sub/a.md")
    await c.pump(lambda: c.statuses)
    assert c.statuses[0]["state"] == "saved" and c.statuses[0]["path"] == "sub/a.md"
    schema.insert_cell(c.doc, None, "code", "python3", "1", "u")
    await c.pump(lambda: [s["state"] for s in c.statuses][-2:] == ["saving", "saved"])
    assert c.statuses[-1]["saved_at"] > 0
    assert "```py" in (tmp_path / "sub" / "a.md").read_text()
    # an explicit flush is immediate
    schema.insert_cell(c.doc, None, "code", "python3", "2", "u")
    await c.pump(lambda: c.statuses[-1]["state"] == "saving")
    async with _http(fast_server) as h:
        r = await h.post("/api/save/sub/a.md")
        assert r.json()["status"]["state"] == "saved"
    assert (tmp_path / "sub" / "a.md").read_text().count("```py") == 2
    await c.ws.close()


async def test_external_edit_reloads_live_doc(fast_server, tmp_path):
    nb = tmp_path / "e.md"
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 1\n```\n")
    c = await connect(fast_server, path="e.md")
    await c.pump(lambda: "aaaaaa" in schema.cells(c.doc))
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 2\n```\n\n```py {#bbbbbb}\ny = 3\n```\n")
    await c.pump(lambda: "bbbbbb" in schema.cells(c.doc), 10)
    assert schema.get_source(c.doc, "aaaaaa") == "x = 2"
    await c.pump(lambda: True)
    assert c.statuses[-1]["state"] == "saved"
    await asyncio.sleep(0.5)  # the reload must not have re-dirtied and rewritten the doc
    assert "x = 2" in nb.read_text() and not [s for s in c.statuses if s["state"] == "saving"]
    await c.ws.close()


async def test_external_edit_conflict_pauses_saving(fast_server, tmp_path, monkeypatch):
    nb = tmp_path / "k.md"
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 1\n```\n")
    c = await connect(fast_server, path="k.md")
    await c.pump(lambda: "aaaaaa" in schema.cells(c.doc))
    await c.pump(lambda: c.statuses)
    monkeypatch.setattr(store_mod, "SAVE_DEBOUNCE_S", 5)  # keep our edit pending; the external write lands first
    schema.insert_cell(c.doc, None, "code", "python3", "mine = 1", "u", cell_id="cccccc")
    await c.pump(lambda: c.statuses[-1]["state"] == "saving")
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 99\n```\n")
    await c.pump(lambda: c.statuses[-1]["state"] == "conflict", 10)
    monkeypatch.setattr(store_mod, "SAVE_DEBOUNCE_S", 0.1)
    schema.insert_cell(c.doc, None, "code", "python3", "more = 1", "u", cell_id="dddddd")
    await asyncio.sleep(0.6)
    assert "x = 99" in nb.read_text() and "mine" not in nb.read_text()  # saving is paused
    async with _http(fast_server) as h:
        assert (await h.post("/api/resolve/k.md", json={"action": "keep"})).json()["status"]["state"] == "saved"
    assert "mine = 1" in nb.read_text() and "more = 1" in nb.read_text() and "x = 99" not in nb.read_text()
    await c.ws.close()


async def test_conflict_reload_takes_disk_version(fast_server, tmp_path, monkeypatch):
    nb = tmp_path / "r.md"
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 1\n```\n")
    c = await connect(fast_server, path="r.md")
    await c.pump(lambda: "aaaaaa" in schema.cells(c.doc))
    monkeypatch.setattr(store_mod, "SAVE_DEBOUNCE_S", 5)
    schema.edit_source(c.doc, "aaaaaa", "x = 1", "x = 5")
    await c.pump(lambda: c.statuses[-1]["state"] == "saving")
    nb.write_text("---\nyupyter: 1\n---\n\n```py {#aaaaaa}\nx = 7\n```\n")
    await c.pump(lambda: c.statuses[-1]["state"] == "conflict", 10)
    monkeypatch.setattr(store_mod, "SAVE_DEBOUNCE_S", 0.1)
    async with _http(fast_server) as h:
        assert (await h.post("/api/resolve/r.md", json={"action": "reload"})).status_code == 200
    await c.pump(lambda: schema.get_source(c.doc, "aaaaaa") == "x = 7" and c.statuses[-1]["state"] == "saved", 10)
    assert "x = 7" in nb.read_text()
    await c.ws.close()


def test_save_failure_reported(tmp_path):
    store = DocStore(tmp_path)
    p = tmp_path / "gone" / "f.md"
    p.parent.mkdir()
    store.get_doc(p)
    p.parent.rmdir()
    with pytest.raises(Exception):
        store.save(p)
    st = store.status(p)
    assert st["state"] == "error" and st["error"]
    p.parent.mkdir()
    store.save(p)
    assert store.status(p)["state"] == "saved" and p.exists()


def test_own_write_is_not_an_external_edit(tmp_path):
    store = DocStore(tmp_path)
    p = (tmp_path / "o.md").resolve()
    doc = store.get_doc(p)
    schema.insert_cell(doc, None, "code", "python3", "1", "u")
    store.save(p)
    assert store.check_external(p) is None
    p.write_text(p.read_text())  # touched, same content
    assert store.check_external(p) is None


def test_rename_moves_assets_and_rewrites_links(client):
    root = client.root
    r = client.post("/api/new", json={"dir": "", "name": "old"})
    assert r.status_code == 201
    store = client.app.app.state.store
    nb = (root / "old.md").resolve()
    doc = store.get_doc(nb)
    cid = schema.insert_cell(doc, None, "code", "python3", "plot()", "u")
    schema.append_output(doc, cid, {"output_type": "display_data", "data": {"image/png": PNG, "text/plain": "<Figure>"}, "metadata": {}})
    assert client.post("/api/save/old.md").status_code == 200
    assert (root / "old.assets").is_dir() and "old.assets/" in nb.read_text()
    (root / "taken.md").write_text("x")
    assert client.post("/api/rename/old.md", json={"to": "taken"}).status_code == 409
    assert client.post("/api/rename/old.md", json={"to": "../out"}).status_code == 403
    assert client.post("/api/rename/old.md", json={"to": "nodir/x"}).status_code == 404
    assert client.post("/api/rename/old.md", json={"to": ".hid"}).status_code == 400
    r = client.post("/api/rename/old.md", json={"to": "new name"})
    assert r.status_code == 200 and r.json()["path"] == "new name.md"
    assert not (root / "old.md").exists() and not (root / "old.assets").exists()
    text = (root / "new name.md").read_text()
    assert "new%20name.assets/" in text and "old.assets" not in text
    assert any((root / "new name.assets").iterdir())
    assert store.status((root / "new name.md").resolve())["path"] == "new name.md"
    # still editable + saved at the new location, nothing recreated at the old one
    schema.insert_cell(doc, None, "code", "python3", "z", "u")
    assert client.post("/api/save/new name.md").status_code == 200
    assert "```py" in (root / "new name.md").read_text() and not (root / "old.md").exists()
    # and the reloaded file round-trips its image
    assert "image/png" in str(store.load((root / "new name.md").resolve()).get("cells", type=__import__("pycrdt").Map).to_py())


async def test_kernel_per_notebook(tmp_path):
    store = DocStore(tmp_path)
    s = RunScheduler(store)
    a, b = (tmp_path / "a.md").resolve(), (tmp_path / "b.md").resolve()
    try:
        ca = schema.insert_cell(store.get_doc(a), None, "code", "python3", "x = 'A'", "u")
        cb = schema.insert_cell(store.get_doc(b), None, "code", "python3", "x = 'B'", "u")
        await s.run_cell(a, ca)
        await s.run_cell(b, cb)
        pa = schema.insert_cell(store.get_doc(a), None, "code", "python3", "x", "u")
        pb = schema.insert_cell(store.get_doc(b), None, "code", "python3", "x", "u")
        assert (await s.run_cell(a, pa))[0]["data"]["text/plain"] == "'A'"
        assert (await s.run_cell(b, pb))[0]["data"]["text/plain"] == "'B'"
        assert s.kernel_running(a) and s.kernel_running(b)
        assert not s.kernel_running((tmp_path / "c.md").resolve())
        # a rename carries the kernel (and its state) along
        s.rename(a, (tmp_path / "a2.md").resolve())
        assert not s.kernel_running(a) and s.kernel_running((tmp_path / "a2.md").resolve())
    finally:
        await s.shutdown()

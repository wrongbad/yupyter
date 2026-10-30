import pytest
from starlette.testclient import TestClient

from yupyter.doc import presence, schema, undo
from yupyter.doc.store import DocStore
from yupyter.server.app import create_app


def test_undo_run_only_reverts_that_run():
    doc = schema.new_doc()
    a = schema.insert_cell(doc, None, "code", "python3", "x=1", "human")
    undo.track_run(doc, "r1")
    schema.insert_cell(doc, a, "code", "python3", "y=2", "claude", origin=undo.run_origin("r1"))
    schema.edit_source(doc, a, "x=1", "x=5", origin=undo.run_origin("r1"))
    schema.set_exec_state(doc, a, "done", count=3)  # server write: not undone
    assert undo.undo_run(doc, "r1") is True
    assert schema.ordered_ids(doc) == [a]
    assert schema.get_source(doc, a) == "x=1"
    assert schema.cell_snapshot(doc, a)["exec"]["state"] == "done"
    assert undo.undo_run(doc, "r1") is False
    with pytest.raises(KeyError):
        undo.undo_run(doc, "nope")


def test_undo_route(tmp_path):
    store = DocStore()
    app = create_app(tmp_path / "nb.md", "tok", store=store)
    doc = store.get_doc((tmp_path / "nb.md").resolve())
    undo.track_run(doc, "r9")
    cid = schema.insert_cell(doc, None, "code", "python3", "1", "claude", origin=undo.run_origin("r9"))
    with TestClient(app, cookies={"yupyter_token": "tok"}) as c:
        assert c.post("/api/undo_run/nb.md", json={}).status_code == 400
        assert c.post("/api/undo_run/nb.md", json={"run_id": "zz"}).status_code == 404
        r = c.post("/api/undo_run/nb.md", json={"run_id": "r9"})
        assert r.json() == {"run_id": "r9", "undone": True}
    assert not schema.has_cell(doc, cid)


class FakeConn:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def test_agent_presence(tmp_path):
    od = DocStore().open(tmp_path / "x.md")
    conn = FakeConn()
    od.connections.add(conn)
    presence.set_agent_presence(od, "claude", "abc123")
    cid = presence.agent_client_id("claude")
    st = od.awareness.states[cid]
    assert st["user"]["kind"] == "agent" and st["user"]["name"] == "claude"
    assert st["focus"] == {"cellId": "abc123"}
    assert len(conn.sent) == 1 and conn.sent[0][0] == 1  # awareness message
    presence.set_agent_presence(od, "claude", None)
    assert od.awareness.states[cid]["focus"] == {"cellId": None}
    presence.clear_agent_presence(od, "claude")
    assert cid not in od.awareness.states
    assert len(conn.sent) == 3


def test_redo_run_route_and_unknown_run_404(tmp_path):
    store = DocStore()
    app = create_app(tmp_path / "nb.md", "tok", store=store)
    doc = store.get_doc((tmp_path / "nb.md").resolve())
    undo.track_run(doc, "r5")
    cid = schema.insert_cell(doc, None, "code", "python3", "1", "claude", origin=undo.run_origin("r5"))
    with TestClient(app, cookies={"yupyter_token": "tok"}) as c:
        assert c.post("/api/redo_run/nb.md", json={}).status_code == 400
        assert c.post("/api/redo_run/nb.md", json={"run_id": "zz"}).status_code == 404
        assert c.post("/api/redo_run/nb.md", json={"run_id": "r5"}).json() == {"run_id": "r5", "redone": False}
        assert c.post("/api/undo_run/nb.md", json={"run_id": "r5"}).json()["undone"] is True
        assert not schema.has_cell(doc, cid)
        assert c.post("/api/redo_run/nb.md", json={"run_id": "r5"}).json() == {"run_id": "r5", "redone": True}
        assert schema.has_cell(doc, cid)


def test_undone_flag_is_shared_state_not_reverted_by_undo():
    doc = schema.new_doc()
    ag = schema.insert_cell(doc, None, "code", "agent:claude", "go", "human")
    with doc.transaction(origin=schema.SERVER_ORIGIN):
        schema.get_cell(doc, ag)["exec"]["runs"] = ["r7"]
    undo.track_run(doc, "r7")
    schema.insert_cell(doc, ag, "code", "python3", "1", "claude", origin=undo.run_origin("r7"))
    undo.undo_run(doc, "r7")
    assert schema.cell_snapshot(doc, ag)["exec"]["undone"] is True
    undo.redo_run(doc, "r7")  # the flag write itself was not captured: redo still has the cell to restore
    assert schema.cell_snapshot(doc, ag)["exec"]["undone"] is False
    assert len(schema.ordered_ids(doc)) == 2
    undo.undo_run(doc, "r7")  # and a second undo still works
    assert len(schema.ordered_ids(doc)) == 1

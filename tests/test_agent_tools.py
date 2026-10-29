import time

import pytest

from yupyter.agent.tools import ToolError
from yupyter.doc import schema

from agent_support import add_agent_cell, make_toolbox, nb  # noqa: F401


async def test_insert_cell_defaults_and_chaining(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    below = schema.insert_cell(doc, ag, "code", "python3", "below", "me")
    tb = make_toolbox(store, sched, path, doc, ag)
    r1 = await tb.call("insert_cell", {"source": "a = 1", "kernel": "py"})
    r2 = await tb.call("insert_cell", {"source": "b = 2"})
    r3 = await tb.call("insert_cell", {"source": "# notes", "kind": "markdown"})
    ids = schema.ordered_ids(doc)
    assert ids == [ag, r1.data["id"], r2.data["id"], r3.data["id"], below]
    c = schema.cell_snapshot(doc, r1.data["id"])
    assert c["author"] == "claude" and c["created_by_run"] == tb.ctx.run_id
    assert c["kernel"] == "python3"  # alias resolved
    assert schema.cell_snapshot(doc, r3.data["id"])["kind"] == "markdown"
    r4 = await tb.call("insert_cell", {"source": "c", "after_id": below})
    assert schema.ordered_ids(doc)[-1] == r4.data["id"]


async def test_insert_cell_rejects_bad_input(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    tb = make_toolbox(store, sched, path, doc, ag)
    with pytest.raises(ToolError, match="unknown kernel"):
        await tb.call("insert_cell", {"source": "x", "kernel": "cobol"})
    with pytest.raises(ToolError, match="agent"):
        await tb.call("insert_cell", {"source": "x", "kernel": "claude"})
    with pytest.raises(ToolError, match="no such cell"):
        await tb.call("insert_cell", {"source": "x", "after_id": "zzzz"})
    with pytest.raises(ToolError, match="kind"):
        await tb.call("insert_cell", {"source": "x", "kind": "raw"})
    with pytest.raises(ToolError, match="bad arguments"):
        await tb.call("insert_cell", {"nope": 1})
    assert schema.ordered_ids(doc) == [ag]


async def test_edit_cell_search_replace(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    c = schema.insert_cell(doc, ag, "code", "python3", "x = 1\ny = 1\n", "me")
    tb = make_toolbox(store, sched, path, doc, ag)
    await tb.call("edit_cell", {"id": c, "old": "x = 1", "new": "x = 42"})
    assert schema.get_source(doc, c) == "x = 42\ny = 1\n"
    with pytest.raises(ToolError, match="exactly once"):
        await tb.call("edit_cell", {"id": c, "old": "= ", "new": "=="})
    with pytest.raises(ToolError, match="exactly once"):
        await tb.call("edit_cell", {"id": c, "old": "absent", "new": "z"})
    with pytest.raises(ToolError, match="agent cell"):
        await tb.call("edit_cell", {"id": ag, "old": "do", "new": "undo"})
    assert schema.get_source(doc, c) == "x = 42\ny = 1\n"


async def test_run_cell_real_kernel_text_error_and_image(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    tb = make_toolbox(store, sched, path, doc, ag)
    c1 = (await tb.call("insert_cell", {"source": "print('hello'); 6*7"})).data["id"]
    r = await tb.call("run_cell", {"id": c1})
    assert "hello" in r.text and "42" in r.text and "finished ok" in r.text
    c2 = (await tb.call("insert_cell", {"source": "1/0"})).data["id"]
    r = await tb.call("run_cell", {"id": c2})
    assert "ZeroDivisionError" in r.text and "with an error" in r.text and r.data["failed"]
    c3 = (
        await tb.call(
            "insert_cell",
            {"source": "import matplotlib\nimport matplotlib.pyplot as plt\nplt.plot([1,2],[3,4])\nplt.show()"},
        )
    ).data["id"]
    r = await tb.call("run_cell", {"id": c3})
    assert len(r.images) == 1 and r.images[0][0] == "image/png"
    assert any("image/png" in o.get("data", {}) for o in schema.get_outputs(doc, c3))
    events = tb.stream.events
    assert [e["summary"] for e in events if e["name"] == "run_cell"] == [f"ran {c1} ✓", f"ran {c2} ✗", f"ran {c3} ✓"]


async def test_run_cell_timeout_interrupts_and_kernel_survives(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    tb = make_toolbox(store, sched, path, doc, ag)
    slow = (await tb.call("insert_cell", {"source": "import time; time.sleep(60)"})).data["id"]
    t0 = time.monotonic()
    r = await tb.call("run_cell", {"id": slow, "timeout": 1})
    assert time.monotonic() - t0 < 20
    assert "timed out" in r.text and "KeyboardInterrupt" in r.text
    ok = (await tb.call("insert_cell", {"source": "'alive'"})).data["id"]
    assert "alive" in (await tb.call("run_cell", {"id": ok})).text


async def test_run_cell_rejects_agent_cells(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    other = add_agent_cell(doc, "other agent", after=ag)
    tb = make_toolbox(store, sched, path, doc, ag)
    with pytest.raises(ToolError, match="deadlock"):
        await tb.call("run_cell", {"id": ag})
    with pytest.raises(ToolError, match="agent cell"):
        await tb.call("run_cell", {"id": other})
    with pytest.raises(ToolError, match="no such cell"):
        await tb.call("run_cell", {"id": "nope"})
    # nothing was queued
    assert schema.cell_snapshot(doc, other)["exec"]["state"] == "idle"
    md = (await tb.call("insert_cell", {"source": "hi", "kind": "markdown"})).data["id"]
    assert "markdown" in (await tb.call("run_cell", {"id": md})).text


async def test_delete_cell_only_own_unless_policy(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    human = schema.insert_cell(doc, ag, "code", "python3", "mine", "me")
    older = schema.insert_cell(doc, ag, "code", "python3", "prev run", "claude", created_by_run="oldrun")
    tb = make_toolbox(store, sched, path, doc, ag)
    mine = (await tb.call("insert_cell", {"source": "tmp"})).data["id"]
    with pytest.raises(ToolError, match="not created by this run"):
        await tb.call("delete_cell", {"id": human})
    with pytest.raises(ToolError, match="not created by this run"):
        await tb.call("delete_cell", {"id": older})
    with pytest.raises(ToolError, match="agent cell"):
        await tb.call("delete_cell", {"id": ag})
    await tb.call("delete_cell", {"id": mine})
    assert not schema.has_cell(doc, mine) and tb.anchor == ag
    schema.meta(doc)["agent_delete"] = "any"
    await tb.call("delete_cell", {"id": human})
    assert not schema.has_cell(doc, human)


async def test_set_kernel_read_cell_list_kernels(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    c = schema.insert_cell(doc, ag, "code", "python3", "1+1", "me")
    schema.set_outputs(doc, c, [{"output_type": "stream", "name": "stdout", "text": "out!\n"}])
    tb = make_toolbox(store, sched, path, doc, ag)
    r = await tb.call("read_cell", {"id": c})
    assert "1+1" in r.text and "out!" in r.text
    assert "1+1" in (await tb.call("read_cell", {"id": c, "include_outputs": False})).text
    await tb.call("set_kernel", {"id": c, "kernel": "py"})
    assert schema.cell_snapshot(doc, c)["kernel"] == "python3"
    with pytest.raises(ToolError):
        await tb.call("set_kernel", {"id": c, "kernel": "claude"})
    with pytest.raises(ToolError):
        await tb.call("set_kernel", {"id": ag, "kernel": "py"})
    ks = await tb.call("list_kernels", {})
    assert "py -> python3" in ks.text and "python3" in ks.text
    assert "claude" not in ks.text.split("installed")[0]


async def test_propose_policy_marks_instead_of_running(nb):
    store, sched, path, doc = nb
    ag = add_agent_cell(doc)
    schema.meta(doc)["agent_policy"] = "propose"
    tb = make_toolbox(store, sched, path, doc, ag)
    c = (await tb.call("insert_cell", {"source": "print('should not run')"})).data["id"]
    r = await tb.call("run_cell", {"id": c})
    assert "queued for human approval" in r.text
    snap = schema.cell_snapshot(doc, c)
    assert snap["exec"]["state"] == "idle" and snap["outputs"] == []
    assert schema.get_cell(doc, c)["proposed"] is True

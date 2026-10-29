import asyncio

import pytest

from yupyter.doc import schema
from yupyter.doc.store import DocStore
from yupyter.scheduler import RunScheduler


@pytest.fixture
async def sched(tmp_path):
    store = DocStore()
    s = RunScheduler(store)
    yield store, s, tmp_path / "nb.md"
    await s.shutdown()


async def test_two_plus_two(sched):
    store, s, path = sched
    doc = store.get_doc(path)
    cid = schema.insert_cell(doc, None, "code", "python3", "2 + 2", "me")
    outs = await s.run_cell(path, cid)
    assert outs[0]["output_type"] == "execute_result"
    assert outs[0]["data"]["text/plain"] == "4"
    snap = schema.ordered_cells(doc)[0]
    assert snap["outputs"][0]["data"]["text/plain"] == "4"
    assert snap["exec"]["state"] == "done" and snap["exec"]["count"] == 1
    assert schema.kernels(doc)["python3"]["status"] == "idle"


async def test_stream_error_and_queue_order(sched):
    store, s, path = sched
    doc = store.get_doc(path)
    a = schema.insert_cell(doc, None, "code", "python3", "import time; print('a'); time.sleep(0.3); print('b')", "me")
    b = schema.insert_cell(doc, a, "code", "python3", "1/0", "me")
    ra = asyncio.create_task(s.run_cell(path, a))
    rb = asyncio.create_task(s.run_cell(path, b))
    await asyncio.sleep(0.05)
    outs_a, outs_b = await asyncio.gather(ra, rb)
    assert outs_a[0]["text"] == "a\nb\n"
    assert outs_b[0]["ename"] == "ZeroDivisionError"
    cells = {c["id"]: c for c in schema.ordered_cells(doc)}
    assert cells[b]["exec"]["state"] == "error"
    assert cells[b]["exec"]["count"] == 2


async def test_interrupt(sched):
    store, s, path = sched
    doc = store.get_doc(path)
    a = schema.insert_cell(doc, None, "code", "python3", "import time; time.sleep(30)", "me")
    t = asyncio.create_task(s.run_cell(path, a))
    await asyncio.sleep(2)
    await s.interrupt("python3")
    outs = await asyncio.wait_for(t, 10)
    assert outs[-1]["ename"] == "KeyboardInterrupt"

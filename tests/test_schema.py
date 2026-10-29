from yupyter.doc import schema
from yupyter.doc.order import between


def test_between_orders():
    keys = []
    for _ in range(50):
        keys.append(between(keys[-1] if keys else None, None))
    assert keys == sorted(keys)
    a, b = "V", "W"
    for _ in range(30):
        m = between(a, b)
        assert a < m < b
        b = m
    assert between(None, "V") < "V"


def test_insert_order_and_edit():
    doc = schema.new_doc()
    a = schema.insert_cell(doc, None, "code", "python3", "a = 1", "u1")
    c = schema.insert_cell(doc, a, "code", "python3", "c", "u1")
    b = schema.insert_cell(doc, a, "markdown", "python3", "# b", "u2")
    top = schema.insert_cell(doc, None, "code", "python3", "top", "u1", before_id=a)
    assert schema.ordered_ids(doc) == [top, a, b, c]
    schema.edit_source(doc, a, "a = 1", "a = 2")
    assert schema.get_source(doc, a) == "a = 2"
    schema.set_source(doc, a, "zzz")
    assert schema.get_source(doc, a) == "zzz"
    schema.delete_cell(doc, b)
    assert schema.ordered_ids(doc) == [top, a, c]


def test_outputs_and_exec():
    doc = schema.new_doc()
    a = schema.insert_cell(doc, None, "code", "python3", "print(1)", "u")
    schema.append_output(doc, a, {"output_type": "stream", "name": "stdout", "text": "1\n"})
    schema.append_output(doc, a, {"output_type": "stream", "name": "stdout", "text": "2\n"})
    schema.append_output(doc, a, {"output_type": "display_data", "data": {"text/plain": "x"}, "metadata": {}})
    outs = schema.get_outputs(doc, a)
    assert len(outs) == 2 and outs[0]["text"] == "1\n2\n"
    schema.set_exec_state(doc, a, "done", count=3)
    snap = schema.ordered_cells(doc)[0]
    assert snap["exec"]["state"] == "done" and snap["exec"]["count"] == 3
    schema.clear_outputs(doc, a)
    assert schema.get_outputs(doc, a) == []
    schema.set_kernel_status(doc, "python3", "idle")
    assert schema.kernels(doc)["python3"]["status"] == "idle"


def test_sync_between_docs():
    from pycrdt import Doc
    d1 = schema.new_doc()
    cid = schema.insert_cell(d1, None, "code", "python3", "hi", "u")
    d2 = Doc()
    d2.apply_update(d1.get_update())
    assert str(schema.cells(d2)[cid]["source"]) == "hi"

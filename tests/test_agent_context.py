from yupyter.agent import context as C
from yupyter.doc import schema

from agent_support import PNG_B64


def _doc():
    doc = schema.new_doc()
    a = schema.insert_cell(doc, None, "code", "python3", "x = 1\nprint(x)", "me")
    schema.set_outputs(doc, a, [{"output_type": "stream", "name": "stdout", "text": "1\n"}])
    return doc, a


def test_renders_above_below_and_task():
    doc, a = _doc()
    md = schema.insert_cell(doc, a, "markdown", "markdown", "# Title\nhello", "me")
    ag = schema.insert_cell(doc, md, "code", "agent:claude", "plot it\nmore detail", "me")
    schema.set_outputs(doc, ag, [{"output_type": "stream", "name": "stdout", "text": "OLD AGENT OUTPUT"}])
    b = schema.insert_cell(doc, ag, "code", "python3", "\n  y = secret_below()\nz = 2", "me")
    text = C.render_context(doc, ag).text()
    assert f"`{a}`" in text and "kernel `python3`" in text
    assert "x = 1\nprint(x)" in text and "\n1\n" in text
    assert "# Title" in text
    # below: id + kernel + first non-empty line only
    assert f"- `{b}` (python3): y = secret_below()" in text
    assert "z = 2" not in text
    # the task comes last and is the agent cell's prompt; its stale output is not shown
    assert text.rstrip().endswith("```")
    assert text.index("# Your task") > text.index("# Title")
    assert "plot it\nmore detail" in text.split("# Your task")[1]
    assert "OLD AGENT OUTPUT" not in text
    assert f"agent cell `{ag}`" in text


def test_truncates_long_output_keeps_head_and_tail():
    doc, a = _doc()
    big = "HEAD" + "x" * 50000 + "TAIL"
    schema.set_outputs(doc, a, [{"output_type": "stream", "name": "stdout", "text": big}])
    ag = schema.insert_cell(doc, a, "code", "agent:claude", "go", "me")
    text = C.render_context(doc, ag, limits=C.Limits(max_output_chars=500)).text()
    assert "HEAD" in text and "TAIL" in text and "chars truncated" in text
    assert len(text) < 2500


def test_errors_kept_ansi_stripped_and_tail_of_traceback():
    doc, a = _doc()
    tb = [f"\x1b[31mline{i}\x1b[0m" for i in range(40)]
    schema.set_outputs(
        doc, a, [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": tb}]
    )
    ag = schema.insert_cell(doc, a, "code", "agent:claude", "fix", "me")
    text = C.render_context(doc, ag).text()
    assert "ZeroDivisionError: division by zero" in text
    assert "line39" in text and "line0\n" not in text
    assert "\x1b" not in text


def test_images_as_blocks_newest_win_and_placeholder_fallback():
    doc, a = _doc()
    cells = [a]
    for i in range(3):
        c = schema.insert_cell(doc, cells[-1], "code", "python3", f"plot{i}", "me")
        schema.set_outputs(
            doc, c, [{"output_type": "display_data", "data": {"image/png": PNG_B64, "text/plain": "<Figure>"}, "metadata": {}}]
        )
        cells.append(c)
    ag = schema.insert_cell(doc, cells[-1], "code", "agent:claude", "go", "me")
    r = C.render_context(doc, ag, limits=C.Limits(max_images=2))
    imgs = [b for b in r.blocks if b["type"] == "image"]
    assert len(imgs) == 2 and r.has_images
    assert imgs[0]["source"] == {"type": "base64", "media_type": "image/png", "data": PNG_B64}
    assert r.text().count("[image output]") == 3  # 2 blocks + 1 over budget
    # text-only rendering
    r2 = C.render_context(doc, ag, images=False)
    assert not r2.has_images and r2.text().count("[image output]") == 3
    assert "<Figure>" not in r2.text()


def test_earlier_agent_cell_output_shows_answer_not_thinking():
    doc, a = _doc()
    prev = schema.insert_cell(doc, a, "code", "agent:claude", "first ask", "me")
    events = [
        {"type": "thinking", "text": "SECRET THOUGHTS", "done": True},
        {"type": "tool", "name": "insert_cell", "summary": "inserted cell c1 (py)", "status": "ok"},
        {"type": "text", "text": "All done."},
    ]
    schema.set_outputs(
        doc, prev, [{"output_type": "display_data", "data": {C.AGENT_MIME: events, "text/plain": "x"}, "metadata": {}}]
    )
    ag = schema.insert_cell(doc, prev, "code", "agent:claude", "second ask", "me")
    text = C.render_context(doc, ag).text()
    assert "SECRET THOUGHTS" not in text
    assert "All done." in text and "inserted cell c1 (py)" in text


def test_source_with_backticks_gets_longer_fence():
    doc = schema.new_doc()
    a = schema.insert_cell(doc, None, "code", "python3", "s = '''\n```\n'''", "me")
    ag = schema.insert_cell(doc, a, "code", "agent:claude", "go", "me")
    text = C.render_context(doc, ag).text()
    assert "````python\ns = '''\n```\n'''\n````" in text


def test_unknown_agent_cell_raises():
    import pytest

    with pytest.raises(KeyError):
        C.render_context(schema.new_doc(), "nope")

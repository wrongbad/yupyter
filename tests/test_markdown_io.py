import base64

import pytest

from yupyter.doc import schema
from yupyter.doc.markdown_io import dump_markdown, load_markdown
from yupyter.doc.store import DocStore

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


def roundtrip(text, tmp_path, name="nb"):
    assets = tmp_path / f"{name}.assets"
    d1 = load_markdown(text, assets)
    t1 = dump_markdown(d1, assets)
    d2 = load_markdown(t1, assets)
    t2 = dump_markdown(d2, assets)
    return d1, t1, d2, t2


def test_plain_markdown_import(tmp_path):
    text = "# Title\n\nSome prose.\n\n```python\nprint(1)\n```\n\nMore prose\n\n```\nx = 2\n```\n"
    d1, t1, d2, t2 = roundtrip(text, tmp_path)
    cs = schema.ordered_cells(d1)
    assert [c["kind"] for c in cs] == ["markdown", "code", "markdown", "code"]
    assert cs[0]["source"] == "# Title\n\nSome prose."
    assert cs[1]["kernel"] == "python3" and cs[3]["kernel"] == "python3"
    assert cs[1]["id"] and cs[1]["id"] in t1  # ids written on save
    assert t1 == t2
    assert [c["id"] for c in schema.ordered_cells(d2) if c["kind"] == "code"] == [
        c["id"] for c in cs if c["kind"] == "code"
    ]


def test_ids_authors_alias_and_reverse_map(tmp_path):
    text = (
        "---\nyupyter: 1\naliases:\n  r: ir\n  rr: ir\n---\n\n"
        "```py {#a3f9}\n2 + 2\n```\n\n"
        '```r {#b2 author="al ice" run=run:7}\nx <- 1\n```\n\n'
        "```claude {#c3}\nplease graph y=2+x\n```\n"
    )
    d1, t1, d2, t2 = roundtrip(text, tmp_path)
    cs = {c["id"]: c for c in schema.ordered_cells(d1)}
    assert cs["a3f9"]["kernel"] == "python3"
    assert cs["b2"]["kernel"] == "ir"
    assert cs["b2"]["author"] == "al ice" and cs["b2"]["created_by_run"] == "run:7"
    assert cs["c3"]["kernel"] == "agent:claude"
    assert schema.meta(d1).get("aliases") == {"r": "ir", "rr": "ir"}
    assert "```py {#a3f9}" in t1
    assert "```r {#b2 author=" in t1  # shortest alias; quoted author
    assert "```claude {#c3}" in t1
    assert t1 == t2
    assert "aliases:\n  r: ir" in t1


def test_unknown_kernel_falls_back_to_name(tmp_path):
    d = load_markdown("```julia {#j1}\n1\n```\n")
    assert schema.ordered_cells(d)[0]["kernel"] == "julia"
    assert "```julia {#j1}" in dump_markdown(d, tmp_path / "a.assets")


def test_nested_fences(tmp_path):
    src = 'print("""\n```\ninner\n```\n""")'
    d = schema.new_doc()
    schema.insert_cell(d, None, "code", "python3", src, None, cell_id="n1")
    t = dump_markdown(d, tmp_path / "a.assets")
    assert "````py {#n1}" in t
    d2 = load_markdown(t)
    assert schema.get_source(d2, "n1") == src
    assert dump_markdown(d2, tmp_path / "a.assets") == t


def test_source_edge_cases(tmp_path):
    cases = [("e1", ""), ("e2", "a\n"), ("e3", "\n"), ("e4", "  x  ")]
    d = schema.new_doc()
    for cid, src in cases:
        schema.insert_cell(d, None, "code", "python3", src, None, cell_id=cid)
    d2 = load_markdown(dump_markdown(d, tmp_path / "a.assets"))
    for cid, src in cases:
        assert schema.get_source(d2, cid) == src


def test_outputs_errors_images_roundtrip(tmp_path):
    assets = tmp_path / "nb.assets"
    d = schema.new_doc()
    schema.insert_cell(d, None, "code", "python3", "stuff", "bob", cell_id="a1")
    schema.append_output(d, "a1", {"output_type": "stream", "name": "stdout", "text": "hi\nthere\n"})
    schema.append_output(d, "a1", {"output_type": "stream", "name": "stderr", "text": "warn\n"})
    schema.append_output(
        d, "a1", {"output_type": "execute_result", "data": {"text/plain": "4"}, "metadata": {}, "execution_count": 1}
    )
    schema.append_output(
        d, "a1", {"output_type": "display_data", "data": {"image/png": PNG, "text/plain": "<Fig>"}, "metadata": {}}
    )
    schema.append_output(
        d, "a1", {"output_type": "display_data", "data": {"text/html": "<b>x</b>"}, "metadata": {}}
    )
    schema.append_output(
        d,
        "a1",
        {
            "output_type": "error",
            "ename": "ZeroDivisionError",
            "evalue": "division by zero",
            "traceback": ["\x1b[31mTraceback\x1b[0m", "ZeroDivisionError: division by zero"],
        },
    )
    schema.insert_cell(d, None, "markdown", "python3", "# hello", None)
    schema.insert_cell(d, None, "code", "agent:claude", "do it", "alice", cell_id="ag1")
    schema.append_output(d, "ag1", {"output_type": "stream", "name": "stdout", "text": "done.\n"})

    t = dump_markdown(d, assets)
    assert "\x1b" not in t
    assert (assets / "a1-3.png").read_bytes().startswith(b"\x89PNG")
    assert (assets / "a1-4.html").read_text() == "<b>x</b>"
    assert "<!-- yupyter:output for=a1 mime=image/png -->\n![](nb.assets/a1-3.png)" in t
    assert "```error {for=a1}" in t and "```claude {#ag1 author=alice}" in t

    d2 = load_markdown(t, assets)
    outs = schema.get_outputs(d2, "a1")
    assert outs[0] == {"output_type": "stream", "name": "stdout", "text": "hi\nthere\n"}
    assert outs[1]["name"] == "stderr"
    assert outs[2]["output_type"] == "execute_result" and outs[2]["data"] == {"text/plain": "4"}
    assert outs[3]["data"] == {"image/png": PNG}
    assert outs[4]["data"] == {"text/html": "<b>x</b>"}
    assert outs[5]["output_type"] == "error"
    assert outs[5]["ename"] == "ZeroDivisionError" and outs[5]["evalue"] == "division by zero"
    cells = schema.ordered_cells(d2)
    assert [c["kind"] for c in cells] == ["code", "markdown", "code"]  # no stray md cells
    assert schema.get_outputs(d2, "ag1")[0]["text"] == "done.\n"
    assert dump_markdown(d2, assets) == t


def test_load_without_assets_skips_rich():
    text = "```py {#a}\n1\n```\n\n<!-- yupyter:output for=a mime=image/png -->\n![](x.assets/a-0.png)\n"
    d = load_markdown(text)
    assert schema.get_outputs(d, "a") == []
    assert [c["kind"] for c in schema.ordered_cells(d)] == ["code"]


def test_duplicate_ids_reassigned():
    d = load_markdown("```py {#x}\n1\n```\n\n```py {#x}\n2\n```\n")
    ids = [c["id"] for c in schema.ordered_cells(d)]
    assert len(set(ids)) == 2 and "x" in ids


def test_store_save_load_atomic_and_stale_assets(tmp_path):
    p = (tmp_path / "nb.md").resolve()
    store = DocStore()
    od = store.open(p)
    schema.insert_cell(od.doc, None, "code", "python3", "1", None, cell_id="q1")
    schema.append_output(
        od.doc, "q1", {"output_type": "display_data", "data": {"image/png": PNG}, "metadata": {}}
    )
    store.save(p)
    assert (tmp_path / "nb.assets" / "q1-0.png").exists()
    assert "```py {#q1}" in p.read_text()
    schema.clear_outputs(od.doc, "q1")
    store.save(p)
    assert not (tmp_path / "nb.assets").exists()
    assert [f.name for f in tmp_path.iterdir()] == ["nb.md"]

    d = DocStore().open(p).doc
    assert schema.get_source(d, "q1") == "1"

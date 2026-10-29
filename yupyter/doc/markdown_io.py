"""Markdown <-> Doc conversion (plans/poc.md section 4).

File format::

    ---
    yupyter: 1
    aliases:
      r: ir
    ---

    # Prose becomes a markdown cell

    ```py {#a3f9}
    2 + 2
    ```

    ```output {for=a3f9}
    4
    ```

    <!-- yupyter:output for=a3f9 mime=image/png -->
    ![](analysis.assets/a3f9-1.png)

* Fence info string: first word is a kernel alias, ``{#id key=value ...}``
  carries the cell id (and ``author`` / ``run`` when set).
* Each maximal run of non-fence text is one markdown cell.
* ``output`` / ``error`` fences attach to the cell named by ``for=``.
* Rich outputs are files in the assets dir, referenced from a marker comment
  plus a link line; the pair is consumed on load (never a markdown cell).
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from pycrdt import Doc

from .. import config
from . import schema

DEFAULT_KERNEL = "python3"
_PY_NAMES = {"python", "python3", "ipython"}

_FENCE_RE = re.compile(r"^(`{3,})([^`]*)$")
_MARKER_RE = re.compile(r"^<!--\s*yupyter:output\s+(.*?)\s*-->\s*$")
_LINK_RE = re.compile(r"\]\(([^)]*)\)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/+@-]+$")
_ID_RE = re.compile(r"^[\w-]+$")

# mime -> (file extension, binary?) in order of preference
_RICH = [
    ("image/png", "png", True),
    ("image/jpeg", "jpg", True),
    ("image/svg+xml", "svg", False),
    ("text/html", "html", False),
    ("text/markdown", "md", False),
    ("application/json", "json", False),
]
_RICH_BY_MIME = {m: (ext, binary) for m, ext, binary in _RICH}


# ---------------------------------------------------------------------------
# small helpers


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _as_text(v: Any) -> str:
    if isinstance(v, list):
        return "".join(str(x) for x in v)
    return "" if v is None else str(v)


def _quote_value(v: str) -> str:
    return v if v and _SAFE_VALUE_RE.match(v) else json.dumps(v)


def _tokenize(s: str) -> list[str]:
    """Split on whitespace, honouring double-quoted values (key="a b")."""
    toks: list[str] = []
    cur: list[str] = []
    i, n, has = 0, len(s), False
    while i < n:
        ch = s[i]
        if ch == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            raw = s[i : j + 1]
            try:
                cur.append(json.loads(raw))
            except ValueError:
                cur.append(raw.strip('"'))
            has = True
            i = j + 1
        elif ch.isspace():
            if has or cur:
                toks.append("".join(cur))
            cur, has = [], False
            i += 1
        else:
            cur.append(ch)
            has = True
            i += 1
    if has or cur:
        toks.append("".join(cur))
    return toks


def _parse_info(info: str) -> tuple[str, dict[str, str]]:
    """``'py {#a3f9 author=x}'`` -> ``('py', {'id': 'a3f9', 'author': 'x'})``."""
    info = info.strip()
    first = ""
    if info and not info.startswith("{"):
        first, _, info = info.partition(" ")
        if "{" in first:  # e.g. ``py{#id}``
            first, brace, rest = first.partition("{")
            info = brace + rest + " " + info
    attrs: dict[str, str] = {}
    for tok in _tokenize(info.replace("{", " ").replace("}", " ")):
        if tok.startswith("#") and len(tok) > 1:
            attrs["id"] = tok[1:]
        elif "=" in tok:
            k, _, v = tok.partition("=")
            attrs[k] = v
    return first, attrs


def _fence_for(*bodies: str) -> str:
    longest = 0
    for b in bodies:
        for m in re.finditer(r"`+", b):
            longest = max(longest, len(m.group()))
    return "`" * max(3, longest + 1)


# ---------------------------------------------------------------------------
# front matter


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].rstrip() in ("---", "..."):
            fm = _parse_front_matter(lines[1:end])
            if fm is None:
                return {}, text
            return fm, "\n".join(lines[end + 1 :])
    return {}, text


def _scalar(v: str) -> Any:
    v = v.strip()
    if v.startswith('"'):
        try:
            return json.loads(v)
        except ValueError:
            return v.strip('"')
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1].replace("''", "'")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _parse_front_matter(lines: list[str]) -> dict[str, Any] | None:
    """Parse the simple subset: ``key: value`` and one-level nested maps."""
    out: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[0] in " \t"
        key, sep, val = raw.strip().partition(":")
        if not sep:
            return None
        key, val = key.strip(), val.strip()
        if indented:
            if current is None:
                return None
            current[_scalar(key) if key.startswith('"') else key] = _scalar(val)
        elif val == "":
            current = {}
            out[key] = current
        else:
            current = None
            out[key] = _scalar(val)
    return out


def _fm_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if re.fullmatch(r"[A-Za-z0-9_./ -]+", s) and s == s.strip() and not re.fullmatch(r"-?\d+", s):
        return s
    return json.dumps(s)


def _meta_to_py(doc: Doc) -> dict[str, Any]:
    out: dict[str, Any] = {}
    m = schema.meta(doc)
    for k in list(m.keys()):
        v = m.get(k)
        if hasattr(v, "to_py"):
            v = v.to_py()
        out[str(k)] = v
    return out


def _doc_aliases(doc: Doc) -> dict[str, str]:
    a = _meta_to_py(doc).get("aliases")
    return {str(k): str(v) for k, v in a.items()} if isinstance(a, dict) else {}


# ---------------------------------------------------------------------------
# load


def _resolve_kernel(name: str, overrides: dict[str, str]) -> str:
    if not name:
        return DEFAULT_KERNEL
    resolved = config.resolve_alias(name, overrides)
    if resolved == name and name in _PY_NAMES:
        return DEFAULT_KERNEL
    return resolved


def _read_asset(assets_dir: Path | None, link: str, binary: bool) -> str | None:
    if assets_dir is None:
        return None
    p = Path(assets_dir) / Path(unquote(link.strip())).name
    try:
        if binary:
            return base64.b64encode(p.read_bytes()).decode("ascii")
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _error_output(body: str) -> dict[str, Any]:
    lines = body.split("\n")
    ename, evalue = "Error", ""
    for ln in reversed(lines):
        if ln.strip():
            m = re.match(r"^([A-Za-z_][\w.]*)\s*:\s*(.*)$", ln.strip())
            if m:
                ename, evalue = m.group(1), m.group(2)
            else:
                m = re.match(r"^([A-Za-z_][\w.]*)$", ln.strip())
                if m:
                    ename = m.group(1)
                else:
                    evalue = ln.strip()
            break
    return {"output_type": "error", "ename": ename, "evalue": evalue, "traceback": lines}


def load_markdown(text: str, assets_dir: Path | None = None) -> Doc:
    """Parse a notebook ``.md`` into a new Doc."""
    text = text.replace("\r\n", "\n")
    fm, body = _split_front_matter(text)
    overrides = {str(k): str(v) for k, v in (fm.get("aliases") or {}).items()} if isinstance(
        fm.get("aliases"), dict
    ) else {}

    doc = schema.new_doc()
    if fm:
        with doc.transaction(origin=schema.SERVER_ORIGIN):
            m = schema.meta(doc)
            for k, v in fm.items():
                m[k] = v
            if "yupyter" not in m:
                m["yupyter"] = 1

    # pass 1: tokenize into items
    items: list[dict[str, Any]] = []  # code / output / error / md / rich
    prose: list[str] = []

    def flush_prose() -> None:
        while prose and not prose[0].strip():
            prose.pop(0)
        while prose and not prose[-1].strip():
            prose.pop()
        if prose:
            items.append({"t": "md", "source": "\n".join(prose)})
        prose.clear()

    lines = body.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        fm_ = _FENCE_RE.match(line)
        if fm_:
            fence, info = fm_.group(1), fm_.group(2)
            j = i + 1
            while j < n:
                cl = lines[j].rstrip()
                if cl.startswith(fence) and set(cl) == {"`"}:
                    break
                j += 1
            content = "\n".join(lines[i + 1 : j])
            flush_prose()
            word, attrs = _parse_info(info)
            if word == "output":
                items.append({"t": "output", "attrs": attrs, "body": content})
            elif word == "error":
                items.append({"t": "error", "attrs": attrs, "body": content})
            else:
                items.append({"t": "code", "word": word, "attrs": attrs, "source": content})
            i = j + 1
            continue
        mk = _MARKER_RE.match(line.strip())
        if mk:
            flush_prose()
            attrs = dict(t.split("=", 1) for t in _tokenize(mk.group(1)) if "=" in t)
            link = ""
            k = i + 1
            while k < n and not lines[k].strip():
                k += 1
            if k < n:
                lm = _LINK_RE.search(lines[k])
                if lm:
                    link = lm.group(1)
                    i = k
            items.append({"t": "rich", "attrs": attrs, "link": link})
            i += 1
            continue
        prose.append(line)
        i += 1
    flush_prose()

    # pass 2: create cells (assign ids)
    used: set[str] = set()
    for it in items:
        if it["t"] == "code":
            cid = it["attrs"].get("id", "")
            if not _ID_RE.match(cid) or cid in used:
                cid = ""
            if cid:
                used.add(cid)
            it["id"] = cid
    for it in items:
        if it["t"] == "code" and not it["id"]:
            while True:
                cid = schema.new_cell_id()
                if cid not in used:
                    break
            used.add(cid)
            it["id"] = cid

    for it in items:
        if it["t"] == "md":
            schema.insert_cell(
                doc, None, "markdown", DEFAULT_KERNEL, it["source"], None,
                origin=schema.SERVER_ORIGIN,
            )
        elif it["t"] == "code":
            a = it["attrs"]
            schema.insert_cell(
                doc, None, "code", _resolve_kernel(it["word"], overrides), it["source"],
                a.get("author") or None, a.get("run") or None,
                origin=schema.SERVER_ORIGIN, cell_id=it["id"],
            )
        else:
            target = it["attrs"].get("for", "")
            if target not in used:
                continue
            out = _build_output(it, assets_dir)
            if out is not None:
                schema.append_output(doc, target, out, origin=schema.SERVER_ORIGIN)
    return doc


def _build_output(it: dict[str, Any], assets_dir: Path | None) -> dict[str, Any] | None:
    attrs = it["attrs"]
    if it["t"] == "error":
        return _error_output(it["body"])
    if it["t"] == "output":
        body = it["body"]
        typ = attrs.get("type", "")
        if typ in ("result", "display"):
            return {
                "output_type": "execute_result" if typ == "result" else "display_data",
                "data": {"text/plain": body},
                "metadata": {},
                **({"execution_count": None} if typ == "result" else {}),
            }
        name = "stderr" if attrs.get("stream") == "stderr" else "stdout"
        return {"output_type": "stream", "name": name, "text": body + "\n"}
    # rich
    mime = attrs.get("mime", "")
    spec = _RICH_BY_MIME.get(mime)
    if spec is None or not it["link"]:
        return None
    data = _read_asset(assets_dir, it["link"], spec[1])
    if data is None:
        return None
    return {"output_type": "display_data", "data": {mime: data}, "metadata": {}}


# ---------------------------------------------------------------------------
# dump


def _reverse_alias(kernel: str, table: dict[str, str]) -> str:
    cands = sorted((a for a, v in table.items() if v == kernel), key=lambda a: (len(a), a))
    return cands[0] if cands else kernel


def _dump_front_matter(doc: Doc) -> str:
    m = _meta_to_py(doc)
    m.setdefault("yupyter", 1)
    out = ["---", f"yupyter: {_fm_value(m.pop('yupyter'))}"]
    aliases = m.pop("aliases", None)
    for k in sorted(m):
        v = m[k]
        if isinstance(v, (str, int, bool)) and re.fullmatch(r"[A-Za-z_][\w-]*", k):
            out.append(f"{k}: {_fm_value(v)}")
    if isinstance(aliases, dict) and aliases:
        out.append("aliases:")
        for k in sorted(aliases):
            out.append(f"  {_quote_value(str(k))}: {_fm_value(aliases[k])}")
    out.append("---")
    return "\n".join(out)


def _best_rich(data: dict[str, Any]) -> tuple[str, str, bool] | None:
    for mime, ext, binary in _RICH:
        if mime in data and data[mime] not in (None, ""):
            return mime, ext, binary
    return None


def _out_block(info: str, body: str) -> str:
    fence = _fence_for(body)
    return f"{fence}{info}\n" + (body + "\n" if body else "") + fence


def dump_markdown(doc: Doc, assets_dir: Path) -> str:
    """Serialise ``doc`` to markdown; rich outputs go in ``assets_dir``.

    Files are written into ``assets_dir`` as needed (the directory is created
    lazily); removing stale files is the caller's job (see ``DocStore.save``).
    """
    assets_dir = Path(assets_dir)
    table = {**config.aliases(), **_doc_aliases(doc)}
    blocks: list[str] = [_dump_front_matter(doc)]

    for cell in schema.ordered_cells(doc):
        cid = cell["id"]
        if cell["kind"] == "markdown":
            src = cell["source"].strip("\n")
            if src.strip():
                blocks.append(src)
            continue
        attrs = [f"#{cid}"]
        if cell["author"]:
            attrs.append(f"author={_quote_value(str(cell['author']))}")
        if cell["created_by_run"]:
            attrs.append(f"run={_quote_value(str(cell['created_by_run']))}")
        word = _reverse_alias(cell["kernel"] or DEFAULT_KERNEL, table)
        src = cell["source"]
        fence = _fence_for(src)
        blocks.append(
            f"{fence}{word} {{{' '.join(attrs)}}}\n" + (src + "\n" if src else "") + fence
        )
        for idx, o in enumerate(cell["outputs"]):
            b = _dump_output(cid, idx, o, assets_dir)
            if b:
                blocks.append(b)
    return "\n\n".join(blocks) + "\n"


def _dump_output(cid: str, idx: int, o: dict[str, Any], assets_dir: Path) -> str | None:
    typ = o.get("output_type")
    if typ == "stream":
        text = _as_text(o.get("text")).rstrip("\n")
        if not text.strip():
            return None
        extra = " stream=stderr" if o.get("name") == "stderr" else ""
        return _out_block(f"output {{for={cid}{extra}}}", text)
    if typ == "error":
        tb = o.get("traceback") or []
        body = _strip_ansi("\n".join(_as_text(x) for x in tb)) if tb else ""
        if not body.strip():
            body = f"{o.get('ename', 'Error')}: {o.get('evalue', '')}"
        return _out_block(f"error {{for={cid}}}", body.rstrip("\n"))
    if typ in ("execute_result", "display_data"):
        data = o.get("data") or {}
        rich = _best_rich(data)
        if rich is not None:
            mime, ext, binary = rich
            fname = f"{cid}-{idx}.{ext}"
            assets_dir.mkdir(parents=True, exist_ok=True)
            p = assets_dir / fname
            if binary:
                p.write_bytes(base64.b64decode(re.sub(r"\s+", "", _as_text(data[mime]))))
            elif mime == "application/json" and not isinstance(data[mime], (str, list)):
                p.write_text(json.dumps(data[mime], indent=2), encoding="utf-8")
            else:
                p.write_text(_as_text(data[mime]), encoding="utf-8")
            href = f"{quote(assets_dir.name)}/{quote(fname)}"
            link = f"![]({href})" if mime.startswith("image/") else f"[output]({href})"
            return f"<!-- yupyter:output for={cid} mime={mime} -->\n{link}"
        text = _as_text(data.get("text/plain")).rstrip("\n")
        if text.strip():
            t = "result" if typ == "execute_result" else "display"
            return _out_block(f"output {{for={cid} type={t}}}", text)
    return None

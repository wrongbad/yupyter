"""Render the notebook as the agent's prompt context (pure functions).

The document *is* the agent's memory: cells from the top through the agent
cell are rendered in full (source + outputs), cells below it as one line each,
and the agent cell's own text is the task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pycrdt import Doc

from ..doc import schema

AGENT_MIME = "application/vnd.yupyter.agent+json"
IMAGE_MIMES = ("image/png", "image/jpeg")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class Limits:
    max_output_chars: int = 3000  # per cell, all text outputs together
    max_traceback_lines: int = 12
    max_source_chars: int = 20000
    max_images: int = 4  # newest images win; older ones become placeholders
    max_image_b64: int = 1_500_000
    max_summary_chars: int = 100


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def truncate(s: str, limit: int) -> str:
    """Keep head and tail of ``s`` within roughly ``limit`` chars, marking the cut."""
    if len(s) <= limit:
        return s
    keep = max(limit - 40, 0)
    head = keep * 2 // 3
    tail = keep - head
    cut = len(s) - head - tail
    return f"{s[:head]}\n... [{cut} chars truncated] ...\n{s[len(s) - tail:]}"


def _fence(text: str, info: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{text}\n{ticks}"


def _join(v: Any) -> str:
    return "".join(v) if isinstance(v, list) else str(v if v is not None else "")


def first_line(source: str, n: int = 100) -> str:
    for line in source.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= n else line[: n - 1] + "…"
    return ""


def agent_events_text(value: Any) -> str:
    """Text for an earlier agent cell's output: answers and tool lines, no thinking."""
    events = value.get("events", []) if isinstance(value, dict) else (value or [])
    lines = []
    for e in events:
        if e.get("type") == "text":
            lines.append(e.get("text", ""))
        elif e.get("type") == "tool":
            lines.append(f"[tool] {e.get('summary') or e.get('name', '')}")
    return "\n".join(lines).strip()


def output_to_parts(out: dict[str, Any], lim: Limits) -> list[tuple[str, str]]:
    """One nbformat output -> [("text", s)] / [("image", "<mime>|<b64>")] parts."""
    t = out.get("output_type")
    if t == "stream":
        txt = strip_ansi(_join(out.get("text")))
        return [("text", txt if out.get("name", "stdout") == "stdout" else f"[stderr]\n{txt}")]
    if t == "error":
        tb = [strip_ansi(x) for x in (out.get("traceback") or [])]
        tb_s = "\n".join(tb[-lim.max_traceback_lines :])
        head = f"{out.get('ename', 'Error')}: {out.get('evalue', '')}"
        return [("text", f"[error] {head}\n{tb_s}".rstrip())]
    if t in ("display_data", "execute_result"):
        data = out.get("data") or {}
        if AGENT_MIME in data:
            return [("text", agent_events_text(data[AGENT_MIME]))]
        for m in IMAGE_MIMES:
            if m in data:
                return [("image", f"{m}|{_join(data[m]).strip()}")]
        if "image/svg+xml" in data:
            return [("text", "[svg image output]")]
        if "text/plain" in data:
            return [("text", strip_ansi(_join(data["text/plain"])))]
        if "text/html" in data:
            return [("text", "[html output]")]
    return []


def render_outputs(outputs: list[dict[str, Any]], lim: Limits) -> list[tuple[str, str]]:
    """Merge adjacent text parts, truncate to the per-cell budget; images stay in place."""
    parts: list[tuple[str, str]] = []
    for o in outputs:
        parts.extend(output_to_parts(o, lim))
    merged: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            merged.append(("text", "\n".join(buf)))
            buf.clear()

    for kind, val in parts:
        if kind == "text":
            if val.strip():
                buf.append(val.rstrip("\n"))
        else:
            flush()
            merged.append((kind, val))
    flush()
    texts = [v for k, v in merged if k == "text"]
    if sum(len(t) for t in texts) > lim.max_output_chars:
        per = max(lim.max_output_chars // max(len(texts), 1), 200)
        merged = [(k, truncate(v, per) if k == "text" else v) for k, v in merged]
    return merged


def cell_header(c: dict[str, Any]) -> str:
    bits = [f"`{c['id']}`", c.get("kind") or "code", f"kernel `{c.get('kernel')}`"]
    if c.get("author"):
        bits.append(f"by {c['author']}")
    return "## Cell " + " · ".join(bits)


@dataclass
class Rendered:
    blocks: list[dict[str, Any]]  # Anthropic content blocks: text and image

    @property
    def has_images(self) -> bool:
        return any(b["type"] == "image" for b in self.blocks)

    def text(self) -> str:
        return "".join(b["text"] if b["type"] == "text" else "[image output]\n" for b in self.blocks)


def render_context(
    doc: Doc,
    agent_cell_id: str,
    *,
    images: bool = True,
    limits: Limits | None = None,
    note: str = "",
) -> Rendered:
    """Render the doc through ``agent_cell_id`` plus the task, as content blocks."""
    lim = limits or Limits()
    snaps = schema.ordered_cells(doc)
    idx = next((i for i, c in enumerate(snaps) if c["id"] == agent_cell_id), None)
    if idx is None:
        raise KeyError(f"no such cell: {agent_cell_id}")
    above, agent, below = snaps[:idx], snaps[idx], snaps[idx + 1 :]
    rendered = [render_outputs(c["outputs"], lim) for c in above]

    # newest images win the image budget
    keep_imgs: set[tuple[int, int]] = set()
    if images and lim.max_images:
        locs = [
            (ci, pi)
            for ci, parts in enumerate(rendered)
            for pi, (k, v) in enumerate(parts)
            if k == "image" and len(v) <= lim.max_image_b64
        ]
        keep_imgs = set(locs[-lim.max_images :])

    blocks: list[dict[str, Any]] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append({"type": "text", "text": "".join(buf)})
            buf.clear()

    title = str(schema.meta(doc).get("title") or "")
    buf.append("# Notebook so far" + (f": {title}" if title else "") + "\n\n")
    if not above:
        buf.append("(no cells above the agent cell)\n\n")
    for ci, c in enumerate(above):
        buf.append(cell_header(c) + "\n\n")
        src = truncate(c["source"], lim.max_source_chars)
        if c.get("kind") == "markdown":
            buf.append(src + "\n\n")
        else:
            buf.append(_fence(src, "python" if "python" in str(c.get("kernel")) else "") + "\n\n")
        if rendered[ci]:
            buf.append("Output:\n")
        for pi, (k, v) in enumerate(rendered[ci]):
            if k == "text":
                buf.append(_fence(v) + "\n\n")
            elif (ci, pi) in keep_imgs:
                mime, b64 = v.split("|", 1)
                flush()
                blocks.append(
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
                )
            else:
                buf.append("[image output]\n\n")
    if below:
        buf.append("# Cells below the agent cell (summary only)\n\n")
        for c in below:
            buf.append(f"- `{c['id']}` ({c.get('kernel')}): {first_line(c['source'], lim.max_summary_chars)}\n")
        buf.append("\n")
    buf.append(f"# Your task\n\nThis is agent cell `{agent['id']}`. Its prompt:\n\n")
    buf.append(_fence(agent["source"].strip()) + "\n")
    if note:
        buf.append("\n" + note + "\n")
    flush()
    return Rendered(blocks)

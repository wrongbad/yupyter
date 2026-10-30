"""ClaudeAgentExecutor: an agent cell runs like a kernel cell.

One run = one fresh model session (no resume): the notebook up to the agent
cell is rendered as the prompt, the model drives ``Toolbox`` tools, and its
thinking / answer / tool calls stream into the cell as a single display output
(mime ``application/vnd.yupyter.agent+json``, a list of events).

The model layer is pluggable (``AgentBackend``): the real one uses the Claude
Agent SDK, the fake one replays a JSON script through the same tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from .. import config
from ..doc import markdown_io, schema, undo
from ..kernels.executor import Output, RunContext
from . import context as ctxmod
from .tools import AGENT_AUTHOR, TOOL_NAMES, TOOL_SPECS, ToolError, Toolbox

log = logging.getLogger("yupyter.agent")

AGENT_MIME = ctxmod.AGENT_MIME
AUTH_HINT = "Run `claude` once in a terminal to log in, or set ANTHROPIC_API_KEY, then run this cell again."
FLUSH_INTERVAL = 0.1  # seconds; output updates are throttled to ~10/s

SYSTEM_PROMPT = """\
You are Claude, working inside a yupyter notebook as a collaborator.

You are shown the notebook from the top down to the agent cell you were invoked
from: each cell with its id, kind, kernel, source and outputs. Cells below your
cell are listed by id and first line only. The final section is your task: the
prompt written in the agent cell.

Work by changing the notebook with your tools, not by pasting code into your
reply:
- insert_cell adds a cell (by default right after your previous insertion, so the
  notebook reads top to bottom). Use kernel aliases like "py"; see list_kernels.
- run_cell runs a code cell and returns its text outputs (plots come back as
  images). Always run code you write, read the result, and fix problems with
  edit_cell (a search/replace; never retype a whole cell).
- read_cell re-reads a cell; delete_cell removes a cell you created in this run;
  set_kernel changes a cell's kernel.
You cannot run or edit agent cells. Humans may be editing the notebook at the same
time; do not touch cells you were not asked about.

Keep cells small and focused, one idea per cell, with plain readable code. For
plots use matplotlib and let the figure display inline. When done, reply with a
short plain-text summary of what you did and found (a sentence or two; markdown
is fine). Do not repeat code that is already in the notebook."""


class AgentError(Exception):
    """A failure to show the user as-is (friendly message)."""


# ---------------------------------------------------------------------------
# streamed output
# ---------------------------------------------------------------------------


class AgentStream:
    """Accumulates agent events and mirrors them into one output of the cell."""

    def __init__(self, doc, cell_id: str, interval: float = FLUSH_INTERVAL) -> None:
        self.doc, self.cell_id, self.interval = doc, cell_id, interval
        self.events: list[dict[str, Any]] = []
        self._dirty = False
        self._index: int | None = None
        self._task: asyncio.Task | None = None
        self.writes = 0  # number of doc updates (tests assert the throttle)

    # -- event API ------------------------------------------------------------
    def _close_open(self, except_type: str | None = None) -> None:
        for e in self.events:
            if e["type"] == "thinking" and not e.get("done") and e["type"] != except_type:
                e["done"] = True

    def begin(self, kind: str) -> None:
        """Start a new event of ``kind`` ("thinking" | "text")."""
        self._close_open()
        ev: dict[str, Any] = {"type": kind, "text": ""}
        if kind == "thinking":
            ev["done"] = False
        self.events.append(ev)
        self.touch()

    def _append(self, kind: str, text: str) -> None:
        if not text:
            return
        if not self.events or self.events[-1]["type"] != kind or self.events[-1].get("done"):
            self.begin(kind)
        self.events[-1]["text"] += text
        self.touch()

    def thinking(self, text: str) -> None:
        self._append("thinking", text)

    def text(self, text: str) -> None:
        self._append("text", text)

    def tool_event(self, name: str, summary: str, status: str = "ok") -> dict[str, Any]:
        self._close_open()
        ev = {"type": "tool", "name": name, "summary": summary, "status": status}
        self.events.append(ev)
        self.touch()
        return ev

    def note(self, summary: str, status: str = "error") -> None:
        self.tool_event("agent", summary, status)

    def touch(self) -> None:
        self._dirty = True

    # -- doc mirroring --------------------------------------------------------
    def _fallback_text(self) -> str:
        return markdown_io.agent_text(self.events)

    def flush(self) -> None:
        if not self._dirty or not schema.has_cell(self.doc, self.cell_id):
            return
        self._dirty = False
        output: Output = {
            "output_type": "display_data",
            "data": {AGENT_MIME: json.loads(json.dumps(self.events)), "text/plain": self._fallback_text()},
            "metadata": {},
        }
        n = len(schema.get_outputs(self.doc, self.cell_id))
        if self._index is None or self._index >= n:
            schema.append_output(self.doc, self.cell_id, output)
            self._index = n
        else:
            schema.update_output(self.doc, self.cell_id, self._index, output)
        self.writes += 1

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            self.flush()

    def start(self) -> None:
        self._task = asyncio.create_task(self._flusher())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._close_open()
        self.touch()
        self.flush()


# ---------------------------------------------------------------------------
# provenance / re-run
# ---------------------------------------------------------------------------


def rerun_mode(doc, cell_id: str) -> str:
    cell = schema.get_cell(doc, cell_id)
    mode = cell.get("rerun") or schema.meta(doc).get("rerun") or "replace"
    return "keep" if mode == "keep" else "replace"


def previous_runs(doc, cell_id: str) -> list[str]:
    ex = schema.get_cell(doc, cell_id).get("exec")
    runs = ex.get("runs") if ex is not None else None
    return [str(r) for r in (runs or [])]


def prepare_rerun(doc, cell_id: str, run_id: str, origin: str) -> str | None:
    """Apply replace/keep for earlier runs of this agent cell and record ``run_id``.

    Returns the cell id new cells should be inserted after (None = after the
    agent cell).
    """
    prev = previous_runs(doc, cell_id)
    mode = rerun_mode(doc, cell_id)
    anchor = None
    if prev:
        mine = [
            cid for cid in schema.ordered_ids(doc)
            if schema.get_cell(doc, cid).get("created_by_run") in prev and cid != cell_id
        ]
        if mode == "replace":
            for cid in mine:
                schema.delete_cell(doc, cid, origin=origin)
        elif mine:
            anchor = mine[-1]  # regenerate below the previous run's cells
    runs = [run_id] if mode == "replace" else prev + [run_id]
    ex = schema.get_cell(doc, cell_id)["exec"]
    with doc.transaction(origin=schema.SERVER_ORIGIN):
        ex["runs"] = runs
        ex["undone"] = False
        ex["summary"] = None
    return anchor


def write_summary(doc, cell_id: str, tb: Toolbox, replaced: int = 0) -> None:
    """Record what the run changed (``exec.summary``) for the undo button. ``replaced`` = earlier-run cells removed."""
    if not schema.has_cell(doc, cell_id):
        return
    alive = lambda cid: schema.has_cell(doc, cid)  # noqa: E731
    summary = {
        "inserted": sum(1 for c in tb.created if alive(c)),
        "edited": sum(1 for c in tb.edited if c not in tb.created and alive(c)),
        "deleted": tb.deleted_old + replaced,
    }
    ex = schema.get_cell(doc, cell_id)["exec"]
    with doc.transaction(origin=schema.SERVER_ORIGIN):
        ex["summary"] = summary


# ---------------------------------------------------------------------------
# presence (optional, another module may not exist yet)
# ---------------------------------------------------------------------------


def _presence(ctx: RunContext, cell_id: str | None) -> None:
    try:
        from ..doc import presence  # type: ignore[attr-defined]

        fn = getattr(presence, "set_agent_presence", None)
        if fn is None:
            return
        fn(ctx.store.open(ctx.notebook_path), "claude", cell_id)
    except ImportError:
        return
    except Exception:
        log.debug("presence update failed", exc_info=True)


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------


@dataclass
class AgentRun:
    ctx: RunContext
    toolbox: Toolbox
    stream: AgentStream
    rendered: ctxmod.Rendered
    settings: dict[str, Any]


class AgentBackend(Protocol):
    async def run(self, run: AgentRun) -> None: ...


def _tool_content(res) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": res.text}]
    for mime, b64 in res.images:
        content.append({"type": "image", "data": b64, "mimeType": mime})
    return content


def friendly_error(exc: BaseException | str, stderr: str = "") -> str:
    """Turn SDK/CLI failures into a one-line hint for the cell."""
    text = f"{exc}\n{stderr}".lower()
    name = type(exc).__name__
    if name == "CLINotFoundError" or "claude code not found" in text:
        return (
            "The Claude Code CLI was not found. Install it (npm i -g @anthropic-ai/claude-code) "
            "or reinstall claude-agent-sdk. " + AUTH_HINT
        )
    if any(k in text for k in ("not logged in", "/login", "authentication", "api key", "401", "unauthorized", "oauth")):
        return "Claude is not signed in. " + AUTH_HINT
    msg = str(exc).strip() or name
    if stderr.strip():
        msg += "\n" + stderr.strip()[-500:]
    return msg


class SdkBackend:
    """Claude Agent SDK: a fresh ``query()`` per run, only yupyter's tools allowed."""

    SERVER = "yupyter"

    def __init__(self, query_fn=None) -> None:
        self._query_fn = query_fn  # injectable for tests

    def _build_options(self, run: AgentRun, stderr_lines: list[str]):
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

        toolbox = run.toolbox

        def make(name: str):
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    return {"content": _tool_content(await toolbox.call(name, args))}
                except ToolError as e:
                    return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # never crash the SDK bridge
                    log.exception("tool %s failed", name)
                    return {"content": [{"type": "text", "text": f"internal error: {e}"}], "is_error": True}

            return tool(name, TOOL_SPECS[name]["description"], TOOL_SPECS[name]["schema"])(handler)

        server = create_sdk_mcp_server(self.SERVER, tools=[make(n) for n in TOOL_NAMES])
        st = run.settings
        kw: dict[str, Any] = dict(
            system_prompt=SYSTEM_PROMPT,
            tools=[],  # no built-in Bash/Read/Write/...
            allowed_tools=[f"mcp__{self.SERVER}__{n}" for n in TOOL_NAMES],
            mcp_servers={self.SERVER: server},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            max_turns=int(st.get("max_turns", 40)),
            include_partial_messages=True,
            thinking={"type": "adaptive", "display": "summarized"},
            cwd=str(run.ctx.notebook_path.parent),
            stderr=lambda line: stderr_lines.append(line),
        )
        if st.get("model"):
            kw["model"] = st["model"]
        return ClaudeAgentOptions(**kw)

    async def run(self, run: AgentRun) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ThinkingBlock,
        )
        from claude_agent_sdk import query as sdk_query

        stderr_lines: list[str] = []
        options = self._build_options(run, stderr_lines)
        blocks = run.rendered.blocks
        if run.rendered.has_images:
            async def prompt():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": blocks},
                    "parent_tool_use_id": None,
                    "session_id": "",
                }
        else:
            prompt = run.rendered.text()
        qfn = self._query_fn or sdk_query
        stream = run.stream
        streamed = False  # once partial events flow, AssistantMessage blocks are duplicates
        gen = qfn(prompt=prompt() if callable(prompt) else prompt, options=options)
        try:
            async for msg in gen:
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    et = ev.get("type")
                    if msg.parent_tool_use_id:
                        continue
                    streamed = True
                    if et == "content_block_start":
                        kind = (ev.get("content_block") or {}).get("type")
                        if kind in ("thinking", "text"):
                            stream.begin(kind)
                    elif et == "content_block_delta":
                        d = ev.get("delta") or {}
                        if d.get("type") == "thinking_delta":
                            stream.thinking(d.get("thinking", ""))
                        elif d.get("type") == "text_delta":
                            stream.text(d.get("text", ""))
                elif isinstance(msg, AssistantMessage):
                    if msg.error:
                        txt = " ".join(b.text for b in msg.content if isinstance(b, TextBlock))
                        raise AgentError(friendly_error(f"{msg.error}: {txt}", "\n".join(stderr_lines)))
                    if not streamed:
                        for b in msg.content:
                            if isinstance(b, ThinkingBlock):
                                stream.begin("thinking")
                                stream.thinking(b.thinking)
                            elif isinstance(b, TextBlock):
                                stream.begin("text")
                                stream.text(b.text)
                elif isinstance(msg, ResultMessage):
                    if msg.subtype == "error_max_turns":
                        stream.note("stopped: turn limit reached")
                        raise AgentError("The agent hit its turn limit before finishing.")
                    if msg.is_error:
                        raise AgentError(friendly_error(msg.result or msg.subtype, "\n".join(stderr_lines)))
        except AgentError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise AgentError(friendly_error(e, "\n".join(stderr_lines))) from e
        finally:
            aclose = getattr(gen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except BaseException:
                    pass


class FakeBackend:
    """Replays a JSON script through the real tool layer (tests / demos).

    ``YUPYTER_AGENT_BACKEND=fake:/path/script.json`` where the file is a list
    (or ``{"steps": [...]}``) of steps:

    - ``{"thinking": "..."}`` / ``{"text": "..."}``  streamed in small chunks
    - ``{"tool": "insert_cell", "args": {...}, "save_as": "c"}``  later steps can use
      ``"$c"`` (also ``$agent`` = the agent cell id, ``$last`` = last saved id)
    - ``{"sleep": 0.5}``, ``{"hang": true}`` (wait forever, for interrupt tests),
      ``{"raise": "message"}``
    """

    def __init__(self, script: list[dict[str, Any]] | dict[str, Any], chunk: int = 16, delay: float = 0.01):
        if isinstance(script, dict):  # {"steps": [...], "chunk": 8, "delay": 0.05}
            chunk, delay = script.get("chunk", chunk), script.get("delay", delay)
            script = script["steps"]
        self.steps, self.chunk, self.delay = script, chunk, delay

    @classmethod
    def from_file(cls, path: str) -> "FakeBackend":
        return cls(json.loads(Path(path).read_text()))

    @staticmethod
    def _subst(v: Any, env: dict[str, str]) -> Any:
        if isinstance(v, str):
            for k, val in env.items():
                v = v.replace(f"${k}", val)
            return v
        if isinstance(v, dict):
            return {k: FakeBackend._subst(x, env) for k, x in v.items()}
        if isinstance(v, list):
            return [FakeBackend._subst(x, env) for x in v]
        return v

    async def _type(self, kind: str, text: str, stream: AgentStream) -> None:
        stream.begin(kind)
        add = stream.thinking if kind == "thinking" else stream.text
        for i in range(0, len(text), self.chunk):
            add(text[i : i + self.chunk])
            await asyncio.sleep(self.delay)

    async def run(self, run: AgentRun) -> None:
        env = {"agent": run.ctx.cell_id}
        for step in self.steps:
            if "thinking" in step:
                await self._type("thinking", step["thinking"], run.stream)
            elif "text" in step:
                await self._type("text", step["text"], run.stream)
            elif "tool" in step:
                args = self._subst(step.get("args", {}), env)
                try:
                    res = await run.toolbox.call(step["tool"], args)
                except ToolError as e:
                    env["error"] = str(e)
                    continue
                if res.data.get("id"):
                    env["last"] = res.data["id"]
                    if step.get("save_as"):
                        env[step["save_as"]] = res.data["id"]
                env["result"] = res.text
            elif "sleep" in step:
                await asyncio.sleep(float(step["sleep"]))
            elif step.get("hang"):
                await asyncio.Event().wait()
            elif "raise" in step:
                raise AgentError(step["raise"])


def make_backend(settings: dict[str, Any]) -> AgentBackend:
    spec = os.environ.get("YUPYTER_AGENT_BACKEND") or settings.get("backend") or ""
    if spec.startswith("fake:"):
        return FakeBackend.from_file(spec[len("fake:") :])
    return SdkBackend()


def agent_settings() -> dict[str, Any]:
    try:
        s = config.load_config().get("agent", {})
        return dict(s) if isinstance(s, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


class _Interrupted(AgentError):
    pass


class ClaudeAgentExecutor:
    def __init__(self, name: str = "agent:claude", backend: AgentBackend | None = None) -> None:
        self.name = name
        self._backend = backend
        self._run_task: asyncio.Task | None = None
        self._toolbox: Toolbox | None = None
        self._interrupted = False

    is_started = True

    def _get_backend(self, settings: dict[str, Any]) -> AgentBackend:
        return self._backend or make_backend(settings)

    async def execute(self, cell_id: str, source: str, ctx: RunContext) -> AsyncIterator[Output]:
        settings = agent_settings()
        doc = ctx.doc
        self._interrupted = False
        stream = AgentStream(doc, cell_id)
        error: AgentError | None = None
        toolbox: Toolbox | None = None
        replaced = 0
        try:
            undo.track_run(doc, ctx.run_id)  # before the first write, so the whole run is one step
            before = set(schema.ordered_ids(doc))
            anchor = prepare_rerun(doc, cell_id, ctx.run_id, ctx.origin)
            replaced = len(before - set(schema.ordered_ids(doc)))
            toolbox = Toolbox(ctx, anchor=anchor, stream=stream)
            toolbox.on_anchor = lambda cid: _presence(ctx, cid)
            self._toolbox = toolbox
            _presence(ctx, cell_id)
            policy_note = ""
            if toolbox.policy() == "propose":
                policy_note = (
                    "Policy: this notebook is in 'propose' mode. run_cell does not run anything; "
                    "it marks the cell for a human to run. Insert the cells and explain what to run."
                )
            rendered = ctxmod.render_context(doc, cell_id, note=policy_note)
            stream.start()
            run = AgentRun(ctx, toolbox, stream, rendered, settings)
            self._run_task = asyncio.ensure_future(self._get_backend(settings).run(run))
            timeout = float(settings.get("timeout_s", 900))
            try:
                done, _ = await asyncio.wait({self._run_task}, timeout=timeout)
            except asyncio.CancelledError:
                self._run_task.cancel()
                raise
            if not done:
                self._run_task.cancel()
                await asyncio.wait({self._run_task}, timeout=5)
                stream.note(f"stopped: {timeout:.0f}s time limit reached")
                error = AgentError(f"The agent hit its {timeout:.0f}s time limit.")
            else:
                try:
                    self._run_task.result()
                except asyncio.CancelledError:
                    if self._interrupted:
                        stream.note("interrupted")
                        error = _Interrupted("Agent run interrupted.")
                    else:
                        raise
                except AgentError as e:
                    stream.note(str(e).splitlines()[0][:200])
                    error = e
                except Exception as e:
                    log.exception("agent run failed")
                    stream.note(f"{type(e).__name__}: {e}")
                    error = AgentError(f"{type(e).__name__}: {e}")
        finally:
            self._run_task = None
            self._toolbox = None
            await stream.close()
            _presence(ctx, None)
            if toolbox is not None:
                write_summary(doc, cell_id, toolbox, replaced)
        if error is not None:
            if isinstance(error, _Interrupted):
                yield {"output_type": "error", "ename": "KeyboardInterrupt",
                       "evalue": "agent run interrupted", "traceback": []}
            else:
                yield {"output_type": "error", "ename": "AgentError", "evalue": str(error), "traceback": []}

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._toolbox is not None:
            await self._toolbox.interrupt_active()
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()

    async def shutdown(self) -> None:
        await self.interrupt()


def register() -> None:
    from ..kernels.registry import register_agent

    register_agent("claude", lambda name: ClaudeAgentExecutor(name))

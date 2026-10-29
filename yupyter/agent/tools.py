"""The agent's tool layer: operations on the live notebook doc.

``Toolbox`` is backend-agnostic: the Claude Agent SDK wraps it as an in-process
MCP server (``executor.py``), and the fake test backend calls it directly. All
writes go through ``yupyter.doc.schema`` on the server's pycrdt doc, so they
sync live to every browser.
"""
from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config
from ..doc import schema
from ..kernels.executor import RunContext
from . import context as ctxmod

AGENT_AUTHOR = "claude"
MAX_TIMEOUT = 600.0


class ToolError(Exception):
    """A problem the model should see (bad id, deadlock guard, ...)."""


@dataclass
class ToolResult:
    text: str
    images: list[tuple[str, str]] = field(default_factory=list)  # (mime, base64)
    data: dict[str, Any] = field(default_factory=dict)  # machine-readable (fake backend)


@functools.lru_cache(maxsize=1)
def installed_kernelspecs() -> dict[str, str]:
    """name -> language of installed kernelspecs ({} if they cannot be listed)."""
    try:
        from jupyter_client.kernelspec import KernelSpecManager

        ksm = KernelSpecManager()
        out = {}
        for name in ksm.find_kernel_specs():
            try:
                out[name] = ksm.get_kernel_spec(name).language or ""
            except Exception:
                out[name] = ""
        return out
    except Exception:
        return {}


def is_agent_kernel(name: str | None) -> bool:
    return bool(name) and config.resolve_alias(str(name)).startswith("agent:")


class Toolbox:
    def __init__(
        self,
        ctx: RunContext,
        *,
        anchor: str | None = None,
        stream: Any = None,  # AgentStream-like: tool_event(name, summary, status) -> dict, touch()
    ) -> None:
        self.ctx = ctx
        self.doc = ctx.doc
        self.anchor = anchor or ctx.cell_id  # new cells go after this one
        self.stream = stream
        self.created: list[str] = []
        self.active_kernel: str | None = None  # kernel of a run_cell in flight
        self.on_anchor: Callable[[str], None] | None = None  # presence hook
        self.limits = ctxmod.Limits()

    # -- policy ---------------------------------------------------------------
    def policy(self) -> str:
        return str(schema.meta(self.doc).get("agent_policy") or "auto")

    def can_delete_any(self) -> bool:
        return str(schema.meta(self.doc).get("agent_delete") or "own") == "any"

    # -- helpers ----------------------------------------------------------------
    def _event(self, name: str, summary: str, status: str = "ok") -> dict[str, Any] | None:
        if self.stream is None:
            return None
        return self.stream.tool_event(name, summary, status)

    def _finish_event(self, ev: dict[str, Any] | None, summary: str, status: str) -> None:
        if ev is not None:
            ev["summary"], ev["status"] = summary, status
            self.stream.touch()

    def _need(self, cell_id: str) -> dict[str, Any]:
        if not isinstance(cell_id, str) or not schema.has_cell(self.doc, cell_id):
            raise ToolError(f"no such cell: {cell_id!r}")
        return schema.cell_snapshot(self.doc, cell_id)

    def _need_not_agent(self, cell_id: str, verb: str) -> dict[str, Any]:
        snap = self._need(cell_id)
        if cell_id == self.ctx.cell_id:
            raise ToolError(f"cannot {verb} the agent cell you are running in ({cell_id})")
        if is_agent_kernel(snap["kernel"]):
            raise ToolError(f"cannot {verb} agent cell {cell_id}")
        return snap

    def _resolve_kernel(self, alias: str) -> str:
        name = config.resolve_alias(alias)
        if name.startswith("agent:"):
            raise ToolError("cannot create or switch to an agent cell; use a language kernel")
        specs = installed_kernelspecs()
        base = name.split(":", 1)[0]
        if specs and base not in specs:
            raise ToolError(
                f"unknown kernel {alias!r}. Installed kernelspecs: {', '.join(sorted(specs))}; "
                f"aliases: {', '.join(sorted(config.aliases()))}"
            )
        return name

    async def interrupt_active(self) -> None:
        """Interrupt the kernel of a run_cell in flight (once)."""
        kernel, self.active_kernel = self.active_kernel, None
        if kernel:
            try:
                await self.ctx.interrupt_kernel(kernel)
            except Exception:
                pass

    # -- tools ------------------------------------------------------------------
    async def insert_cell(
        self, source: str, kernel: str = "py", kind: str = "code", after_id: str | None = None
    ) -> ToolResult:
        if kind not in ("code", "markdown"):
            raise ToolError("kind must be 'code' or 'markdown'")
        after = after_id or self.anchor
        self._need(after)
        resolved = "markdown" if kind == "markdown" else self._resolve_kernel(kernel or "py")
        cid = schema.insert_cell(
            self.doc,
            after,
            kind,
            resolved,
            source or "",
            AGENT_AUTHOR,
            created_by_run=self.ctx.run_id,
            origin=self.ctx.origin,
        )
        self.created.append(cid)
        self.anchor = cid
        if self.on_anchor:
            self.on_anchor(cid)
        label = "markdown" if kind == "markdown" else (kernel or "py")
        self._event("insert_cell", f"inserted cell {cid} ({label})")
        return ToolResult(f"inserted cell {cid} ({label}) after {after}", data={"id": cid})

    async def edit_cell(self, id: str, old: str, new: str) -> ToolResult:
        self._need_not_agent(id, "edit")
        try:
            schema.edit_source(self.doc, id, old, new, origin=self.ctx.origin)
        except ValueError as e:
            raise ToolError(str(e)) from None
        self._event("edit_cell", f"edited cell {id}")
        return ToolResult(f"edited cell {id}", data={"id": id})

    async def read_cell(self, id: str, include_outputs: bool = True) -> ToolResult:
        snap = self._need(id)
        lines = [
            f"cell {id}: kind={snap['kind']} kernel={snap['kernel']} "
            f"state={snap['exec'].get('state')} author={snap['author']}",
            ctxmod._fence(ctxmod.truncate(snap["source"], self.limits.max_source_chars)),
        ]
        images: list[tuple[str, str]] = []
        if include_outputs:
            for kind, val in ctxmod.render_outputs(snap["outputs"], self.limits):
                if kind == "text":
                    lines.append("output:\n" + ctxmod._fence(val))
                else:
                    mime, b64 = val.split("|", 1)
                    if len(images) < 2 and len(b64) <= self.limits.max_image_b64:
                        images.append((mime, b64))
                    lines.append("[image output]")
        return ToolResult("\n".join(lines), images=images, data={"id": id})

    async def delete_cell(self, id: str) -> ToolResult:
        snap = self._need_not_agent(id, "delete")
        if snap["created_by_run"] != self.ctx.run_id and not self.can_delete_any():
            raise ToolError(
                f"cell {id} was not created by this run; only cells you created can be deleted"
            )
        schema.delete_cell(self.doc, id, origin=self.ctx.origin)
        if self.anchor == id:
            self.anchor = self.ctx.cell_id
        self._event("delete_cell", f"deleted cell {id}")
        return ToolResult(f"deleted cell {id}")

    async def set_kernel(self, id: str, kernel: str) -> ToolResult:
        self._need_not_agent(id, "change the kernel of")
        resolved = self._resolve_kernel(kernel)
        cell = schema.get_cell(self.doc, id)
        with self.doc.transaction(origin=self.ctx.origin):
            cell["kernel"] = resolved
        self._event("set_kernel", f"cell {id} kernel -> {kernel}")
        return ToolResult(f"cell {id} now uses kernel {kernel} ({resolved})")

    async def list_kernels(self) -> ToolResult:
        specs = installed_kernelspecs()
        lines = ["aliases (use these in insert_cell / set_kernel):"]
        for a, target in sorted(config.aliases().items()):
            if not target.startswith("agent:"):
                lines.append(f"  {a} -> {target}")
        lines.append("installed kernelspecs:")
        for n, lang in sorted(specs.items()):
            lines.append(f"  {n} ({lang or 'unknown language'})")
        return ToolResult("\n".join(lines))

    async def run_cell(self, id: str, timeout: float = 60) -> ToolResult:
        snap = self._need(id)
        if id == self.ctx.cell_id:
            raise ToolError("cannot run the agent cell you are running in (it would deadlock)")
        if is_agent_kernel(snap["kernel"]):
            raise ToolError(f"cannot run agent cell {id} (it would deadlock the agent queue)")
        if snap["kind"] == "markdown":
            return ToolResult(f"cell {id} is markdown; nothing to run")
        if self.policy() == "propose":
            cell = schema.get_cell(self.doc, id)
            with self.doc.transaction(origin=self.ctx.origin):
                cell["proposed"] = True
            self._event("run_cell", f"proposed running {id} (awaiting approval)", "pending")
            return ToolResult(f"queued for human approval: cell {id} was marked; a human must run it")
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 60.0
        timeout = min(max(timeout, 1.0), MAX_TIMEOUT)
        kernel = config.resolve_alias(snap["kernel"] or "python3")
        ev = self._event("run_cell", f"running {id}…", "running")
        t0 = time.monotonic()
        task = asyncio.ensure_future(self.ctx.run_cell(id))
        timed_out = False
        self.active_kernel = kernel
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                timed_out = True
                await self.ctx.interrupt_kernel(kernel)
                await asyncio.wait({task}, timeout=15)
        except asyncio.CancelledError:
            # the agent run itself was interrupted: stop the cell it started
            await self.interrupt_active()
            task.cancel()
            raise
        finally:
            self.active_kernel = None
        if not task.done():
            task.cancel()
            self._finish_event(ev, f"run of {id} timed out", "error")
            return ToolResult(f"cell {id} did not stop after {timeout:.0f}s and could not be interrupted")
        try:
            outs = task.result()
        except asyncio.CancelledError:
            outs = schema.get_outputs(self.doc, id) if schema.has_cell(self.doc, id) else []
        except KeyError as e:
            raise ToolError(str(e)) from None
        failed = any(o.get("output_type") == "error" for o in outs)
        dt = time.monotonic() - t0
        mark = "✗" if failed or timed_out else "✓"
        self._finish_event(
            ev, f"ran {id} {mark}" + (" (timed out)" if timed_out else ""), "error" if failed or timed_out else "ok"
        )
        head = (
            f"cell {id} timed out after {timeout:.0f}s and was interrupted"
            if timed_out
            else f"cell {id} finished {'with an error' if failed else 'ok'} in {dt:.1f}s"
        )
        lines = [head]
        images: list[tuple[str, str]] = []
        for kind, val in ctxmod.render_outputs(outs, self.limits):
            if kind == "text":
                lines.append(val)
            else:
                mime, b64 = val.split("|", 1)
                if len(images) < 2 and len(b64) <= self.limits.max_image_b64:
                    images.append((mime, b64))
                lines.append("[image output shown below]" if len(images) <= 2 else "[image output]")
        if len(lines) == 1:
            lines.append("(no output)")
        return ToolResult("\n".join(lines), images=images, data={"id": id, "failed": failed or timed_out})

    # -- dispatch ----------------------------------------------------------------
    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        spec = TOOL_SPECS.get(name)
        if spec is None:
            raise ToolError(f"unknown tool {name!r}")
        try:
            return await getattr(self, name)(**(args or {}))
        except ToolError as e:
            self._event(name, f"{name} failed: {e}", "error")
            raise
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from None
        except KeyError as e:
            raise ToolError(str(e).strip("'\"")) from None


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_S = {"type": "string"}
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "insert_cell": {
        "description": (
            "Insert a new cell. By default it goes right after the previous cell you inserted "
            "(or after the agent cell), so the notebook reads top to bottom. Returns the new cell id. "
            "Does not run it; call run_cell for that."
        ),
        "schema": _obj(
            {
                "source": {**_S, "description": "Cell source (code, or markdown text)."},
                "kernel": {**_S, "description": "Kernel alias, e.g. 'py' or 'r'. See list_kernels."},
                "kind": {"type": "string", "enum": ["code", "markdown"]},
                "after_id": {**_S, "description": "Insert after this cell id instead of the default place."},
            },
            ["source"],
        ),
    },
    "edit_cell": {
        "description": (
            "Search/replace inside a cell's source. `old` must occur exactly once. "
            "Never rewrites the whole cell, so concurrent human edits are preserved."
        ),
        "schema": _obj({"id": _S, "old": _S, "new": _S}, ["id", "old", "new"]),
    },
    "run_cell": {
        "description": (
            "Run a code cell on its kernel and wait for it. Returns its outputs as text "
            "(plots come back as images). Interrupts the cell after `timeout` seconds."
        ),
        "schema": _obj({"id": _S, "timeout": {"type": "number", "description": "seconds, default 60"}}, ["id"]),
    },
    "read_cell": {
        "description": "Read a cell's source and current outputs (useful beyond the rendered context).",
        "schema": _obj({"id": _S, "include_outputs": {"type": "boolean"}}, ["id"]),
    },
    "delete_cell": {
        "description": "Delete a cell. Only cells created by this run can be deleted.",
        "schema": _obj({"id": _S}, ["id"]),
    },
    "set_kernel": {
        "description": "Change which kernel a cell runs on (by alias).",
        "schema": _obj({"id": _S, "kernel": _S}, ["id", "kernel"]),
    },
    "list_kernels": {
        "description": "List kernel aliases and installed kernelspecs.",
        "schema": _obj({}, []),
    },
}

TOOL_NAMES = list(TOOL_SPECS)

"""The one execution interface: Jupyter kernels and agents both implement it."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

from pycrdt import Doc

if TYPE_CHECKING:  # pragma: no cover
    from ..doc.store import DocStore

# nbformat-style output dicts, one of:
#   {"output_type": "stream", "name": "stdout"|"stderr", "text": str}
#   {"output_type": "display_data", "data": {mime: value}, "metadata": {}}
#   {"output_type": "execute_result", "data": {...}, "metadata": {}, "execution_count": int|None}
#   {"output_type": "error", "ename": str, "evalue": str, "traceback": [str]}
Output = dict[str, Any]


@dataclass
class RunContext:
    """Everything an executor may need while running one cell."""

    doc: Doc
    store: "DocStore"
    run_id: str
    notebook_path: Path
    cell_id: str
    kernel: str  # the executor name this cell was resolved to, e.g. "python3"
    author: str | None = None
    # Run another cell of this notebook through the scheduler and wait for its
    # outputs. Agent executors use this to implement their run_cell tool.
    run_cell: Callable[..., Awaitable[list[Output]]] = field(default=None)  # type: ignore
    # Undo/provenance origin to use for doc writes made on behalf of this run.
    origin: str = ""


@runtime_checkable
class Executor(Protocol):
    def execute(self, cell_id: str, source: str, ctx: RunContext) -> AsyncIterator[Output]:
        """Run ``source`` and yield outputs as they are produced.

        Implement as an async generator. Raise to signal an executor failure
        (the scheduler turns it into an ``error`` output + ``error`` state).
        """
        ...

    async def interrupt(self) -> None:
        """Stop the currently running execution, if any."""
        ...

    async def shutdown(self) -> None:
        """Release resources (kill the kernel, cancel the agent loop...)."""
        ...

    # Optional (looked up with getattr by the scheduler):
    #   is_started: bool        -- False until the backing kernel is up, lets the
    #                              scheduler publish "starting"
    #   async def restart()     -- restart the kernel

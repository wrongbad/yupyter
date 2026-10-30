"""RunScheduler: one FIFO queue per executor name; the only writer of outputs/exec."""
from __future__ import annotations

import asyncio
import logging
import secrets
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .doc import schema, undo
from .doc.store import DocStore
from .kernels.executor import Output, RunContext
from .kernels.registry import ExecutorRegistry

log = logging.getLogger("yupyter.scheduler")


@dataclass
class _Job:
    path: Path
    cell_id: str
    run_id: str
    author: str | None
    future: "asyncio.Future[list[Output]]"
    skip: bool = False


class RunScheduler:
    def __init__(self, store: DocStore, registry: ExecutorRegistry | None = None) -> None:
        self.store = store
        self.registry = registry or ExecutorRegistry()
        # One FIFO queue + worker per (notebook path, executor name): a notebook
        # has its own kernel, like Jupyter.
        self._queues: dict[tuple[Path, str], asyncio.Queue[_Job]] = {}
        self._workers: dict[tuple[Path, str], asyncio.Task] = {}
        self._counts: dict[tuple[Path, str], int] = {}
        self._current: dict[tuple[Path, str], _Job | None] = {}

    # -- public API ---------------------------------------------------------
    async def run_cell(
        self,
        path: str | Path,
        cell_id: str,
        *,
        author: str | None = None,
        run_id: str | None = None,
        wait_for_cell: float = 0.0,
    ) -> list[Output]:
        """Queue a cell on its kernel's queue and wait until it finishes.

        Returns the outputs. ``wait_for_cell`` waits (up to N seconds) for a
        client-created cell to arrive over the sync channel before failing.
        """
        od = self.store.open(path)
        doc = od.doc
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_for_cell
        while not schema.has_cell(doc, cell_id):
            if loop.time() >= deadline:
                raise KeyError(f"no such cell: {cell_id}")
            await asyncio.sleep(0.02)
        cell = schema.get_cell(doc, cell_id)
        if cell.get("kind") == "markdown":
            return []
        name = self.registry.normalize(cell.get("kernel") or "python3")
        job = _Job(od.path, cell_id, run_id or secrets.token_hex(4), author, loop.create_future())
        schema.set_exec_state(doc, cell_id, "queued", run_id=job.run_id)
        self._ensure_worker((od.path, name)).put_nowait(job)
        return await job.future

    async def interrupt(self, kernel: str, path: str | Path | None = None) -> None:
        """Interrupt the running cell on a kernel and drop its queued jobs."""
        name = self.registry.normalize(kernel)
        p = Path(path).resolve() if path is not None else None
        for (qp, qn), q in self._queues.items():
            if qn == name and (p is None or qp == p):
                for job in list(q._queue):  # type: ignore[attr-defined]
                    job.skip = True
        for ex in self.registry.find(name, p):
            await ex.interrupt()

    def busy(self, path: Path) -> bool:
        """True if the notebook has a queued or running job."""
        return any(
            (qp == path and (not q.empty() or self._current.get((qp, qn)) is not None))
            for (qp, qn), q in self._queues.items()
        )

    def kernel_running(self, path: Path) -> bool:
        return self.registry.running(path)

    def rename(self, old: Path, new: Path) -> None:
        """Follow a notebook rename (call only when not ``busy``): the kernel moves with it."""
        for key in [k for k in self._queues if k[0] == old]:
            self._workers.pop(key).cancel()
            del self._queues[key]
            self._current.pop(key, None)
            if key in self._counts:
                self._counts[(new, key[1])] = self._counts.pop(key)
        self.registry.rename(old, new)

    async def shutdown(self) -> None:
        for t in self._workers.values():
            t.cancel()
        await self.registry.shutdown()

    # -- internals ------------------------------------------------------------
    def _ensure_worker(self, key: tuple[Path, str]) -> "asyncio.Queue[_Job]":
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
            self._workers[key] = asyncio.create_task(self._worker(key))
        return self._queues[key]

    async def _worker(self, key: tuple[Path, str]) -> None:
        q = self._queues[key]
        while True:
            job = await q.get()
            self._current[key] = job
            try:
                outs = await self._run_job(key, job)
                if not job.future.done():
                    job.future.set_result(outs)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never kill the worker
                log.exception("job failed")
                if not job.future.done():
                    job.future.set_exception(e)
            finally:
                self._current[key] = None

    async def _run_job(self, key: tuple[Path, str], job: _Job) -> list[Output]:
        name = key[1]
        doc = self.store.open(job.path).doc
        cid = job.cell_id
        if job.skip or not schema.has_cell(doc, cid):
            if schema.has_cell(doc, cid):
                schema.set_exec_state(doc, cid, "idle")
            return []
        source = schema.get_source(doc, cid)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        origin = undo.run_origin(job.run_id)
        outputs: list[Output] = []
        failed = False
        try:
            executor = self.registry.resolve(name, job.path)
            starting = getattr(executor, "is_started", True) is False
            schema.set_kernel_status(doc, name, "starting" if starting else "busy")
            schema.clear_outputs(doc, cid)
            schema.set_exec_state(doc, cid, "running", count=count, run_id=job.run_id)
            ctx = RunContext(
                doc=doc,
                store=self.store,
                run_id=job.run_id,
                notebook_path=job.path,
                cell_id=cid,
                kernel=name,
                author=job.author,
                run_cell=lambda cell_id, **kw: self.run_cell(
                    job.path, cell_id, author=job.author, **kw
                ),
                interrupt_kernel=lambda k: self.interrupt(k, job.path),
                origin=origin,
            )
            first = True
            async for out in executor.execute(cid, source, ctx):
                if first:
                    schema.set_kernel_status(doc, name, "busy")
                    first = False
                if out.get("output_type") == "error":
                    failed = True
                if schema.has_cell(doc, cid):
                    schema.append_output(doc, cid, out)
                outputs.append(out)
            schema.set_kernel_status(doc, name, "idle")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            failed = True
            err = {
                "output_type": "error",
                "ename": type(e).__name__,
                "evalue": str(e),
                "traceback": traceback.format_exc().splitlines(),
            }
            outputs.append(err)
            if schema.has_cell(doc, cid):
                schema.append_output(doc, cid, err)
            schema.set_kernel_status(doc, name, "dead")
        if schema.has_cell(doc, cid):
            schema.set_exec_state(doc, cid, "error" if failed else "done")
            return schema.get_outputs(doc, cid)  # merged view, as stored in the doc
        return outputs

    def status(self) -> dict[str, Any]:
        return {f"{p}:{n}": {"queued": q.qsize(), "running": self._current.get((p, n)) is not None}
                for (p, n), q in self._queues.items()}

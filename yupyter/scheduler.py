"""RunScheduler: one FIFO queue per executor name; the only writer of outputs/exec."""
from __future__ import annotations

import asyncio
import logging
import secrets
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .doc import schema
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
        self._queues: dict[str, asyncio.Queue[_Job]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._counts: dict[str, int] = {}
        self._current: dict[str, _Job | None] = {}

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
        self._ensure_worker(name).put_nowait(job)
        return await job.future

    async def interrupt(self, kernel: str, path: str | Path | None = None) -> None:
        """Interrupt the running cell on a kernel and drop its queued jobs."""
        name = self.registry.normalize(kernel)
        q = self._queues.get(name)
        if q is not None:
            for job in list(q._queue):  # type: ignore[attr-defined]
                job.skip = True
        ex = self.registry._executors.get(name)
        if ex is not None:
            await ex.interrupt()

    async def shutdown(self) -> None:
        for t in self._workers.values():
            t.cancel()
        await self.registry.shutdown()

    # -- internals ------------------------------------------------------------
    def _ensure_worker(self, name: str) -> "asyncio.Queue[_Job]":
        if name not in self._queues:
            self._queues[name] = asyncio.Queue()
            self._workers[name] = asyncio.create_task(self._worker(name))
        return self._queues[name]

    async def _worker(self, name: str) -> None:
        q = self._queues[name]
        while True:
            job = await q.get()
            self._current[name] = job
            try:
                outs = await self._run_job(name, job)
                if not job.future.done():
                    job.future.set_result(outs)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never kill the worker
                log.exception("job failed")
                if not job.future.done():
                    job.future.set_exception(e)
            finally:
                self._current[name] = None

    async def _run_job(self, name: str, job: _Job) -> list[Output]:
        doc = self.store.open(job.path).doc
        cid = job.cell_id
        if job.skip or not schema.has_cell(doc, cid):
            if schema.has_cell(doc, cid):
                schema.set_exec_state(doc, cid, "idle")
            return []
        source = schema.get_source(doc, cid)
        count = self._counts.get(name, 0) + 1
        self._counts[name] = count
        origin = f"run:{job.run_id}"
        outputs: list[Output] = []
        failed = False
        try:
            executor = self.registry.resolve(name)
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
        return {n: {"queued": q.qsize(), "running": self._current.get(n) is not None}
                for n, q in self._queues.items()}

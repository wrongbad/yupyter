"""Executor backed by a jupyter_client kernel (lazily started)."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from jupyter_client.manager import AsyncKernelManager

from .executor import Output, RunContext

log = logging.getLogger("yupyter.kernel")


class JupyterExecutor:
    def __init__(self, kernel_name: str) -> None:
        self.kernel_name = kernel_name
        self.km: AsyncKernelManager | None = None
        self.client = None
        self._start_lock = asyncio.Lock()

    @property
    def is_started(self) -> bool:
        return self.km is not None and self.client is not None

    async def start(self) -> None:
        async with self._start_lock:
            if self.is_started:
                return
            km = AsyncKernelManager(kernel_name=self.kernel_name)
            await km.start_kernel()
            client = km.client()
            client.start_channels()
            try:
                await client.wait_for_ready(timeout=60)
            except Exception:
                client.stop_channels()
                await km.shutdown_kernel(now=True)
                raise
            self.km, self.client = km, client

    async def execute(self, cell_id: str, source: str, ctx: RunContext) -> AsyncIterator[Output]:
        await self.start()
        client = self.client
        assert client is not None
        msg_id = client.execute(source, store_history=True, allow_stdin=False)
        while True:
            msg = await client.get_iopub_msg()
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            mtype = msg["msg_type"]
            c = msg["content"]
            if mtype == "stream":
                yield {"output_type": "stream", "name": c["name"], "text": c["text"]}
            elif mtype == "display_data":
                yield {
                    "output_type": "display_data",
                    "data": c["data"],
                    "metadata": c.get("metadata", {}),
                }
            elif mtype == "execute_result":
                yield {
                    "output_type": "execute_result",
                    "data": c["data"],
                    "metadata": c.get("metadata", {}),
                    "execution_count": c.get("execution_count"),
                }
            elif mtype == "error":
                yield {
                    "output_type": "error",
                    "ename": c["ename"],
                    "evalue": c["evalue"],
                    "traceback": c["traceback"],
                }
            elif mtype == "status" and c["execution_state"] == "idle":
                break
        try:  # drain the shell reply so it doesn't pile up
            await client.get_shell_msg(timeout=5)
        except Exception:
            pass

    async def interrupt(self) -> None:
        if self.km is not None:
            await self.km.interrupt_kernel()

    async def restart(self) -> None:
        if self.km is not None:
            await self.km.restart_kernel(now=True)
            await self.client.wait_for_ready(timeout=60)  # type: ignore[union-attr]

    async def shutdown(self) -> None:
        if self.client is not None:
            self.client.stop_channels()
        if self.km is not None:
            try:
                await self.km.shutdown_kernel(now=True)
            except Exception:
                log.exception("kernel shutdown failed")
        self.km = self.client = None

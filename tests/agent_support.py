"""Shared helpers for the agent tests."""
from __future__ import annotations

import base64
import secrets

import pytest

from yupyter.agent.executor import AgentStream, ClaudeAgentExecutor
from yupyter.agent.tools import Toolbox
from yupyter.doc import schema
from yupyter.doc.store import DocStore
from yupyter.kernels.executor import RunContext
from yupyter.scheduler import RunScheduler

# 1x1 transparent png
PNG_B64 = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
    )
).decode()


@pytest.fixture
async def nb(tmp_path):
    store = DocStore()
    sched = RunScheduler(store)
    path = tmp_path / "nb.md"
    doc = store.get_doc(path)
    yield store, sched, path, doc
    await sched.shutdown()


def use_backend(sched: RunScheduler, backend) -> ClaudeAgentExecutor:
    ex = ClaudeAgentExecutor(backend=backend)
    sched.registry._executors["agent:claude"] = ex
    return ex


def make_ctx(store, sched, path, doc, agent_id: str, run_id: str | None = None) -> RunContext:
    run_id = run_id or secrets.token_hex(4)
    return RunContext(
        doc=doc,
        store=store,
        run_id=run_id,
        notebook_path=path,
        cell_id=agent_id,
        kernel="agent:claude",
        author="me",
        run_cell=lambda cid, **kw: sched.run_cell(path, cid, author="me", **kw),
        interrupt_kernel=sched.interrupt,
        origin=f"run:{run_id}",
    )


def make_toolbox(store, sched, path, doc, agent_id: str) -> Toolbox:
    return Toolbox(make_ctx(store, sched, path, doc, agent_id), stream=AgentStream(doc, agent_id))


def add_agent_cell(doc, prompt="do it", after=None) -> str:
    return schema.insert_cell(doc, after, "code", "agent:claude", prompt, "me")

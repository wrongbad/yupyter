import asyncio
import json
import sys
import types

import pytest

from yupyter.agent import context as C
from yupyter.agent.executor import (
    AgentRun,
    AgentStream,
    FakeBackend,
    SdkBackend,
    friendly_error,
)
from yupyter.doc import schema

from agent_support import add_agent_cell, nb, use_backend  # noqa: F401

PLOT = "import matplotlib.pyplot as plt\nimport numpy as np\nx = np.linspace(-5, 5, 20)\nplt.plot(x, 2 + x)\nplt.show()"
DEMO = [
    {"thinking": "The user wants y=2+x plotted. I'll use matplotlib."},
    {"tool": "insert_cell", "args": {"source": PLOT, "kernel": "py"}, "save_as": "plot"},
    {"tool": "run_cell", "args": {"id": "$plot"}},
    {"text": "Plotted **y = 2 + x**."},
]


def _agent_events(doc, cell_id):
    outs = schema.get_outputs(doc, cell_id)
    assert outs and outs[0]["output_type"] == "display_data"
    return outs[0]["data"][C.AGENT_MIME], outs[0]["data"]["text/plain"]


async def test_demo_graph_through_registry_env_backend(nb, tmp_path, monkeypatch):
    store, sched, path, doc = nb
    script = tmp_path / "script.json"
    script.write_text(json.dumps(DEMO))
    monkeypatch.setenv("YUPYTER_AGENT_BACKEND", f"fake:{script}")
    import yupyter.agent  # noqa: F401  (registers agent:claude)

    ag = add_agent_cell(doc, "please graph y=2+x")
    outs = await sched.run_cell(path, ag)
    cells = schema.ordered_cells(doc)
    assert [c["id"] for c in cells][0] == ag and len(cells) == 2
    new = cells[1]
    assert new["author"] == "claude" and new["kernel"] == "python3"
    assert new["created_by_run"] == cells[0]["exec"]["runId"]
    assert any("image/png" in o.get("data", {}) for o in new["outputs"])
    assert new["exec"]["state"] == "done"
    events, fallback = _agent_events(doc, ag)
    assert [e["type"] for e in events] == ["thinking", "tool", "tool", "text"]
    assert events[0]["done"] is True and "matplotlib" in events[0]["text"]
    assert events[1]["summary"] == f"inserted cell {new['id']} (py)"
    assert events[2]["summary"] == f"ran {new['id']} ✓" and events[2]["status"] == "ok"
    assert "y = 2 + x" in events[3]["text"] and "[thinking]" in fallback
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "done"
    assert len(outs) == 1 and schema.kernels(doc)["agent:claude"]["status"] == "idle"


async def test_agent_sees_edited_earlier_cell(nb):
    store, sched, path, doc = nb
    seen = []

    class Spy:
        async def run(self, run: AgentRun):
            seen.append(run.rendered.text())

    use_backend(sched, Spy())
    a = schema.insert_cell(doc, None, "code", "python3", "x = 1", "me")
    ag = add_agent_cell(doc, "look", after=a)
    await sched.run_cell(path, ag)
    schema.edit_source(doc, a, "x = 1", "x = 99")
    await sched.run_cell(path, ag)
    assert "x = 1" in seen[0] and "x = 99" in seen[1] and "x = 1\n" not in seen[1]


async def test_rerun_replace_deletes_only_this_agent_cells_previous_cells(nb):
    store, sched, path, doc = nb
    script = [{"tool": "insert_cell", "args": {"source": "v = 1"}}, {"tool": "insert_cell", "args": {"source": "v = 2"}}]
    use_backend(sched, FakeBackend(script))
    human = schema.insert_cell(doc, None, "code", "python3", "human", "me")
    a1 = add_agent_cell(doc, "first", after=human)
    a2 = add_agent_cell(doc, "second", after=a1)
    await sched.run_cell(path, a1)
    await sched.run_cell(path, a2)
    mine = lambda: [c["id"] for c in schema.ordered_cells(doc) if c["author"] == "claude"]  # noqa: E731
    first_gen = mine()
    assert len(first_gen) == 4
    by_run = {c["id"]: c["created_by_run"] for c in schema.ordered_cells(doc) if c["author"] == "claude"}
    run_a1 = schema.cell_snapshot(doc, a1)["exec"]["runId"]
    a1_cells = [i for i, r in by_run.items() if r == run_a1]
    assert len(a1_cells) == 2

    await sched.run_cell(path, a1)  # re-run: replace
    ids = schema.ordered_ids(doc)
    assert human in ids and a1 in ids and a2 in ids
    assert not any(schema.has_cell(doc, c) for c in a1_cells)  # old a1 cells gone
    a2_cells = [i for i, r in by_run.items() if r != run_a1]
    assert all(schema.has_cell(doc, c) for c in a2_cells)  # a2's untouched
    assert len(mine()) == 4
    # regenerated right under a1, in order
    assert ids[:3] == [human, a1, ids[2]] and schema.cell_snapshot(doc, ids[2])["source"] == "v = 1"
    assert schema.cell_snapshot(doc, a1)["exec"]["runs"] == [schema.cell_snapshot(doc, a1)["exec"]["runId"]]


async def test_rerun_keep_inserts_below_previous_generation(nb):
    store, sched, path, doc = nb
    use_backend(sched, FakeBackend([{"tool": "insert_cell", "args": {"source": "g"}}]))
    schema.meta(doc)["rerun"] = "keep"
    ag = add_agent_cell(doc)
    tail = schema.insert_cell(doc, ag, "code", "python3", "tail", "me")
    await sched.run_cell(path, ag)
    await sched.run_cell(path, ag)
    ids = schema.ordered_ids(doc)
    assert len(ids) == 4 and ids[0] == ag and ids[-1] == tail
    assert len(schema.cell_snapshot(doc, ag)["exec"]["runs"]) == 2
    # per-cell option overrides doc meta
    schema.get_cell(doc, ag)["rerun"] = "replace"
    await sched.run_cell(path, ag)
    assert len(schema.ordered_ids(doc)) == 3


async def test_interrupt_cancels_run_and_marks_error(nb):
    store, sched, path, doc = nb
    use_backend(sched, FakeBackend([{"thinking": "hmm"}, {"hang": True}]))
    ag = add_agent_cell(doc)
    t = asyncio.create_task(sched.run_cell(path, ag))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if schema.get_outputs(doc, ag):
            break
    await sched.interrupt("agent:claude")
    outs = await asyncio.wait_for(t, 10)
    assert outs[-1]["output_type"] == "error" and outs[-1]["ename"] == "KeyboardInterrupt"
    events, _ = _agent_events(doc, ag)
    assert events[0]["done"] is True and events[-1]["summary"] == "interrupted"
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "error"
    # the executor is reusable afterwards
    use_backend(sched, FakeBackend([{"text": "ok"}]))
    await sched.run_cell(path, ag)
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "done"


async def test_interrupt_during_run_cell_stops_the_kernel(nb):
    store, sched, path, doc = nb
    script = [
        {"tool": "insert_cell", "args": {"source": "import time; time.sleep(60)"}, "save_as": "s"},
        {"tool": "run_cell", "args": {"id": "$s", "timeout": 120}},
    ]
    use_backend(sched, FakeBackend(script))
    ag = add_agent_cell(doc)
    t = asyncio.create_task(sched.run_cell(path, ag))
    child = None
    for _ in range(200):
        await asyncio.sleep(0.1)
        cs = [c for c in schema.ordered_cells(doc) if c["id"] != ag]
        if cs and cs[0]["exec"]["state"] == "running":
            child = cs[0]["id"]
            await asyncio.sleep(1)
            break
    assert child
    await sched.interrupt("agent:claude")
    await asyncio.wait_for(t, 20)
    for _ in range(100):  # the kernel's reply lands shortly after the agent run ends
        outs = schema.get_outputs(doc, child)
        if any(o.get("ename") for o in outs):
            break
        await asyncio.sleep(0.1)
    assert any(o.get("ename") == "KeyboardInterrupt" for o in outs), outs


async def test_tool_error_is_reported_not_fatal(nb):
    store, sched, path, doc = nb
    script = [
        {"tool": "run_cell", "args": {"id": "$agent"}},
        {"tool": "insert_cell", "args": {"source": "ok = 1"}},
        {"text": "recovered"},
    ]
    use_backend(sched, FakeBackend(script))
    ag = add_agent_cell(doc)
    await sched.run_cell(path, ag)
    events, _ = _agent_events(doc, ag)
    assert events[0]["status"] == "error" and "deadlock" in events[0]["summary"]
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "done"
    assert len(schema.ordered_ids(doc)) == 2


async def test_output_updates_are_throttled(nb):
    store, sched, path, doc = nb
    use_backend(sched, FakeBackend([{"thinking": "x" * 2000}], chunk=2, delay=0.002))
    ag = add_agent_cell(doc)
    t0 = asyncio.get_running_loop().time()
    await sched.run_cell(path, ag)
    elapsed = asyncio.get_running_loop().time() - t0
    events, _ = _agent_events(doc, ag)
    assert len(events[0]["text"]) == 2000  # nothing lost to throttling
    # (instrumented in the dedicated stream test below)
    assert elapsed > 0.5


async def test_stream_write_count_bounded():
    doc = schema.new_doc()
    ag = schema.insert_cell(doc, None, "code", "agent:claude", "x", "me")
    s = AgentStream(doc, ag, interval=0.05)
    s.start()
    for _ in range(500):
        s.thinking("abc")
        await asyncio.sleep(0.002)
    await s.close()
    assert 2 <= s.writes <= 40  # ~1s of streaming at 20/s max
    assert len(schema.get_outputs(doc, ag)) == 1


async def test_presence_hook_called_when_module_exists(nb, monkeypatch):
    store, sched, path, doc = nb
    calls = []
    mod = types.ModuleType("yupyter.doc.presence")
    mod.set_agent_presence = lambda od, name, cell_id: calls.append((name, cell_id))
    monkeypatch.setitem(sys.modules, "yupyter.doc.presence", mod)
    monkeypatch.setattr("yupyter.doc.presence", mod, raising=False)
    use_backend(sched, FakeBackend([{"tool": "insert_cell", "args": {"source": "1"}, "save_as": "c"}]))
    ag = add_agent_cell(doc)
    await sched.run_cell(path, ag)
    new = [i for i in schema.ordered_ids(doc) if i != ag][0]
    assert calls == [("claude", ag), ("claude", new), ("claude", None)]


# ---- SDK backend error handling (no real model) --------------------------------


def _sdk_backend(msgs=None, exc=None):
    async def fake_query(prompt, options):
        if exc:
            raise exc
        for m in msgs:
            yield m

    return SdkBackend(query_fn=fake_query)


async def _run_sdk(nb, backend):
    store, sched, path, doc = nb
    use_backend(sched, backend)
    ag = add_agent_cell(doc)
    outs = await sched.run_cell(path, ag)
    return doc, ag, outs


async def test_missing_cli_gives_friendly_error(nb):
    from claude_agent_sdk import CLINotFoundError

    doc, ag, outs = await _run_sdk(nb, _sdk_backend(exc=CLINotFoundError("Claude Code not found")))
    err = outs[-1]
    assert err["output_type"] == "error" and err["ename"] == "AgentError"
    assert "claude" in err["evalue"] and "ANTHROPIC_API_KEY" in err["evalue"]
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "error"
    events, _ = _agent_events(doc, ag)
    assert events[-1]["status"] == "error"


async def test_auth_failure_message_gives_login_hint(nb):
    from claude_agent_sdk import AssistantMessage, TextBlock

    msg = AssistantMessage(content=[TextBlock("Not logged in · Please run /login")], model="m", error="authentication_failed")
    doc, ag, outs = await _run_sdk(nb, _sdk_backend([msg]))
    assert "log in" in outs[-1]["evalue"] and "ANTHROPIC_API_KEY" in outs[-1]["evalue"]


async def test_result_error_and_process_error(nb):
    from claude_agent_sdk import ProcessError, ResultMessage

    res = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
        session_id="s", result="Invalid API key · Please run /login",
    )
    doc, ag, outs = await _run_sdk(nb, _sdk_backend([res]))
    assert "ANTHROPIC_API_KEY" in outs[-1]["evalue"]
    doc, ag, outs = await _run_sdk(nb, _sdk_backend(exc=ProcessError("boom", exit_code=1, stderr="oops")))
    assert outs[-1]["ename"] == "AgentError" and "boom" in outs[-1]["evalue"]


async def test_sdk_stream_events_become_agent_events(nb):
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ThinkingBlock

    def se(event):
        return StreamEvent(uuid="u", session_id="s", event=event)

    msgs = [
        se({"type": "message_start", "message": {}}),
        se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}),
        se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "let me "}}),
        se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "think"}}),
        se({"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}),
        se({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}}),
        # the CLI emits one AssistantMessage per block; both duplicate the stream and are ignored
        AssistantMessage(content=[ThinkingBlock("let me think", "sig")], model="m"),
        AssistantMessage(content=[TextBlock("Hello")], model="m"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s"),
    ]
    doc, ag, outs = await _run_sdk(nb, _sdk_backend(msgs))
    events, _ = _agent_events(doc, ag)
    assert [(e["type"], e["text"]) for e in events] == [("thinking", "let me think"), ("text", "Hello")]
    assert schema.cell_snapshot(doc, ag)["exec"]["state"] == "done"


async def test_sdk_options_are_locked_down(nb):
    store, sched, path, doc = nb
    from agent_support import make_ctx

    ag = add_agent_cell(doc)
    ctx = make_ctx(store, sched, path, doc, ag)
    from yupyter.agent.tools import Toolbox

    run = AgentRun(ctx, Toolbox(ctx), AgentStream(doc, ag), C.render_context(doc, ag), {"max_turns": 7})
    opts = SdkBackend()._build_options(run, [])
    assert opts.tools == [] and opts.setting_sources == [] and opts.max_turns == 7
    assert opts.resume is None and not opts.continue_conversation
    assert set(opts.mcp_servers) == {"yupyter"}
    assert all(t.startswith("mcp__yupyter__") for t in opts.allowed_tools) and opts.allowed_tools
    assert opts.model is None and isinstance(opts.system_prompt, str) and "claude-" not in opts.system_prompt


def test_friendly_error_variants():
    assert "log in" in friendly_error("Not logged in")
    assert "not found" in friendly_error(Exception("Claude Code not found: /x")).lower()
    assert friendly_error(ValueError("weird")) == "weird"

# Contributing

## Setup

```bash
uv venv && YUPYTER_SKIP_FRONTEND=1 uv pip install -e '.[dev]'   # or: python -m venv .venv && pip install -e '.[dev]'
cd frontend && npm ci && npm run build                          # outputs to yupyter/static/ (gitignored)
```

Run: `.venv/bin/yupyter /tmp/x.md --no-browser` (prints a `?token=` URL).
Frontend dev with HMR: `cd frontend && npm run dev` (proxies `/api` and `/ws` to a server on :8890).

## Tests

```bash
.venv/bin/pytest                 # schema, scheduler on a real ipykernel, websocket sync
node tests/e2e/smoke.mjs         # browser smoke (needs built frontend; uses preinstalled Playwright chromium)
node tests/e2e/agent.mjs         # agent cell end to end with the scripted fake backend (no Claude needed)
```

## Module map

| Path | Role |
|---|---|
| `yupyter/doc/schema.py` | pycrdt doc schema + helpers (`new_doc`, `insert_cell`, `delete_cell`, `edit_source`, `set_source`, `ordered_cells`, `set_outputs`/`append_output`/`clear_outputs`, `set_exec_state`, `set_kernel_status`). All writers take `origin=`. Server alone writes `exec`/`outputs`. |
| `yupyter/doc/order.py` | fractional index keys (`between(a, b)`); mirrored in `frontend/src/doc.ts`. |
| `yupyter/doc/store.py` | `DocStore` (`open(path)`, `load(path)`, `save(path)` hooks, debounced autosave) and `OpenDoc.serve(ws)` (y-websocket sync + awareness relay). |
| `yupyter/doc/markdown_io.py` | `load_markdown(text) -> Doc`, `dump_markdown(doc, assets_dir) -> str`. Stubs (NotImplementedError); store tolerates that. |
| `yupyter/kernels/executor.py` | `Executor` protocol, `RunContext`, `Output`. |
| `yupyter/kernels/registry.py` | `ExecutorRegistry.resolve(name)`, `register_agent("claude", factory)` for `agent:*` names. |
| `yupyter/kernels/jupyter_executor.py` | `JupyterExecutor` (lazy start, iopub -> outputs, interrupt, restart). |
| `yupyter/scheduler.py` | `RunScheduler.run_cell(path, cell_id)`, `interrupt(kernel)`; FIFO queue per executor name; writes exec/outputs/kernel status. |
| `yupyter/config.py` | `~/.config/yupyter/config.toml`; `aliases`, `resolve_alias`. |
| `yupyter/server/app.py` | `create_app(notebook_path, token, port=)`; protocol in `yupyter/server/README.md`. |
| `yupyter/cli.py` | `yupyter [PATH] --port --ip --no-browser`. |
| `yupyter/agent/context.py` | `render_context(doc, agent_cell_id)`: doc through the agent cell as prompt content blocks (text + images); cells below as one line each. Pure. |
| `yupyter/agent/tools.py` | `Toolbox` (insert/edit/run/read/delete cell, set_kernel, list_kernels) on the live doc, plus `TOOL_SPECS`. Backend-agnostic. |
| `yupyter/agent/executor.py` | `ClaudeAgentExecutor` (registered as `agent:claude`), `AgentStream` (throttled streaming output), re-run replace/keep, `SdkBackend` (Claude Agent SDK), `FakeBackend`. |
| `frontend/src/outputs/agent.tsx` | renderer for `application/vnd.yupyter.agent+json` (events: `thinking`, `text`, `tool`). |
| `frontend/src/doc.ts` | Yjs doc, `WebsocketProvider`, `listCells`, `insertCell`, `runCell`. |
| `frontend/src/magic.ts` | `parsePrompt()` seam for `%%magic` handling. |
| `frontend/src/outputs/` | one renderer file per mime family; dispatcher in `index.tsx`. |
| `frontend/src/components/` | `App`, `Cell`, `Editor` (CodeMirror + y-codemirror.next), `PromptBar`. |

Notes: pycrdt `transaction.origin()` returns `hash(origin)`, not the object. Clients must not write `exec`/`outputs`.

## Agent cells

`%%claude <prompt>` makes a cell with kernel `agent:claude`. Running it starts a fresh Claude Agent SDK
session whose only tools are `Toolbox` (in-process MCP server); it uses your Claude Code login or
`ANTHROPIC_API_KEY`. Optional `[agent]` config: `model`, `max_turns` (40), `timeout_s` (900).
Doc meta: `agent_policy` = `auto`|`propose`, `agent_delete` = `own`|`any`, `rerun` = `replace`|`keep`
(a cell-level `rerun` wins). Agent-made cells carry `author="claude"` and `created_by_run`; the agent
cell's `exec.runs` lists its runs, which is what re-run "replace" deletes.

Tests without a model: `YUPYTER_AGENT_BACKEND=fake:script.json` replays a script through the same tools:

```json
{"chunk": 6, "delay": 0.04, "steps": [
  {"thinking": "..."},
  {"tool": "insert_cell", "args": {"source": "print(1)", "kernel": "py"}, "save_as": "c"},
  {"tool": "run_cell", "args": {"id": "$c"}},
  {"text": "done"}]}
```
(`$agent` is the agent cell id; `{"hang": true}` blocks, for interrupt tests; `{"raise": "msg"}` fails the run.)
In pytest, inject `ClaudeAgentExecutor(backend=...)` into `sched.registry._executors["agent:claude"]` (see `tests/agent_support.py`).

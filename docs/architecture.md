# Architecture

How yupyter is put together: where each piece lives, the shapes shared between server and browser, and how
agent cells work. For what we reuse from Jupyter and why, see [vs_jupyter.md](vs_jupyter.md); for the HTTP and
websocket protocol, [yupyter/server/README.md](../yupyter/server/README.md); for the original design,
[plans/poc.md](../plans/poc.md).

## Module map

| Path | Role |
|---|---|
| `yupyter/doc/schema.py` | pycrdt doc schema + helpers (`new_doc`, `insert_cell`, `delete_cell`, `edit_source`, `set_source`, `ordered_cells`, `set_outputs`/`append_output`/`clear_outputs`, `set_exec_state`, `set_kernel_status`). All writers take `origin=`. Server alone writes `exec`/`outputs`. |
| `yupyter/doc/order.py` | fractional index keys (`between(a, b)`); mirrored in `frontend/src/doc.ts`. |
| `yupyter/doc/store.py` | `DocStore` (`open(path)`, `load(path)`, atomic `save(path)` incl. assets dir, debounced autosave, save on shutdown) and `OpenDoc.serve(ws)` (y-websocket sync + awareness relay). |
| `yupyter/doc/markdown_io.py` | `load_markdown(text, assets_dir=None) -> Doc`, `dump_markdown(doc, assets_dir) -> str`. Fences = code cells (`py {#id run=}`; `author` is not persisted), `output`/`error` fences attach via `for=id`, rich outputs live in `<name>.assets/` behind `<!-- yupyter:output for= mime= -->` markers; front matter holds `aliases`. |
| `yupyter/kernels/executor.py` | `Executor` protocol, `RunContext`, `Output`. |
| `yupyter/kernels/registry.py` | `ExecutorRegistry.resolve(name)`, `register_agent("claude", factory)` for `agent:*` names. |
| `yupyter/kernels/jupyter_executor.py` | `JupyterExecutor` (lazy start, iopub -> outputs, interrupt, restart). |
| `yupyter/scheduler.py` | `RunScheduler.run_cell(path, cell_id)`, `interrupt(kernel)`; FIFO queue per executor name; writes exec/outputs/kernel status. |
| `yupyter/config.py` | `~/.config/yupyter/config.toml`; `aliases`, `resolve_alias`. |
| `yupyter/server/app.py` | `create_app(root, token, initial=None, port=)`: one server, many notebooks under `root`; protocol in `yupyter/server/README.md`. `server/paths.py`: `resolve_rel` (root-confined path resolution). |
| `yupyter/cli.py` | `yupyter [FILE\|DIR] --port --ip --no-browser`. |
| `yupyter/agent/context.py` | `render_context(doc, agent_cell_id)`: doc through the agent cell as prompt content blocks (text + images); cells below as one line each. Pure. |
| `yupyter/agent/tools.py` | `Toolbox` (insert/edit/run/read/delete cell, set_kernel, list_kernels) on the live doc, plus `TOOL_SPECS`. Backend-agnostic. |
| `yupyter/agent/executor.py` | `ClaudeAgentExecutor` (registered as `agent:claude`), `AgentStream` (throttled streaming output), re-run replace/keep, `SdkBackend` (Claude Agent SDK), `FakeBackend`. |
| `frontend/src/outputs/agent.tsx` | renderer for `application/vnd.yupyter.agent+json` (events: `thinking`, `text`, `tool`). |
| `frontend/src/doc.ts` | Yjs doc, `WebsocketProvider`, `listCells`, `insertCell`, `runCell`. |
| `frontend/src/magic.ts` | `runWithMagic(id)`: runs a cell, first turning a leading `%%claude` line into an `agent:claude` cell. |
| `frontend/src/files.ts`, `components/FileHeader.tsx`, `components/Drawer.tsx` | save status (ws message 101), rename/save/conflict RPCs, drawer state; header (name, status, external-change banner) and file tree. |
| `frontend/src/kernels.ts` | `useKernels()` (one fetch of `/api/info` `kernels`), `kernelLabel`, `kernelClass` (label color). |
| `frontend/src/outputs/` | one renderer file per mime family; dispatcher in `index.tsx`. |
| `yupyter/doc/presence.py` | `set_agent_presence(open_doc, name, cell_id\|None)` / `clear_agent_presence(open_doc, name)`: server-local awareness for agents (refresh <30s). |
| `yupyter/doc/undo.py` | `track_run(doc, run_id)` before an agent run, write with `origin=run_origin(run_id)` (`"agent-run:<id>"`); `undo_run`/`redo_run(doc, run_id)` (also set the shared `exec.undone` flag on the agent cell; `exec.summary` = what the run changed). `POST /api/undo_run` / `POST /api/redo_run {run_id}` (`server/undo_routes.py`, also `POST /api/save`; `POST /api/clear {cell_id}` in `app.py`); 404 = no history (server restarted). The agent executor tracks every run; the scheduler gives it `origin=run_origin(run_id)`. Server `exec`/`outputs` writes use other origins and are never undone. |
| `frontend/src/commands.ts` | `registerCommand({id, title, run, shortcut?})` adds to the Ctrl/Cmd+K palette. Built-ins in `builtinCommands.ts`. |
| `frontend/src/presence/`, `undo.ts`, `ops.ts`, `keys.ts` | peers/avatars/selection, per-user undo: `structUndo` (`STRUCT_ORIGIN` transactions only) plus a unified `undo()`/`redo()` history that interleaves those with agent runs this client started (`pushRun`, from `magic.ts`; `POST /api/run` takes a client-chosen `run_id`), used by Cmd/Ctrl+Z and the palette, cell ops, command-mode keys (Esc, j/k, a/b, dd, Alt+↑/↓, Enter, Shift+Enter); `insertRelative`/`deleteSelected` take an optional cell id. |
| `frontend/src/dnd.ts` | drag a cell by its head bar to reorder: `cellDragProps(id)` on the head, `useCellDrop()` on the column (drop line, `moveCellTo` in `ops.ts`). |
| `frontend/src/components/` | `App`, `Cell`, `Editor` (CodeMirror + y-codemirror.next; `wrap` prop), `Menu` (dropdown used by the kernel label and the ⋯ cell menu). New cells come from the "+ New cell" button. |

Notes: pycrdt `transaction.origin()` returns `hash(origin)`, not the object. Clients must not write `exec`/`outputs`.

## Awareness shape

Every participant (browser or agent) publishes `{user: {id, name, color, colorLight, kind: "human"|"agent"}, focus: {cellId: string|null}}`.
A browser's `name`/`color` come from the server: each websocket connection is sent a letter and colour unique among live connections (message type 100, see `yupyter/doc/store.py`); a name set by clicking your own avatar (stored in localStorage) overrides the letter. `id` is the stable per-browser id used for cell authorship.
`cursor` is owned by y-codemirror.next (`{anchor, head}`), do not write it. Agents (name `claude`, `kind: "agent"`) use `yupyter.doc.presence`; the UI shows them in the avatar row and as a left-edge marker on the focused cell. Frontend structural edits must use `ydoc.transact(fn, STRUCT_ORIGIN)` to be undoable.

## Agent cells

`%%claude <prompt>` makes a cell with kernel `agent:claude`. Running it starts a fresh Claude Agent SDK
session whose only tools are `Toolbox` (in-process MCP server); it uses your Claude Code login or
`ANTHROPIC_API_KEY`. Optional `[agent]` config: `model`, `max_turns` (40), `timeout_s` (900).
Doc meta: `agent_policy` = `auto`|`propose`, `agent_delete` = `own`|`any`, `rerun` = `replace`|`keep`
(a cell-level `rerun` wins). Agent-made cells carry `created_by_run` (persisted as `run=`; `author="claude"` only in the live doc); the agent
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

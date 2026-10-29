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
node tests/e2e/persist.mjs       # save on autosave/shutdown, reload from .md
node tests/e2e/markdown_cells.mjs  # markdown cells: KaTeX, live remote re-render
node tests/e2e/multiplayer.mjs   # two users: presence, remote cursors, undo, move, palette
```

## Module map

| Path | Role |
|---|---|
| `yupyter/doc/schema.py` | pycrdt doc schema + helpers (`new_doc`, `insert_cell`, `delete_cell`, `edit_source`, `set_source`, `ordered_cells`, `set_outputs`/`append_output`/`clear_outputs`, `set_exec_state`, `set_kernel_status`). All writers take `origin=`. Server alone writes `exec`/`outputs`. |
| `yupyter/doc/order.py` | fractional index keys (`between(a, b)`); mirrored in `frontend/src/doc.ts`. |
| `yupyter/doc/store.py` | `DocStore` (`open(path)`, `load(path)`, atomic `save(path)` incl. assets dir, debounced autosave, save on shutdown) and `OpenDoc.serve(ws)` (y-websocket sync + awareness relay). |
| `yupyter/doc/markdown_io.py` | `load_markdown(text, assets_dir=None) -> Doc`, `dump_markdown(doc, assets_dir) -> str`. Fences = code cells (`py {#id author= run=}`), `output`/`error` fences attach via `for=id`, rich outputs live in `<name>.assets/` behind `<!-- yupyter:output for= mime= -->` markers; front matter holds `aliases`. |
| `yupyter/kernels/executor.py` | `Executor` protocol, `RunContext`, `Output`. |
| `yupyter/kernels/registry.py` | `ExecutorRegistry.resolve(name)`, `register_agent("claude", factory)` for `agent:*` names. |
| `yupyter/kernels/jupyter_executor.py` | `JupyterExecutor` (lazy start, iopub -> outputs, interrupt, restart). |
| `yupyter/scheduler.py` | `RunScheduler.run_cell(path, cell_id)`, `interrupt(kernel)`; FIFO queue per executor name; writes exec/outputs/kernel status. |
| `yupyter/config.py` | `~/.config/yupyter/config.toml`; `aliases`, `resolve_alias`. |
| `yupyter/server/app.py` | `create_app(notebook_path, token, port=)`; protocol in `yupyter/server/README.md`. |
| `yupyter/cli.py` | `yupyter [PATH] --port --ip --no-browser`. |
| `frontend/src/doc.ts` | Yjs doc, `WebsocketProvider`, `listCells`, `insertCell`, `runCell`. |
| `frontend/src/magic.ts` | `parsePrompt()` seam for `%%magic` handling. |
| `frontend/src/outputs/` | one renderer file per mime family; dispatcher in `index.tsx`. |
| `yupyter/doc/presence.py` | `set_agent_presence(open_doc, name, cell_id\|None)` / `clear_agent_presence(open_doc, name)`: server-local awareness for agents (refresh <30s). |
| `yupyter/doc/undo.py` | `track_run(doc, run_id)` before an agent run, write with `origin=run_origin(run_id)` (`"agent-run:<id>"`); `undo_run(doc, run_id)`. `POST /api/undo_run {run_id}` (`server/undo_routes.py`, also `POST /api/save`). Server `exec`/`outputs` writes use other origins and are never undone. |
| `frontend/src/commands.ts` | `registerCommand({id, title, run, shortcut?})` adds to the Ctrl/Cmd+K palette. Built-ins in `builtinCommands.ts`. |
| `frontend/src/presence/`, `undo.ts`, `ops.ts`, `keys.ts` | peers/avatars/selection, per-user structural undo (`STRUCT_ORIGIN` transactions only), cell ops, command-mode keys (Esc, j/k, a/b, dd, Alt+↑/↓, Enter, Shift+Enter). |
| `frontend/src/components/` | `App`, `Cell`, `Editor` (CodeMirror + y-codemirror.next), `PromptBar`. |

Notes: pycrdt `transaction.origin()` returns `hash(origin)`, not the object. Clients must not write `exec`/`outputs`.

## Awareness shape

Every participant (browser or agent) publishes `{user: {id, name, color, colorLight, kind: "human"|"agent"}, focus: {cellId: string|null}}`.
`cursor` is owned by y-codemirror.next (`{anchor, head}`), do not write it. Agents (name `claude`, `kind: "agent"`) use `yupyter.doc.presence`; the UI shows them in the avatar row and as a left-edge marker on the focused cell. Frontend structural edits must use `ydoc.transact(fn, STRUCT_ORIGIN)` to be undoable.

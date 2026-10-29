# yupyter — proof of concept plan

A decluttered, "zen mode" notebook built on the Jupyter kernel ecosystem, with
markdown documents, several kernels per notebook, multiplayer editing, and an AI
agent that takes part in the notebook the way a kernel does.

## Target experience

```
$ pip install yupyter
$ yupyter analysis.md          # starts local server, opens browser
```

In the browser:

1. Type `2 + 2⏎` in the prompt bar. A python cell appears, runs, and shows `4`.
2. Type `%%claude please graph y=2+x⏎`. An agent cell appears. Claude's
   thinking streams in as that cell's output. It then inserts a python cell
   with matplotlib code, runs it, and the plot shows up below.
3. Open the same URL in a second tab: both tabs edit live, with cursors.

## Scope of the POC

- **Local only.** One CLI-launched server per user, bound to `127.0.0.1`,
  opens the browser with a token URL (same model as `jupyter notebook`).
  "Multiplayer" means several tabs/windows plus the agent, all on one
  machine. LAN/hosted sharing is a later concern.
- **Easy pip install.** One wheel with the prebuilt frontend bundled in it.
  No node needed by users. Python works out of the box (depends on
  `ipykernel`); other languages work if their kernelspec is installed.
- **Claude through the user's subscription.** Uses the Claude Agent SDK,
  which drives the Claude Code login. `ANTHROPIC_API_KEY` works too, as a
  fallback.

Out of scope for now: auth beyond the token, sandboxing, ipywidgets, hosted
deployment, `.ipynb` import (nice later).

---

## 1. Core idea: the agent is a kernel

The server has one execution interface:

```python
class Executor(Protocol):
    async def execute(self, cell: CellRef, ctx: RunContext) -> AsyncIterator[Output]: ...
    async def interrupt(self) -> None: ...
```

- `JupyterExecutor` wraps a `jupyter_client` kernel.
- `AgentExecutor` wraps a Claude agent loop.

An agent cell joins the same run queue, has the same busy/idle state, streams
into the same output area, and can be interrupted, re-run and undone like any
other cell.

## 2. Cell magics: choosing kernels and agents

Follows the Jupyter convention: `%%name` on the first line of a cell decides
how the whole cell runs (as `%%bash` / `%%R` do in IPython).

| Input | Result |
|---|---|
| `2 + 2` | code cell, author's default kernel |
| `%%py` + code | python cell |
| `%%r` + code | R cell |
| `%%claude ...` | agent cell (prompt = rest of cell) |
| `%%md` / `> text` | markdown cell |
| `%%py:scratch` | python cell in a separate instance named `scratch` (later) |

- The magic line is **consumed when the cell is created**: it sets the cell's
  `kernel` field and is removed from the source. The cell header shows a
  kernel chip you can click to change it. That keeps cell source as pure code
  and avoids clashing with IPython's own `%%` magics. Unknown names are left
  in the source and go to the kernel unchanged.
- Line form `%claude ...` in the prompt bar is shorthand for a one-line agent
  cell.
- In the `.md` file the same information is the code fence info string:
  ` ```py `, ` ```r `, ` ```claude `.

### Alias map

A global map from short names to executors, saved in user config
(`~/.config/yupyter/config.toml`) and overridable per notebook (front matter):

```toml
[aliases]
py     = "python3"        # kernelspec name
r      = "ir"
sh     = "bash"
claude = "agent:claude"
```

Defaults are generated from installed kernelspecs (via their `language`
field), so `py` / `r` / `julia` work without config.

### Default kernel per user

Each user (browser identity, stored locally) has a default kernel, initially
`py`. A cell created without a magic gets the author's default, **resolved
and stored on the cell**. Changing your default later never changes existing
cells.

## 3. Kernel pool

- Kernels are keyed by `(kernelspec, instance)`; instance defaults to
  `"default"`.
- **Same language shares one kernel**: all `py` cells in a notebook share
  state, as in Jupyter.
- **Lazy start**: a kernel starts the first time a cell needs it. A
  python-only notebook never starts R.
- `instance` (`%%py:scratch`) leaves room for isolated kernels of the same
  language later; for the POC only `default` is needed.
- Uses `jupyter_client.AsyncMultiKernelManager` + `KernelSpecManager`.
- One FIFO run queue per kernel. Different kernels can run in parallel.
- Kernel status (`starting`/`idle`/`busy`/`dead`) is published in the shared
  doc so all clients show it.

## 4. Document model and merging

**Use a structured CRDT, with markdown as the file format.** A single shared
markdown text would force re-parsing cell identity, kernel, and outputs from
text being edited at the same time. That is fragile.

### Merge engine: Yjs (pycrdt on the server)

- **pycrdt** (Python bindings for yrs, the Rust Yjs port) makes the server a
  full participant in the doc: outputs and agent edits go through the same
  CRDT as the browsers. Jupyter's own collaboration stack uses it.
- **y-codemirror.next** gives collaborative CodeMirror 6 editing with remote
  cursors.
- **Awareness** carries presence, including Claude's cursor.
- **Y.UndoManager with origins**: each agent run uses its own origin, so
  "undo Claude's last run" is one undo step.
- Alternative to watch: **Loro** (native movable list, better history). Keep
  doc access behind a thin `DocStore` layer so switching later is possible.

### Schema

```
doc
├─ meta: Y.Map             # title, alias overrides
├─ cells: Y.Map<cellId, Y.Map>
│    ├─ order: string      # fractional index; avoids delete+insert on move
│    ├─ kind: "code" | "markdown"
│    ├─ kernel: "python3" | "ir" | "agent:claude" | ...
│    ├─ source: Y.Text
│    ├─ author: userId
│    ├─ created_by_run: runId | null    # provenance for agent-made cells
│    ├─ exec: Y.Map        # state, count, started/finished, runId
│    └─ outputs: Y.Array   # nbformat-style mime bundles; server-write only
└─ kernels: Y.Map          # live kernel status
```

Rules:

- **Only the server writes `outputs` and `exec`.** Clients request runs over
  an RPC message; the server runs and writes results. No output conflicts.
- Outputs belong to their cell (a re-run replaces them) but **display as
  their own block**, so `2 + 2` looks like it adds a `4` cell.
- Cell order uses fractional index keys, not array position, because Yjs has
  no native move. Moving a cell then never loses concurrent edits inside it.

### Persistence: `.md` on disk

````markdown
---
yupyter: 1
---

# Analysis

```py {#a3f9}
2 + 2
```

```output {for=a3f9}
4
```

```claude {#b71c}
please graph y=2+x
```
````

- Cell ids live in the fence info string, so reloads keep identity.
- Text outputs go inline as ` ```output ` blocks; images and large outputs go
  to `analysis.assets/` and are linked. Clean git diffs.
- A plain markdown file with no ids still opens: fences become code cells,
  everything else becomes markdown cells.
- While a document is open, the in-memory CRDT is the source of truth. It is
  saved to `.md` on a debounce and on shutdown. Optionally the CRDT update log
  is kept in `~/.cache/yupyter/` for crash recovery.

## 5. Agent design

### Runtime: Claude Agent SDK (subscription auth)

Most users have a Claude subscription rather than an API key, so the agent
runs through the **Claude Agent SDK** (`claude-agent-sdk`), which uses the
local Claude Code login. If no login is found, show a one-line hint to run
`claude` once to log in, or set `ANTHROPIC_API_KEY`.

Configured to give yupyter full control of context:

- **Fresh `query()` per agent cell run**, with no session resume. The document
  itself is the memory.
- **Custom system prompt** that replaces the default coding-agent prompt.
- **Only yupyter's tools are allowed** (an in-process SDK MCP server); the
  built-in file/shell tools are off by default. Code runs through notebook
  kernels, where the user can see it.
- No user/project settings loaded (`setting_sources=[]`) so behaviour is
  predictable.

### Context = the document

When agent cell `C` runs:

1. Render the doc **from the top through `C`** as markdown: cell ids,
   kernels, outputs (truncated; images as image blocks; errors kept). Cells
   below `C` are summarised by id + first line so the agent knows they exist.
2. That rendering plus `C`'s prompt is the user message.
3. Editing an earlier cell and re-running means the agent sees the edited
   version. There is no hidden transcript. That gives "resume from here"
   rather than "append".
4. Prompt caching lines up with this: the cached prefix is the doc prefix, and
   an edit only invalidates what follows it.

### Tools

| Tool | Notes |
|---|---|
| `insert_cell(after, kernel, source, kind)` | returns new cell id |
| `edit_cell(id, old, new)` | search/replace on the shared text so it merges with concurrent human typing; never a full overwrite |
| `run_cell(id, timeout)` | queues on that cell's kernel, waits, returns outputs |
| `read_cell(id)` / `read_outputs(id)` | for cells beyond the rendered context or after long runs |
| `delete_cell(id)` | only cells created by this agent run, unless the user allows more |
| `set_kernel(id, alias)` / `list_kernels()` | |

All edits are applied as CRDT ops under the run's origin, so humans see them
live with a "claude" cursor, and the whole run undoes as one step.

### Output of an agent cell

- Thinking and text stream into the agent cell's output area (thinking in a
  collapsible block, final answer shown).
- Tool calls appear as compact lines ("inserted cell `c12` (py)", "ran
  `c12` ✓").

### Provenance and re-run

Cells the agent creates record `created_by_run`. Re-running an agent cell
offers **replace** (remove that run's cells, regenerate) or **keep**
(regenerate below). Replace is the default, which gives the in-place
overwrite behaviour.

### Run policy

Per notebook: `auto` (agent may run cells; default for the POC, as it is
local) or `propose` (agent-created cells wait for a human ▶). Interrupt on the
agent cell cancels the loop. Turn and time limits per run.

### Later: external agents via MCP

Expose the same tool layer at `/mcp` so a Claude Code session started in a
terminal can join a notebook as a participant. The tools are written once and
used by both modes. Event-driven behaviour ("offer a fix when a cell errors")
goes here too, opt-in and debounced.

## 6. Architecture

```
browser tab(s) ──ws: y-sync + rpc──┐
                                   │   yupyter server (single process)
                                   ├─  ├─ DocStore (pycrdt doc per notebook, .md load/save)
                                   │   ├─ RunScheduler (queue per kernel)
                                   │   ├─ KernelPool ── jupyter_client ── python3, ir, …
                                   │   └─ AgentExecutor ── claude-agent-sdk ── Claude Code login
(later) Claude Code ──MCP──────────┘
```

### Stack

| Layer | Choice |
|---|---|
| Server | Python 3.10+, Starlette + uvicorn, `jupyter_client`, `ipykernel`, `pycrdt` (+ its websocket sync), `claude-agent-sdk` |
| Frontend | TypeScript + Vite, CodeMirror 6, `y-codemirror.next`, `yjs`, markdown-it, KaTeX |
| Outputs | Custom renderers first: ANSI text, HTML, PNG/JPEG/SVG, markdown, LaTeX, Plotly/Vega JSON. `@jupyterlab/rendermime` later if widgets are wanted (it pulls in Lumino) |
| UI framework | Small: Solid or Preact. Keep the bundle light |
| Packaging | hatchling; a build hook runs the Vite build and puts `dist/` in the wheel under `yupyter/static/`. Releases are built in CI so users never need node |

### UI ("zen mode")

- One centred column. No sidebars, no toolbars by default.
- Prompt bar pinned at the bottom: `Enter` creates+runs, `Shift+Enter` adds a
  newline. `%%magic` on the first line picks the kernel/agent.
- Cells are editable in place. A small kernel chip and run state show on
  hover. Outputs show as their own blocks.
- `Ctrl/Cmd+K` command palette for everything else (kernels, restart,
  defaults, save, export).
- Presence: coloured cursors and a small avatar row (tabs + claude).

## 7. CLI

```
yupyter [PATH]           # open a .md notebook (created if missing); default: new untitled
  --port N               # default: first free from 8890
  --no-browser
  --ip 127.0.0.1
```

Prints a token URL like Jupyter does and opens it. Ctrl+C saves and shuts down
kernels.

## 8. Milestones

1. **Skeleton**: package layout, CLI, Starlette server serving the bundled
   frontend, token auth, browser opens.
2. **Single kernel**: pycrdt doc schema, y-sync over websocket, prompt bar,
   run a python cell, outputs written to the doc and rendered. `2 + 2` → `4`.
3. **Markdown I/O**: load/save `.md` with ids and outputs; markdown cells
   rendered.
4. **Kernel pool**: alias map, `%%py` / `%%r` magics, lazy start, kernel chip,
   per-user default kernel, interrupt/restart.
5. **Multiplayer polish**: awareness cursors, per-user undo, two tabs editing
   live.
6. **Agent**: `AgentExecutor` on the Agent SDK, doc-to-context renderer, tool
   set, streamed thinking, provenance + re-run replace. "Graph y=2+x" demo
   works end to end.
7. **Release**: wheel with bundled frontend, `pip install yupyter` on a clean
   machine, README with a GIF.

Later: `/mcp` endpoint for external agents, scratch kernel instances,
`.ipynb` import/export, rich output renderers / widgets, LAN sharing.

## 9. Open questions

- Prompt-bar default for plain text: code in the default kernel (current
  plan), or markdown unless it looks like code?
- Should agent cells see cells *below* them in full, or only a summary
  (current plan)?
- Does the agent get the built-in Bash/file tools as an opt-in setting
  (useful for `pip install` and reading data files), or should that always go
  through a `%%sh` kernel?
- How are per-user identities picked locally: a name prompt on first visit,
  or the OS username plus a tab colour?

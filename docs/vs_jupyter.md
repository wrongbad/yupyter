# yupyter vs Jupyter: what we reuse, what we rebuilt, and why

yupyter is not a Jupyter extension. It uses Jupyter's **kernel layer** and
rebuilds everything above it: server, document model, file format,
collaboration, scheduling and frontend. This page explains where the line
sits and why each rebuilt piece is ours.

Design background is in [plans/poc.md](../plans/poc.md); module details are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## At a glance

| Area | Jupyter stack | yupyter |
|---|---|---|
| Kernels | `jupyter_client` + kernelspecs | **reused** (`JupyterExecutor`) |
| Python kernel | `ipykernel` | **reused** (a dependency) |
| Output format | nbformat output dicts / MIME bundles | **reused as a shape** (we don't import `nbformat`) |
| CRDT library | `pycrdt` / Yjs | **reused** (the same library, used directly) |
| Server | Jupyter Server (Tornado) | rebuilt: Starlette app |
| File format | `.ipynb` JSON | rebuilt: Markdown |
| Document model | `jupyter_ydoc` `YNotebook` | rebuilt: our own pycrdt schema |
| Collaboration | `jupyter-collaboration` | rebuilt: our own sync, presence, undo |
| Execution | Jupyter Server kernel/session managers | rebuilt: `RunScheduler` and `ExecutorRegistry` |
| Frontend | JupyterLab / Lumino | rebuilt: Preact + CodeMirror 6 |
| Output rendering | `@jupyterlab/rendermime` | rebuilt: small per-MIME renderers |
| AI | none built in | new: `agent:claude` executor |

## What we reuse

### Kernels: `jupyter_client` and kernelspecs

[`yupyter/kernels/jupyter_executor.py`](../yupyter/kernels/jupyter_executor.py)
(about 100 lines) is the whole interface to Jupyter. It uses
`AsyncKernelManager` to start, interrupt, restart and shut down a kernel. It
sends `execute` requests and turns the iopub messages (`stream`,
`display_data`, `execute_result`, `error`, `status: idle`) into output dicts.
`KernelSpecManager` lists the installed kernels for the agent's
`list_kernels` tool.

So any installed Jupyter kernel works unchanged: `ipykernel`, IRkernel, IJulia,
bash kernels and so on. This is the most valuable part of the Jupyter
ecosystem, and there was no reason to touch it.

### The output format

Outputs are stored as nbformat-style dicts (see the comment in
[`yupyter/kernels/executor.py`](../yupyter/kernels/executor.py)). Kernels
already produce that shape, it is well understood, and it keeps `.ipynb`
import/export cheap if we add it later. We copy the shape but never import
the `nbformat` library.

### The CRDT: pycrdt / Yjs

The server uses `pycrdt` (Python bindings for yrs, the Rust Yjs port). The
browser uses `yjs`, `y-websocket` and `y-codemirror.next`. This is the same CRDT
family jupyter-collaboration is built on. We reuse the **engine** and the
**wire protocol**: the server speaks the standard y-websocket protocol, so the
browser runs `WebsocketProvider` unchanged. We do not reuse Jupyter's
**document schema or server extension** built on top of it (see below).

## What we rebuilt, and why

### Collaboration engine (instead of jupyter-collaboration)

jupyter-collaboration is a Jupyter Server extension. It syncs a `jupyter_ydoc`
`YNotebook` (a Yjs mirror of the `.ipynb` structure) between JupyterLab
clients and persists it back to `.ipynb`. Adopting it would have meant
adopting Jupyter Server, the `.ipynb` model and, in practice, JupyterLab's
frontend. Every core feature of yupyter cuts against one of those:

1. **The server must be a full writer, not just a relay.** In yupyter, only the
   server writes `outputs` and `exec` ([`schema.py`](../yupyter/doc/schema.py)
   rules). Clients ask for runs over HTTP, and the scheduler writes results
   into the CRDT. Two tabs therefore never race on outputs, and a run keeps
   going if the tab that started it closes. Agent edits are server-side CRDT
   writes too. In the stock JupyterLab model the frontend that runs a cell is
   the one that writes its outputs into the shared doc. Server-side execution
   needs extra pieces, which brings more Jupyter machinery into play, not less.

2. **One kernel per cell, not one per notebook.** `.ipynb`, and therefore
   `YNotebook`, keep a single kernelspec in notebook metadata. Our cells each
   carry a `kernel` field (`python3`, `ir`, `agent:claude`, ...), and several
   kernels live in one document. The `.ipynb`-shaped schema has nowhere natural
   to put this.

3. **Moves without losing edits.** Yjs has no native move. `YNotebook` stores
   cells in an array, so moving a cell is delete+insert, and a concurrent edit
   inside the moved cell can be lost. Our schema stores `cells` as a map keyed
   by id, with a fractional-index `order` string
   ([`order.py`](../yupyter/doc/order.py), mirrored in `frontend/src/doc.ts`).
   A move changes one string and never touches the cell's text.

4. **Provenance and per-run undo for agents.** Agent-created cells carry
   `author` and `created_by_run`. Every agent write uses the origin
   `agent-run:<id>`, so [`undo.py`](../yupyter/doc/undo.py) can undo a whole
   Claude run in one step and re-run can "replace" exactly the cells that run
   made. That needs schema fields and origin discipline that the stock schema
   doesn't have.

5. **Agents as participants.** Claude has no websocket. It still appears in
   the avatar row and on the cell it's working on, because
   [`presence.py`](../yupyter/doc/presence.py) injects server-local awareness
   states with a stable pseudo client id. Owning the awareness relay made that
   simple.

6. **Markdown persistence.** jupyter-collaboration's save path ends in
   `.ipynb`. Ours ends in [`markdown_io.py`](../yupyter/doc/markdown_io.py)
   (see the next section).

7. **Size and dependencies.** The goal is `pip install yupyter && yupyter
   notes.md`: one process, one wheel, a light bundle. Our sync layer is
   [`store.py`](../yupyter/doc/store.py) (about 275 lines) plus a 290-line
   schema. That costs less than carrying Jupyter Server, its extension system
   and its file-ID service just to sync one document.

What we gave up: interop with JupyterLab clients, and the work the Jupyter
team has put into edge cases at scale (many users, rooms, out-of-band file
changes). Our current server has one room per process and is local-only, so
those don't bite yet. The plan keeps doc access behind `DocStore` so the
engine could change later (Loro is noted as an alternative to watch).

**On the record:** plans/poc.md never weighs jupyter-collaboration against a
custom build explicitly. It specifies the schema, the server-only output rule
and Markdown persistence as requirements. The reasons above are why those
requirements rule jupyter-collaboration out, worked out from the plan and the
code rather than taken from a recorded decision.

### File format: Markdown instead of `.ipynb`

A notebook is a `.md` file. Code cells are fences whose info string holds the
kernel alias and cell id (` ```py {#a3f9} `). Text outputs are ` ```output
{for=a3f9} ` blocks, and images go to `<name>.assets/` behind a marker comment.
Why:

- **Readable and diffable.** `.ipynb` is JSON with base64 images and
  execution counts inline, so git diffs are noisy. Markdown reads fine on
  GitHub and in any editor.
- **Any Markdown file opens.** A plain `.md` with no ids still loads: fences
  become code cells and prose becomes markdown cells.
- **Identity survives reloads.** Ids in the fence info string let the CRDT
  doc, agent provenance and `for=` output links survive a save/load round
  trip.

The CRDT document, not the text, is the source of truth while a notebook is
open. A single shared Markdown text would mean re-parsing cell identity,
kernel and outputs from text that several people are editing at once. The file
is written on a debounce and on shutdown.

### Server: Starlette instead of Jupyter Server

[`yupyter/server/app.py`](../yupyter/server/app.py) is about 150 lines:
static frontend, token auth (same `?token=` model as `jupyter notebook`), the
y-websocket endpoint and a small JSON run API
([protocol](../yupyter/server/README.md)). We need no contents API,
sessions API, terminals, extension loader or multi-user config. Jupyter Server
offers all of that, but none of it serves a single-document, local-only tool.

### Execution: our own scheduler and registry

Jupyter Server's kernel and session managers assume one kernel per notebook.
We need:

- **An `Executor` interface that both kernels and agents implement**
  ([`kernels/executor.py`](../yupyter/kernels/executor.py)). `JupyterExecutor`
  and `ClaudeAgentExecutor` look the same to everything above them, so an
  agent cell queues, streams output, shows busy/idle, and can be interrupted
  like any other cell. This is the central design idea, and it's why execution
  had to be ours.
- **One FIFO queue per kernel, kernels in parallel**
  ([`scheduler.py`](../yupyter/scheduler.py)). An agent cell can be running
  and calling `run_cell` on a Python cell at the same time without
  deadlocking (the toolbox has a guard for this).
- **Lazy start and aliases** ([`registry.py`](../yupyter/kernels/registry.py),
  [`config.py`](../yupyter/config.py)). `py` resolves to `python3`, and a
  kernel only starts when a cell first needs it.
- **The scheduler as the only writer of `exec` and `outputs`**, which is what
  makes the collaboration rule above hold.

### Frontend: Preact + CodeMirror instead of JupyterLab

The UI is meant to be a single-column "zen mode" page: a "+ New cell" button at the
bottom, no sidebars or toolbars, Ctrl/Cmd+K for everything else. JupyterLab is
an IDE built on Lumino with a large plugin system and bundle. Stripping it
down to one column would have been more work than building the page, and its
notebook model is the `.ipynb` one we don't use.

The stack is Preact, CodeMirror 6, `y-codemirror.next` for collaborative
editing and remote cursors, markdown-it and KaTeX. Cells, command-mode keys,
the palette, per-user structural undo and presence are in
[`frontend/src/`](../frontend/src/).

### Output rendering: our own renderers instead of rendermime

[`frontend/src/outputs/`](../frontend/src/outputs/) has one small renderer
per MIME family: text with ANSI colours, HTML, images, errors, and
`application/vnd.yupyter.agent+json` for agent runs (thinking, text and tool
events). `@jupyterlab/rendermime` would pull in Lumino. The plan leaves it as an
option if widget support is ever wanted.

### Claude as a kernel (new, no Jupyter equivalent)

[`yupyter/agent/`](../yupyter/agent/) implements `agent:claude` on the Claude
Agent SDK. Each run is a fresh session. Its context is the notebook rendered
from the top through the agent cell, so the document itself is the memory.
Its only tools are notebook operations (insert, edit, run, read, delete cell),
applied as CRDT writes under the run's origin. This depends on the custom
schema (provenance, origins) and on the scheduler (agent runs queue like
kernel runs), which is a large part of why those pieces are ours.

## Consequences of the split

- **Works:** any Jupyter kernel and its display outputs (matplotlib, pandas
  HTML, images and so on).
- **Doesn't work:** Jupyter Server and JupyterLab extensions, and
  **ipywidgets**: `JupyterExecutor` ignores `comm_*` messages, and there is no
  widget manager in the frontend. Widgets are out of scope in the plan.
- **Not yet:** `.ipynb` import/export (planned; the shared output shape makes
  it straightforward), multiple rooms per server, and sharing beyond
  localhost.

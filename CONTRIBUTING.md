# Contributing

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
YUPYTER_SKIP_FRONTEND=1 pip install -e '.[dev]'   # skip the frontend build; we do it ourselves below
cd frontend && npm ci && npm run build            # outputs to yupyter/static/ (gitignored)
```

Run: `yupyter /tmp/x.md --no-browser` (prints a `?token=` URL).
Frontend dev with hot reload: `cd frontend && npm run dev` (proxies `/api` and `/ws` to a server on :8890).

## Tests

```bash
pytest                               # schema, markdown round-trip, scheduler on a real ipykernel, server, sync
cd frontend && npx tsc --noEmit      # typecheck
```

Browser end-to-end tests need a built frontend and Playwright. Install it once in the repo root
(`node_modules/` is gitignored), or point `PW_CHROMIUM` at an existing Chromium:

```bash
npm i --no-save playwright && npx playwright install chromium
node tests/e2e/smoke.mjs             # basic editing and running
node tests/e2e/persist.mjs           # autosave / shutdown save, reload from .md
node tests/e2e/markdown_cells.mjs    # markdown cells: KaTeX, live remote re-render
node tests/e2e/multiplayer.mjs       # two users: presence, remote cursors, undo, move, palette
node tests/e2e/files.mjs             # header and save status, Ctrl+S, rename, external edits, drawer
node tests/e2e/agent.mjs             # agent cell end to end with a scripted fake backend (no Claude needed)
```

## Ground rules

- Only the server writes a cell's `exec` and `outputs`; the browser asks via an API route.
- Browser structural edits (insert, delete, move, change type) go through `ydoc.transact(fn, STRUCT_ORIGIN)`,
  so they're undoable per user.
- The `.md` format must stay readable and round-trip exactly; format changes come with a test in
  `tests/test_markdown_io.py`.
- UI changes get an e2e check where practical.

Where things live, the awareness shape and agent internals: [docs/architecture.md](docs/architecture.md).

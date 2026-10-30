# Server protocol

All endpoints require the token: `?token=...` on the first page load sets a
cookie (`yupyter_token_<port>`); later HTTP and websocket requests use the cookie
(a websocket may also pass `?token=`). Websocket auth failure closes with code 4401.

## Root and paths

One server serves every notebook under a **root** directory (`create_app(root, token, initial=None)`;
CLI: `yupyter notes.md` -> root = cwd, opens `notes.md`; `yupyter some/dir` or `yupyter` -> root is that
dir / cwd, nothing opened). Every route below takes the notebook as a path relative to the root
(`{path}`, posix, `.md` only). Paths are resolved (symlinks followed) and must stay inside the root:
absolute paths, `..` and symlink escapes give `403`; dotfiles, `*.assets`, `*.tmp`, `*.old` give `404`.
The frontend lives at `/n/<path>` (the server returns `index.html` for it); `/` is the home page.
Each notebook has its own docs, run queues and kernels (one kernel per notebook and kernel name).

## Doc sync: `GET /ws/{path}` (websocket, binary)

Standard y-websocket protocol (message byte 0: `0` sync, `1` awareness,
`3` queryAwareness), so the browser uses `y-websocket`'s `WebsocketProvider`
unchanged (room = the notebook path). Bad paths close with code 4403. The server holds a real
pycrdt doc, writes outputs / exec state / kernel status into it, and relays awareness between clients.
Clients must not write `exec` or `outputs`.

Server -> client extensions (var-string JSON payloads):

- `100` on connect: `{"name": "A", "color": "#4c8dd6"}`, a letter and colour unique among live
  connections, which the client publishes as its awareness `user`.
- `101` on connect and whenever it changes: file status
  `{"state": "saved"|"saving"|"error"|"conflict", "path", "saved_at", "error"}`. A side channel, never
  stored in the Y doc. `path` changes after a rename (clients should reload at the new URL).

## File lifecycle

- Autosave is debounced (2s). `saving` = unsaved changes pending; `error` = last write failed (retried
  on the next edit or explicit save).
- External edits: the server polls each open file's (mtime, size); its own writes are recognised by
  content. A changed file with no pending edits is reloaded into the live doc as one transaction. With
  pending edits the file enters `conflict` and saving is paused until resolved.
- `POST /api/save/{path}` flush now -> `{"ok", "status"}` (`500 {"error"}` if the write fails).
- `POST /api/resolve/{path} {"action": "reload"|"keep"}` settle a conflict (take the disk version /
  overwrite it with ours).
- `POST /api/rename/{path} {"to": "dir/name"}` moves the notebook and its `<stem>.assets/` (re-rendered
  at the new location so asset links are rewritten); `409` on collision, running cells or an unresolved
  conflict; the kernel moves with it.

## Run RPC (JSON over HTTP)

- `POST /api/run/{path} {"cell_id", "author"?, "wait"?}` -> `202 {"queued": true}`;
  with `"wait": true` -> `200 {"cell_id", "outputs": [...]}` after completion.
  Clients create the cell via the doc first; the server waits up to 5s for it
  to arrive over the sync channel. Results appear in the doc, not the response.
- `POST /api/interrupt/{path} {"kernel"}` -> `{"ok": true}`; interrupts the running
  cell on that notebook's kernel and drops its queued jobs.
- `POST /api/undo_run/{path} {"run_id"}` -> `{run_id, undone}` reverts one agent run;
  `POST /api/redo_run/{path} {"run_id"}` -> `{run_id, redone}` re-applies it. 404 if the
  server has no history for the run (it is in memory only). Whether a run is currently
  undone is shared in the doc as the agent cell's `exec.undone`. `/api/run` also accepts
  an optional `run_id` (`[0-9a-zA-Z_-]{1,32}`) so a client can name the run it starts.
- `GET /api/info` -> `{root, initial, aliases, default_kernel, kernels}`;
  `GET /api/info/{path}` adds `{path, name, room}`.

## Files

- `GET /api/tree?dir=<rel>` -> `{dir, root, entries: [{name, path, type: dir|notebook|markdown|other,
  open_by?: [{name, color}], kernel_running?}]}`; dirs first. `notebook` = `.md` with `yupyter:` front
  matter, `markdown` = any other `.md`. Symlinks leaving the root are omitted.
- `POST /api/new {"dir", "name"}` -> `201 {"path"}`: an empty notebook (`.md` appended); `409` if it exists.

Known limitation: HTTP and websocket are separate connections, so a run
request sent immediately after a keystroke could (rarely) be processed before
that keystroke's CRDT update.

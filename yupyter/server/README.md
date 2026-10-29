# Server protocol

All endpoints require the token: `?token=...` on the first page load sets a
cookie (`yupyter_token_<port>`); later HTTP and websocket requests use the cookie
(a websocket may also pass `?token=`). Websocket auth failure closes with code 4401.

## Doc sync: `GET /ws/<room>` (websocket, binary)

Standard y-websocket protocol (message byte 0: `0` sync, `1` awareness,
`3` queryAwareness), so the browser uses `y-websocket`'s `WebsocketProvider`
unchanged. `<room>` is currently ignored (one notebook per server; the
frontend uses `main`). The server holds a real pycrdt doc, writes outputs /
exec state / kernel status into it, and relays awareness between clients.
Clients must not write `exec` or `outputs`.

## Run RPC (JSON over HTTP)

- `POST /api/run {"cell_id", "author"?, "wait"?}` -> `202 {"queued": true}`;
  with `"wait": true` -> `200 {"cell_id", "outputs": [...]}` after completion.
  Clients create the cell via the doc first; the server waits up to 5s for it
  to arrive over the sync channel. Results appear in the doc, not the response.
- `POST /api/interrupt {"kernel"}` -> `{"ok": true}`; interrupts the running
  cell on that kernel and drops its queued jobs.
- `GET /api/info` -> `{path, name, room, aliases, default_kernel}`.

Known limitation: HTTP and websocket are separate connections, so a run
request sent immediately after a keystroke could (rarely) be processed before
that keystroke's CRDT update.

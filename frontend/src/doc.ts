// Shared Yjs document + websocket provider + typed accessors for the schema
// in plans/poc.md section 4. Clients may create/edit/delete cells but must
// never write `exec` or `outputs` (the server does).
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";
import { getUser, userState } from "./user";

export type Output = Record<string, any>;

export interface ExecState {
  state: "idle" | "queued" | "running" | "done" | "error";
  count: number | null;
  runId?: string;
  started?: number | null;
  finished?: number | null;
}

export interface CellView {
  id: string;
  order: string;
  kind: "code" | "markdown";
  kernel: string;
  author: string | null;
  createdByRun: string | null;
  ymap: Y.Map<any>;
  source: Y.Text;
  exec: ExecState;
  outputs: Output[];
}

export const ydoc = new Y.Doc();
export const ycells = ydoc.getMap<Y.Map<any>>("cells");
export const ymeta = ydoc.getMap<any>("meta");
export const ykernels = ydoc.getMap<any>("kernels");

const proto = location.protocol === "https:" ? "wss:" : "ws:";
export const provider = new WebsocketProvider(
  `${proto}//${location.host}/ws`,
  "main",
  ydoc,
);
export const awareness = provider.awareness;

const user = getUser();
awareness.setLocalStateField("user", userState());

/** Origin of local structural edits (insert/delete/move cell); the structural UndoManager tracks only this. */
export const STRUCT_ORIGIN = "struct";

export function listCells(): CellView[] {
  const out: CellView[] = [];
  ycells.forEach((c, id) => {
    const ex = (c.get("exec") as Y.Map<any> | undefined)?.toJSON() ?? {};
    const outs = (c.get("outputs") as Y.Array<Output> | undefined)?.toJSON() ?? [];
    out.push({
      id,
      order: String(c.get("order") ?? ""),
      kind: c.get("kind") ?? "code",
      kernel: c.get("kernel") ?? "python3",
      author: c.get("author") ?? null,
      createdByRun: c.get("created_by_run") ?? null,
      ymap: c,
      source: c.get("source") as Y.Text,
      exec: { state: "idle", count: null, ...ex },
      outputs: outs,
    });
  });
  out.sort((a, b) => (a.order < b.order ? -1 : a.order > b.order ? 1 : a.id < b.id ? -1 : 1));
  return out;
}

// --- fractional indexing (must match yupyter/doc/order.py) ------------------
const DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
function midpoint(a: string, b: string | null): string {
  if (b !== null && a >= b) throw new Error(`${a} >= ${b}`);
  if (b !== null) {
    let n = 0;
    while ((a[n] ?? "0") === b[n]) n++;
    if (n > 0) return b.slice(0, n) + midpoint(a.slice(n), b.slice(n));
  }
  const da = a ? DIGITS.indexOf(a[0]) : 0;
  const db = b ? DIGITS.indexOf(b[0]) : DIGITS.length;
  if (db - da > 1) return DIGITS[Math.round(0.5 * (da + db))];
  if (b && b.length > 1) return b.slice(0, 1);
  return DIGITS[da] + midpoint(a.slice(1), null);
}
export function orderBetween(a: string | null, b: string | null): string {
  return midpoint(a ?? "", b || null);
}

export function newCellId(): string {
  return Math.random().toString(16).slice(2, 8);
}

/** Create a cell (in one transaction) after `afterId` (default: at the end). */
export function insertCell(opts: {
  kind?: "code" | "markdown";
  kernel: string;
  source: string;
  afterId?: string | null;
  beforeId?: string | null;
}): string {
  const cells = listCells();
  let lo: string | null = null;
  let hi: string | null = null;
  if (opts.beforeId) {
    const i = cells.findIndex((c) => c.id === opts.beforeId);
    lo = cells[i - 1]?.order ?? null;
    hi = cells[i]?.order ?? null;
  } else if (opts.afterId) {
    const i = cells.findIndex((c) => c.id === opts.afterId);
    lo = cells[i]?.order ?? null;
    hi = cells[i + 1]?.order ?? null;
  } else {
    lo = cells.length ? cells[cells.length - 1].order : null;
  }
  const id = newCellId();
  ydoc.transact(() => {
    const c = new Y.Map<any>();
    c.set("order", orderBetween(lo, hi));
    c.set("kind", opts.kind ?? "code");
    c.set("kernel", opts.kernel);
    c.set("source", new Y.Text(opts.source));
    c.set("author", user.id);
    c.set("created_by_run", null);
    c.set("exec", new Y.Map<any>([["state", "idle"], ["count", null]]));
    c.set("outputs", new Y.Array<Output>());
    ycells.set(id, c);
  }, STRUCT_ORIGIN);
  return id;
}

export function deleteCell(id: string) {
  ydoc.transact(() => ycells.delete(id), STRUCT_ORIGIN);
}

// --- RPC ---------------------------------------------------------------------
async function post(path: string, body: unknown) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) console.warn(path, r.status, await r.text());
  return r;
}

export const runCell = (cellId: string) => post("/api/run", { cell_id: cellId, author: user.id });
export const interruptKernel = (kernel: string) => post("/api/interrupt", { kernel });

/** Subscribe to any structural/exec/output change (but not keystrokes in cell sources). */
export function onCellsChange(cb: () => void): () => void {
  const handler = (events: Y.YEvent<any>[]) => {
    const interesting = events.some((e) => !(e.target instanceof Y.Text));
    if (interesting) cb();
  };
  ycells.observeDeep(handler);
  ykernels.observeDeep(cb);
  return () => {
    ycells.unobserveDeep(handler);
    ykernels.unobserveDeep(cb);
  };
}

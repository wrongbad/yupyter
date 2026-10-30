// Per-user undo. `structUndo` tracks only this client's STRUCT_ORIGIN
// transactions (insert/delete/move cell); server writes (exec/outputs) and
// remote edits are never tracked. Text undo inside an editor is the editor's
// own yUndo.
//
// An agent run is not a Yjs transaction of ours (the server wrote it), so the
// per-client `undo()`/`redo()` below interleave two kinds of entries in time
// order: {struct} entries mirror structUndo's own stack, {run} entries are
// reverted/re-applied through the server (`/api/undo_run`, `/api/redo_run`).
import * as Y from "yjs";
import { STRUCT_ORIGIN, apiUrl, listCells, post, ycells } from "./doc";

export const structUndo = new Y.UndoManager(ycells, {
  trackedOrigins: new Set([STRUCT_ORIGIN]),
  captureTimeout: 0,
});

type Entry = { kind: "struct" } | { kind: "run"; runId: string; cellId: string };
const undoStack: Entry[] = [];
const redoStack: Entry[] = [];
let replaying = false; // our own structUndo.undo()/redo() must not look like a new edit

structUndo.on("stack-item-added", (e: { type: "undo" | "redo" }) => {
  if (replaying || e.type !== "undo") return;
  undoStack.push({ kind: "struct" });
  redoStack.length = 0;
});

/** Remember that this client started agent run `runId` (on agent cell `cellId`). */
export function pushRun(runId: string, cellId: string) {
  undoStack.push({ kind: "run", runId, cellId });
  redoStack.length = 0;
}

export type RunResult = "ok" | "lost" | "error";

/** Ask the server to undo/redo a run. "lost": it has no history for it (server restarted). */
export async function runAction(kind: "undo" | "redo", runId: string): Promise<RunResult> {
  try {
    const r = await post(apiUrl(`/api/${kind}_run`), { run_id: runId });
    return r.ok ? "ok" : r.status === 404 ? "lost" : "error";
  } catch {
    return "error";
  }
}

/** A run can only be undone once it has finished (a later re-run of the cell means this one has). */
function finished(e: Entry & { kind: "run" }) {
  const s = listCells().find((c) => c.id === e.cellId)?.exec;
  if (!s) return true;
  if (!s.runId) return false; // not queued on the server yet
  return s.runId !== e.runId || s.state === "done" || s.state === "error";
}

function step(from: Entry[], to: Entry[], struct: () => void, kind: "undo" | "redo"): void {
  const e = from[from.length - 1];
  if (!e) return;
  if (e.kind === "run" && !finished(e)) return;
  from.pop();
  to.push(e);
  if (e.kind === "struct") {
    replaying = true;
    try {
      struct();
    } finally {
      replaying = false;
    }
  } else {
    void runAction(kind, e.runId).then((r) => {
      if (r !== "lost") return;
      to.splice(to.lastIndexOf(e), 1); // no history for it: drop the entry and fall through to the next one
      step(from, to, struct, kind);
    });
  }
}

export const undo = () => step(undoStack, redoStack, () => void structUndo.undo(), "undo");
export const redo = () => step(redoStack, undoStack, () => void structUndo.redo(), "redo");

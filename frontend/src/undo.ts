// Per-user structural undo: only this client's STRUCT_ORIGIN transactions
// (insert/delete/move cell). Server writes (exec/outputs) and remote edits are
// never tracked. Text undo inside an editor is the editor's own yUndo.
import * as Y from "yjs";
import { STRUCT_ORIGIN, ycells } from "./doc";

export const structUndo = new Y.UndoManager(ycells, {
  trackedOrigins: new Set([STRUCT_ORIGIN]),
  captureTimeout: 0,
});

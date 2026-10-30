// `%%claude` cell magic: running a code cell whose first line is `%%claude`
// turns it into an agent cell (kernel agent:claude, magic stripped) first.
// Agent runs are also recorded for Cmd/Ctrl+Z (see undo.ts).
import { STRUCT_ORIGIN, listCells, newCellId, runCell, ydoc } from "./doc";
import { pushRun } from "./undo";

export function runWithMagic(id: string) {
  const c = listCells().find((x) => x.id === id);
  const m = c?.kind === "code" && /^%%claude\b[ \t]*\n?/.exec(c.source.toString());
  if (c && m) {
    ydoc.transact(() => {
      c.ymap.set("kernel", "agent:claude");
      c.source.delete(0, m[0].length);
    }, STRUCT_ORIGIN);
  }
  // agent run: name it and put it on this user's undo history (after the magic conversion above)
  const agent = (c?.kernel ?? "").startsWith("agent:") || !!m;
  const runId = agent ? newCellId() + newCellId() : undefined;
  if (runId) pushRun(runId, id);
  return runCell(id, runId);
}

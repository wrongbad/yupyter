// Structural cell operations used by shortcuts and the palette.
import { STRUCT_ORIGIN, CellView, insertCell, deleteCell, listCells, orderBetween, ydoc } from "./doc";
import { runWithMagic } from "./magic";
import { getDefaultKernel } from "./user";
import { getSelected, select } from "./presence/selection";

export function focusCellEditor(id: string) {
  // the cell may not be rendered yet (Preact updates async): poll briefly
  let tries = 0;
  const go = () => {
    const el = document.querySelector(`[data-cell-id="${id}"]`);
    const cm = el?.querySelector(".cm-content") as HTMLElement | null;
    const md = el?.querySelector(".md-rendered") as HTMLElement | null;
    if (cm) cm.focus();
    else if (md) md.click();
    else if (tries++ < 40) setTimeout(go, 25);
  };
  go();
}

function neighbour(id: string, d: number): string | null {
  const cs = listCells();
  const i = cs.findIndex((c) => c.id === id);
  return cs[i + d]?.id ?? null;
}

export function moveSelection(d: number) {
  const cs = listCells();
  if (!cs.length) return;
  const i = cs.findIndex((c) => c.id === getSelected());
  const next = cs[Math.max(0, Math.min(cs.length - 1, i < 0 ? (d > 0 ? 0 : cs.length - 1) : i + d))];
  select(next.id);
  document.querySelector(`[data-cell-id="${next.id}"]`)?.scrollIntoView({ block: "nearest" });
}

export function insertRelative(where: "above" | "below", target?: string) {
  const cur = target ?? getSelected();
  const opts = { kernel: getDefaultKernel(), source: "" };
  const id = insertCell(
    cur ? (where === "above" ? { ...opts, beforeId: cur } : { ...opts, afterId: cur }) : opts,
  );
  select(id);
  focusCellEditor(id);
  return id;
}

export function addCell() {
  const id = insertCell({ kernel: getDefaultKernel(), source: "" });
  select(id);
  focusCellEditor(id);
  return id;
}

/** Switch a cell's type: "markdown", or a kernel name (which makes it a code cell). */
export function setCellType(cell: CellView, type: string) {
  ydoc.transact(() => {
    if (type === "markdown") cell.ymap.set("kind", "markdown");
    else (cell.ymap.set("kind", "code"), cell.ymap.set("kernel", type));
  }, STRUCT_ORIGIN);
}

export function deleteSelected(id?: string) {
  const cur = id ?? getSelected();
  if (!cur) return;
  const next = neighbour(cur, 1) ?? neighbour(cur, -1);
  deleteCell(cur);
  select(next);
}

export function moveCell(d: -1 | 1) {
  const cur = getSelected();
  if (!cur) return;
  const cs = listCells();
  const i = cs.findIndex((c) => c.id === cur);
  const j = i + d;
  if (i < 0 || j < 0 || j >= cs.length) return;
  const order =
    d < 0
      ? orderBetween(cs[j - 1]?.order ?? null, cs[j].order)
      : orderBetween(cs[j].order, cs[j + 1]?.order ?? null);
  ydoc.transact(() => cs[i].ymap.set("order", order), STRUCT_ORIGIN);
  requestAnimationFrame(() =>
    document.querySelector(`[data-cell-id="${cur}"]`)?.scrollIntoView({ block: "nearest" }),
  );
}

export function runSelected() {
  const cur = getSelected();
  if (cur) runWithMagic(cur);
}

export function runAll() {
  for (const c of listCells()) if (c.kind === "code") runWithMagic(c.id);
}

export function isEditable(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el?.closest?.("input, textarea, [contenteditable=true], .cm-editor");
}

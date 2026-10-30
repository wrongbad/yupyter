// Drag a cell by its head bar to reorder. Native HTML5 drag and drop: the head is
// draggable (except on its buttons/menus), the column computes the drop slot from
// cell midpoints and shows a line there; the drop is one undoable STRUCT_ORIGIN move.
import { useState } from "preact/hooks";
import { moveCellTo } from "./ops";

const MIME = "application/x-yupyter-cell";
const INTERACTIVE = "button, input, select, textarea, a, .menu-pop";
let dragging: string | null = null;
let dimFrame = 0;

type Ev<E extends Event> = E & { currentTarget: HTMLElement };

/** Props for a cell's head bar. */
export function cellDragProps(id: string) {
  return {
    onMouseDown: (e: Ev<MouseEvent>) => {
      e.currentTarget.draggable = !(e.target as Element).closest(INTERACTIVE);
    },
    onDragStart: (e: Ev<DragEvent>) => {
      const cell = e.currentTarget.closest(".cell") as HTMLElement | null;
      if (!e.dataTransfer || !cell) return;
      dragging = id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData(MIME, id);
      const r = cell.getBoundingClientRect();
      e.dataTransfer.setDragImage(cell, e.clientX - r.left, e.clientY - r.top);
      dimFrame = requestAnimationFrame(() => cell.classList.add("dragging")); // after the drag image is taken
    },
    onDragEnd: (e: Ev<DragEvent>) => {
      dragging = null;
      cancelAnimationFrame(dimFrame);
      e.currentTarget.draggable = false;
      document.querySelectorAll(".cell.dragging").forEach((c) => c.classList.remove("dragging"));
    },
  };
}

/** Drop handling for the cell column: spread `props` on it and draw a line at `slotTop` (the column must be position: relative). */
export function useCellDrop() {
  const [slot, setSlot] = useState<{ before: string | null; top: number } | null>(null);

  const locate = (col: HTMLElement, y: number) => {
    const frames = [...col.querySelectorAll<HTMLElement>("[data-frame-id]")];
    const base = col.getBoundingClientRect().top;
    for (let i = 0; i < frames.length; i++) {
      const r = frames[i].getBoundingClientRect();
      if (y < r.top + r.height / 2) {
        const prev = frames[i - 1]?.getBoundingClientRect();
        return { before: frames[i].dataset.frameId!, top: (prev ? (prev.bottom + r.top) / 2 : r.top - 6) - base };
      }
    }
    const last = frames[frames.length - 1]?.getBoundingClientRect();
    return { before: null, top: (last ? last.bottom + 11 : 0) - base };
  };

  const props = {
    onDragOver: (e: Ev<DragEvent>) => {
      if (!dragging || !e.dataTransfer?.types.includes(MIME)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const s = locate(e.currentTarget, e.clientY);
      if (s.before !== slot?.before || s.top !== slot?.top) setSlot(s);
    },
    onDragLeave: (e: Ev<DragEvent>) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setSlot(null);
    },
    onDrop: (e: Ev<DragEvent>) => {
      const id = e.dataTransfer?.getData(MIME);
      if (!id) return;
      e.preventDefault();
      moveCellTo(id, locate(e.currentTarget, e.clientY).before);
      setSlot(null);
    },
    onDragEnd: () => setSlot(null),
  };
  return { props, slotTop: slot?.top ?? null };
}

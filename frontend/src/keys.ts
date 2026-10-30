// Global keyboard handling: palette, undo (cell ops + agent runs), Jupyter-style command mode.
import { isPaletteOpen, togglePalette } from "./components/Palette";
import { deleteSelected, insertRelative, isEditable, moveCell, moveSelection, focusCellEditor, runSelected } from "./ops";
import { getSelected, select } from "./presence/selection";
import { listCells } from "./doc";
import { redo, undo } from "./undo";
import { saveNow } from "./files";

export function installKeys(): () => void {
  let lastD = 0;
  const onKey = (e: KeyboardEvent) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      e.stopPropagation();
      togglePalette();
      return;
    }
    if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      void saveNow();
      return;
    }
    if (isPaletteOpen()) return;
    const editable = isEditable(e.target);

    if (e.key === "Escape" && editable) {
      (document.activeElement as HTMLElement | null)?.blur();
      if (!getSelected()) {
        const cs = listCells();
        if (cs.length) select(cs[cs.length - 1].id);
      }
      e.preventDefault();
      return;
    }
    if (mod && !e.altKey && !editable) {
      const k = e.key.toLowerCase();
      if (k === "z" || k === "y") {
        e.preventDefault();
        if (k === "y" || e.shiftKey) redo();
        else undo();
      }
      return;
    }
    if (editable || mod) return;

    if (e.altKey) {
      if (e.key === "ArrowUp") (e.preventDefault(), moveCell(-1));
      else if (e.key === "ArrowDown") (e.preventDefault(), moveCell(1));
      return;
    }
    switch (e.key) {
      case "j":
      case "ArrowDown":
        e.preventDefault();
        moveSelection(1);
        break;
      case "k":
      case "ArrowUp":
        e.preventDefault();
        moveSelection(-1);
        break;
      case "a":
        e.preventDefault();
        insertRelative("above");
        break;
      case "b":
        e.preventDefault();
        insertRelative("below");
        break;
      case "d":
        if (Date.now() - lastD < 600) {
          lastD = 0;
          deleteSelected();
        } else lastD = Date.now();
        break;
      case "Enter": {
        const cur = getSelected();
        if (!cur) break;
        e.preventDefault();
        if (e.shiftKey) runSelected();
        else focusCellEditor(cur);
        break;
      }
    }
  };
  window.addEventListener("keydown", onKey, true);
  return () => window.removeEventListener("keydown", onKey, true);
}

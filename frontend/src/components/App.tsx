import { useEffect, useState } from "preact/hooks";
import { listCells, notebookPath, onCellsChange } from "../doc";
import { Drawer } from "./Drawer";
import { FileHeader } from "./FileHeader";
import { useDrawerOpen } from "../files";
import { Cell } from "./Cell";
import { Palette } from "./Palette";
import { Avatars, useSynced } from "../presence/Avatars";
import { CellFrame } from "../presence/CellFrame";
import { installKeys } from "../keys";
import "../builtinCommands";
import { addCell } from "../ops";
import { useCellDrop } from "../dnd";
import { getSelected, select } from "../presence/selection";

function useCells() {
  const [cells, setCells] = useState(listCells());
  useEffect(() => {
    const update = () => setCells(listCells());
    update();
    return onCellsChange(update);
  }, []);
  return cells;
}

export function App() {
  const cells = useCells();
  const synced = useSynced();
  const drawer = useDrawerOpen();
  const drop = useCellDrop();
  useEffect(() => installKeys(), []);
  useEffect(() => {
    const sel = getSelected();
    if (sel && !cells.some((c) => c.id === sel)) select(null);
  }, [cells]);
  return (
    <div class={`app${drawer ? " drawer-open" : ""}`} data-synced={synced}>
      <FileHeader />
      <Drawer />
      {notebookPath !== null && <Avatars />}
      <Palette />
      {notebookPath === null ? (
        <main class="column"><p class="empty">Pick a notebook from the file list, or create one.</p></main>
      ) : (
      <main class="column cells" {...drop.props}>
        {drop.slotTop !== null && <div class="drop-line" style={{ top: `${drop.slotTop}px` }} />}
        {cells.map((c) => (
          <CellFrame key={c.id} id={c.id}>
            <Cell cell={c} />
          </CellFrame>
        ))}
        {!cells.length && synced && <p class="empty">Nothing here yet.</p>}
        {synced && <button class="new-cell" onClick={addCell}>+ New cell</button>}
      </main>
      )}
    </div>
  );
}

import { useEffect, useState } from "preact/hooks";
import { awareness, listCells, onCellsChange, provider } from "../doc";
import { Cell } from "./Cell";
import { PromptBar } from "./PromptBar";
import { Palette } from "./Palette";
import { Avatars } from "../presence/Avatars";
import { CellFrame } from "../presence/CellFrame";
import { installKeys } from "../keys";
import "../builtinCommands";
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

function useSynced() {
  const [synced, setSynced] = useState(provider.synced);
  useEffect(() => {
    const h = (s: boolean) => setSynced(s);
    provider.on("sync", h);
    return () => provider.off("sync", h);
  }, []);
  return synced;
}

function usePeers() {
  const [n, setN] = useState(1);
  useEffect(() => {
    const h = () => setN(awareness.getStates().size);
    awareness.on("change", h);
    h();
    return () => awareness.off("change", h);
  }, []);
  return n;
}

export function App() {
  const cells = useCells();
  const synced = useSynced();
  const peers = usePeers();
  useEffect(() => installKeys(), []);
  useEffect(() => {
    const sel = getSelected();
    if (sel && !cells.some((c) => c.id === sel)) select(null);
  }, [cells]);
  return (
    <div class="app" data-synced={synced}>
      <Avatars />
      <Palette />
      <main class="column">
        {cells.map((c) => (
          <CellFrame key={c.id} id={c.id}>
            <Cell cell={c} />
          </CellFrame>
        ))}
        {!cells.length && synced && <p class="empty">Nothing here yet. Type below.</p>}
      </main>
      <footer class="bottom">
        <PromptBar />
        <div class="status">
          <span>{synced ? "connected" : "connecting…"}</span>
          {peers > 1 && <span>{peers} here</span>}
        </div>
      </footer>
    </div>
  );
}

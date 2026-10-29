import { useEffect, useState } from "preact/hooks";
import { awareness, listCells, onCellsChange, provider } from "../doc";
import { Cell } from "./Cell";
import { PromptBar } from "./PromptBar";

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
  return (
    <div class="app" data-synced={synced}>
      <main class="column">
        {cells.map((c) => (
          <Cell key={c.id} cell={c} />
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

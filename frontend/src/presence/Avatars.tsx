import { useEffect, useState } from "preact/hooks";
import { provider } from "../doc";
import { renameSelf, usePeers } from "./peers";

export function useSynced() {
  const [synced, setSynced] = useState(provider.synced);
  useEffect(() => {
    const h = (s: boolean) => setSynced(s);
    provider.on("sync", h);
    h(provider.synced); // sync may have landed between first render and subscribing
    return () => provider.off("sync", h);
  }, []);
  return synced;
}

/** Top-right bar: connection status, then one dot per connection (agents included). Click your own to rename. */
export function Avatars() {
  const synced = useSynced();
  const peers = usePeers();
  return (
    <div class="avatars" data-testid="avatars">
      <span class="conn-status" data-synced={synced}>{synced ? "connected" : "connecting…"}</span>
      {peers.map((p) => (
        <span
          key={p.clientId}
          class={`avatar ${p.kind}${p.self ? " self" : ""}`}
          data-name={p.name}
          data-kind={p.kind}
          title={p.name + (p.self ? " (you, click to rename)" : "")}
          style={{ background: p.color }}
          onClick={p.self ? renameSelf : undefined}
        >
          {p.kind === "agent" ? "✦" : p.name.length <= 2 ? p.name : p.name.slice(0, 1).toUpperCase()}
        </span>
      ))}
    </div>
  );
}

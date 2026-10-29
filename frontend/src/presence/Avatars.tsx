import { usePeers } from "./peers";

/** Subtle avatar row (top right) of everyone present, agents included. */
export function Avatars() {
  const seen = new Set<string>();
  const peers = usePeers().filter((p) => (seen.has(p.id) ? false : (seen.add(p.id), true)));
  return (
    <div class="avatars" data-testid="avatars">
      {peers.map((p) => (
        <span
          key={p.id}
          class={`avatar ${p.kind}${p.self ? " self" : ""}`}
          data-name={p.name}
          data-kind={p.kind}
          title={p.name + (p.self ? " (you)" : "")}
          style={{ background: p.color }}
        >
          {p.kind === "agent" ? "✦" : p.name.slice(0, 1).toUpperCase()}
        </span>
      ))}
    </div>
  );
}

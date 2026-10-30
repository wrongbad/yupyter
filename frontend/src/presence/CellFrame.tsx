import type { ComponentChildren } from "preact";
import { usePeers } from "./peers";
import { select, useSelected } from "./selection";

/** Wraps a cell: tracks selection (for command-mode keys) and shows a coloured left-edge marker for remote participants. */
export function CellFrame({ id, children }: { id: string; children: ComponentChildren }) {
  const sel = useSelected();
  const remote = usePeers().filter((p) => !p.self && p.focus === id);
  const style = remote.length ? { "--remote-color": remote[0].color } : undefined;
  return (
    <div
      class={`cell-frame${sel === id ? " selected" : ""}${remote.length ? " has-remote" : ""}`}
      data-frame-id={id}
      data-remote={remote.map((r) => r.name).join(",") || undefined}
      title={remote.length ? remote.map((r) => r.name).join(", ") : undefined}
      style={style as any}
      onFocusIn={() => select(id)}
      onMouseDown={() => select(id)}
    >
      {children}
    </div>
  );
}

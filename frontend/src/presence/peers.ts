// Awareness helpers: who is here, and which cell each participant is focused in.
// Awareness shape: { user: {id,name,color,colorLight,kind}, cursor: <y-codemirror.next owned>, focus: {cellId} }
import { useEffect, useState } from "preact/hooks";
import { awareness } from "../doc";
import { getUser } from "../user";

export interface Peer {
  clientId: number;
  id: string;
  name: string;
  color: string;
  kind: "human" | "agent";
  focus: string | null;
  self: boolean;
}

export function getPeers(): Peer[] {
  const out: Peer[] = [];
  const me = getUser().id;
  awareness.getStates().forEach((st: any, clientId: number) => {
    const u = st?.user;
    if (!u || !u.name) return;
    out.push({
      clientId,
      id: u.id ?? String(clientId),
      name: u.name,
      color: u.color ?? "#888",
      kind: u.kind === "agent" ? "agent" : "human",
      focus: st.focus?.cellId ?? null,
      self: clientId === awareness.clientID || u.id === me,
    });
  });
  out.sort((a, b) => (a.self === b.self ? a.name.localeCompare(b.name) : a.self ? -1 : 1));
  return out;
}

export function setFocus(cellId: string | null) {
  awareness.setLocalStateField("focus", { cellId });
}

export function usePeers(): Peer[] {
  const [peers, setPeers] = useState(getPeers());
  useEffect(() => {
    let key = "";
    const h = () => {
      const p = getPeers();
      const k = JSON.stringify(p);
      if (k !== key) {
        key = k;
        setPeers(p);
      }
    };
    awareness.on("change", h);
    h();
    return () => awareness.off("change", h);
  }, []);
  return peers;
}

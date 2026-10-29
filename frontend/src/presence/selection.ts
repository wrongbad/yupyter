// Which cell is selected locally (Jupyter "command mode" cursor). Published as awareness focus.
import { useEffect, useState } from "preact/hooks";
import { setFocus } from "./peers";

let selected: string | null = null;
const subs = new Set<() => void>();

export const getSelected = () => selected;

export function select(id: string | null) {
  if (id === selected) return;
  selected = id;
  setFocus(id);
  subs.forEach((f) => f());
}

export function useSelected(): string | null {
  const [s, setS] = useState(selected);
  useEffect(() => {
    const f = () => setS(selected);
    subs.add(f);
    f();
    return () => void subs.delete(f);
  }, []);
  return s;
}

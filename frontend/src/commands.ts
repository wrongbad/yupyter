// Command registry. Other features add palette commands with registerCommand().
//   registerCommand({ id: "kernel.restart", title: "Restart kernel", run: () => ... })
// `run` may return a promise. `shortcut` is display-only text (e.g. "Alt+↑").
import { useEffect, useState } from "preact/hooks";

export interface Command {
  id: string;
  title: string;
  run: () => void | Promise<void>;
  shortcut?: string;
}

const registry = new Map<string, Command>();
const subs = new Set<() => void>();

/** Register (or replace, by id) a command. Returns an unregister function. */
export function registerCommand(c: Command): () => void {
  registry.set(c.id, c);
  subs.forEach((f) => f());
  return () => {
    if (registry.get(c.id) === c) registry.delete(c.id);
    subs.forEach((f) => f());
  };
}

export const getCommands = (): Command[] => [...registry.values()];

export function useCommands(): Command[] {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    subs.add(f);
    return () => void subs.delete(f);
  }, []);
  return getCommands();
}

/** Subsequence fuzzy score; higher is better, -1 = no match. */
export function fuzzy(query: string, text: string): number {
  const q = query.toLowerCase().replace(/\s+/g, "");
  const t = text.toLowerCase();
  if (!q) return 0;
  let ti = 0;
  let score = 0;
  let prev = -2;
  for (const ch of q) {
    const i = t.indexOf(ch, ti);
    if (i < 0) return -1;
    score += i === prev + 1 ? 3 : 1;
    if (i === 0 || t[i - 1] === " ") score += 2;
    prev = i;
    ti = i + 1;
  }
  return score - t.length * 0.01;
}

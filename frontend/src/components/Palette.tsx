import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Command, fuzzy, useCommands } from "../commands";

// Module-level palette state so commands/keys can drive it.
type Mode =
  | { kind: "closed" }
  | { kind: "commands"; onlyShortcuts?: boolean }
  | { kind: "input"; title: string; initial: string; resolve: (v: string | null) => void };

let mode: Mode = { kind: "closed" };
const subs = new Set<() => void>();
const set = (m: Mode) => {
  mode = m;
  subs.forEach((f) => f());
};

export const isPaletteOpen = () => mode.kind !== "closed";
export const togglePalette = () => set(mode.kind === "closed" ? { kind: "commands" } : { kind: "closed" });
export const closePalette = () => {
  if (mode.kind === "input") mode.resolve(null);
  set({ kind: "closed" });
};
export const showShortcuts = () => set({ kind: "commands", onlyShortcuts: true });
/** Ask for a line of text in the palette; resolves null if dismissed. */
export const askText = (title: string, initial = "") =>
  new Promise<string | null>((resolve) => set({ kind: "input", title, initial, resolve }));

export function Palette() {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    subs.add(f);
    return () => void subs.delete(f);
  }, []);
  if (mode.kind === "closed") return null;
  return <PaletteBody key={mode.kind + (mode.kind === "input" ? mode.title : "")} m={mode} />;
}

function PaletteBody({ m }: { m: Exclude<Mode, { kind: "closed" }> }) {
  const [q, setQ] = useState(m.kind === "input" ? m.initial : "");
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const all = useCommands();
  useLayoutEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  const filter = (query: string): Command[] =>
    m.kind !== "commands"
      ? []
      : all
          .filter((c) => !m.onlyShortcuts || c.shortcut)
          .map((c) => ({ c, s: fuzzy(query, c.title) }))
          .filter((x) => x.s >= 0)
          .sort((a, b) => b.s - a.s)
          .map((x) => x.c);
  const items = filter(q);

  function accept() {
    // read the live input value: state may lag fast typing
    const live = input.current?.value ?? q;
    if (m.kind === "input") {
      m.resolve(live);
      set({ kind: "closed" });
      return;
    }
    const c = filter(live)[live === q ? idx : 0];
    set({ kind: "closed" });
    if (c) setTimeout(() => void c.run(), 0);
  }

  return (
    <div class="palette-backdrop" onMouseDown={closePalette}>
      <div class="palette" role="dialog" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={input}
          class="palette-input"
          value={q}
          placeholder={m.kind === "input" ? m.title : m.onlyShortcuts ? "Shortcuts" : "Type a command…"}
          spellcheck={false}
          onInput={(e) => {
            setQ((e.target as HTMLInputElement).value);
            setIdx(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              closePalette();
            } else if (e.key === "Enter") {
              e.preventDefault();
              accept();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              setIdx(Math.min(items.length - 1, idx + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIdx(Math.max(0, idx - 1));
            }
          }}
        />
        {m.kind === "commands" && (
          <ul class="palette-list">
            {items.map((c, i) => (
              <li
                key={c.id}
                class={i === idx ? "active" : ""}
                data-command={c.id}
                onMouseEnter={() => setIdx(i)}
                onClick={() => {
                  set({ kind: "closed" });
                  setTimeout(() => void c.run(), 0);
                }}
              >
                <span>{c.title}</span>
                {c.shortcut && <kbd>{c.shortcut}</kbd>}
              </li>
            ))}
            {!items.length && <li class="none">No matching command</li>}
          </ul>
        )}
      </div>
    </div>
  );
}

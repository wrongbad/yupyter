import { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  active?: boolean;
  danger?: boolean;
}

interface Props {
  button: ComponentChildren;
  items: MenuItem[];
  title?: string;
  class?: string;
}

/** Button + popup list; closes on outside click, Escape or selection. */
export function Menu({ button, items, title, class: cls }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: Event) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") (e.stopPropagation(), setOpen(false));
    };
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("pointerdown", down, true);
      document.removeEventListener("keydown", key, true);
    };
  }, [open]);
  return (
    <span class="menu" ref={root}>
      <button class={cls} title={title} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {button}
      </button>
      {open && (
        <ul class="menu-pop" role="menu">
          {items.map((it) => (
            <li
              key={it.label}
              role="menuitem"
              class={(it.active ? "active " : "") + (it.danger ? "danger" : "")}
              onClick={() => (setOpen(false), it.onSelect())}
            >
              {it.label}
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}

import { useEffect, useState } from "preact/hooks";
import { notebookPath } from "../doc";
import { TreeEntry, fetchTree, newNotebook, openNotebook, useDrawerOpen } from "../files";
import { askText } from "./Palette";

const dirOf = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

function Icon({ type }: { type: TreeEntry["type"] }) {
  const common = { width: 14, height: 14, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.3, "stroke-linejoin": "round", "aria-hidden": true } as const;
  if (type === "dir") return <svg {...common}><path d="M2 4.5h4l1.5 1.5H14v6.5H2z" /></svg>;
  if (type === "notebook") return <svg {...common}><rect x="3" y="2" width="10" height="12" rx="1.5" /><path d="M6 5.5h4M6 8h4" stroke-linecap="round" /></svg>;
  return <svg {...common}><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3" /></svg>;
}

function Dir({ dir, depth, tick }: { dir: string; depth: number; tick: number }) {
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    let live = true;
    fetchTree(dir).then(
      (e) => live && (setEntries(e), setErr(null)),
      (e) => live && setErr(String(e.message ?? e)),
    );
    return () => void (live = false);
  }, [dir, tick]);
  if (err) return <div class="drawer-err" style={{ paddingLeft: `${0.8 + depth}rem` }}>{err}</div>;
  if (!entries) return null;
  return (
    <ul class="tree">
      {entries.map((e) => {
        const pad = { paddingLeft: `${0.6 + depth * 0.9}rem` };
        if (e.type === "dir") {
          const isOpen = open[e.path] ?? (notebookPath?.startsWith(e.path + "/") ?? false);
          return (
            <li key={e.path}>
              <div class="tree-row dir" style={pad} data-testid="tree-dir" onClick={() => setOpen({ ...open, [e.path]: !isOpen })}>
                <span class="caret">{isOpen ? "▾" : "▸"}</span>
                <Icon type="dir" /> <span class="tree-name">{e.name}</span>
              </div>
              {isOpen && <Dir dir={e.path} depth={depth + 1} tick={tick} />}
            </li>
          );
        }
        const clickable = e.type !== "other";
        const current = e.path === notebookPath;
        return (
          <li key={e.path}>
            <div
              class={`tree-row file ${e.type}${current ? " current" : ""}${clickable ? "" : " disabled"}`}
              style={pad}
              data-testid="tree-file"
              data-path={e.path}
              title={e.type === "markdown" ? "Plain markdown: becomes a notebook when saved" : undefined}
              onClick={clickable && !current ? () => openNotebook(e.path) : undefined}
            >
              <span class="caret" />
              <Icon type={e.type} /> <span class="tree-name">{e.name}</span>
              {e.kernel_running && <span class="k-dot" title="Kernel running" />}
              {(e.open_by ?? []).slice(0, 4).map((p, i) => (
                <span key={i} class="mini-avatar" title={p.name} style={{ background: p.color }} />
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Left drawer: folder tree from the server root, "+ New notebook" in the current folder. */
export function Drawer() {
  const open = useDrawerOpen();
  const [tick, setTick] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setTick((t) => t + 1);
    const h = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(h);
  }, [open]);
  if (!open) return null;
  const dir = notebookPath ? dirOf(notebookPath) : "";
  const create = async () => {
    const name = await askText(`New notebook in ${dir || "the root folder"}`, "untitled");
    if (!name?.trim()) return;
    try {
      openNotebook(await newNotebook(dir, name.trim()));
    } catch (e: any) {
      setErr(String(e.message ?? e));
    }
  };
  return (
    <aside class="drawer" data-testid="drawer">
      <div class="drawer-body">
        <Dir dir="" depth={0} tick={tick} />
      </div>
      <div class="drawer-foot">
        {err && <div class="drawer-err">{err}</div>}
        <button class="new-nb" data-testid="new-notebook" onClick={create}>+ New notebook</button>
      </div>
    </aside>
  );
}

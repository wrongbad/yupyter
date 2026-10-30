import { useEffect, useState } from "preact/hooks";
import MarkdownIt from "markdown-it";
import markdownItKatex from "@vscode/markdown-it-katex";
import "katex/dist/katex.min.css";
import * as Y from "yjs";
import { CellView, interruptKernel } from "../doc";
import { deleteSelected, insertRelative, setCellType } from "../ops";
import { runWithMagic } from "../magic";
import { runAction } from "../undo";
import { kernelClass, kernelLabel, useKernels } from "../kernels";
import { Menu } from "./Menu";
import { OutputList } from "../outputs";
import { Editor } from "./Editor";

// CJS interop: the plugin may arrive as {default: fn}
const katexPlugin: typeof markdownItKatex = (markdownItKatex as any).default ?? markdownItKatex;
const md = new MarkdownIt({ html: false, linkify: true }).use(katexPlugin, { throwOnError: false });

/** Rendered markdown that re-renders whenever the cell's Y.Text changes (local or remote). */
function MarkdownView({ text, onClick }: { text: Y.Text; onClick: () => void }) {
  const [, bump] = useState(0);
  useEffect(() => {
    const h = () => bump((n) => n + 1);
    text.observe(h);
    return () => text.unobserve(h);
  }, [text]);
  return (
    <div
      class="md-rendered"
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: md.render(text.toString() || "*empty*") }}
    />
  );
}

function RunState({ cell }: { cell: CellView }) {
  const s = cell.exec.state;
  return (
    <span class={`runstate rs-${s}`} data-state={s}>
      <span class="dot" />
      <span class="tip">{cell.id} · {s}</span>
    </span>
  );
}

function KernelMenu({ cell }: { cell: CellView }) {
  const md = cell.kind === "markdown";
  const ks = useKernels();
  const all = [...new Set([...(md ? [] : [cell.kernel]), ...ks])];
  return (
    <Menu
      class={`kernel ${md ? "k-md" : kernelClass(cell.kernel)}`}
      title="Cell type"
      button={md ? "markdown" : kernelLabel(cell.kernel)}
      items={[
        ...all.map((k) => ({ label: kernelLabel(k), active: !md && k === cell.kernel, onSelect: () => setCellType(cell, k) })),
        { label: "markdown", active: md, onSelect: () => setCellType(cell, "markdown") },
      ]}
    />
  );
}

const NOT_REVERTED = "Kernel state (variables, installed packages, files written) is NOT reverted.";

function plural(n: number, w: string) {
  return `${n} ${w}${n === 1 ? "" : "s"}`;
}

/** "Undo this run" / "Redo" for a finished agent run; `exec.undone` is shared, so every collaborator sees the same state. */
function RunUndo({ cell }: { cell: CellView }) {
  const { runId, summary, undone } = cell.exec;
  const [busy, setBusy] = useState(false);
  const [lost, setLost] = useState(false);
  const highlight = (on: boolean) =>
    document.querySelectorAll(`[data-run="${runId}"]`).forEach((el) => el.classList.toggle("run-hl", on));
  useEffect(() => () => highlight(false), [runId]);
  if (!runId || !summary) return null;
  const parts = [
    summary.inserted && `inserted ${plural(summary.inserted, "cell")}`,
    summary.edited && `edited ${plural(summary.edited, "cell")}`,
    summary.deleted && `deleted ${plural(summary.deleted, "cell")}`,
  ].filter(Boolean);
  if (!parts.length) return null;
  const click = async () => {
    setBusy(true);
    highlight(false);
    if ((await runAction(undone ? "redo" : "undo", runId)) === "lost") setLost(true);
    setBusy(false);
  };
  return (
    <div class="run-undo">
      <span class="run-summary">Claude {parts.join(", ")}</span>
      <button
        disabled={busy || lost}
        title={lost ? "The server forgot this run when it restarted." : `${undone ? "Re-apply" : "Revert"} this run's changes to the notebook. ${NOT_REVERTED}`}
        onMouseEnter={() => highlight(true)}
        onMouseLeave={() => highlight(false)}
        onClick={click}
      >
        {lost ? "Undo history lost after restart" : undone ? "Redo" : "Undo this run"}
      </button>
    </div>
  );
}

function CellMenu({ id }: { id: string }) {
  return (
    <Menu
      title="Cell menu"
      button="⋯"
      items={[
        { label: "Insert above", onSelect: () => void insertRelative("above", id) },
        { label: "Insert below", onSelect: () => void insertRelative("below", id) },
        { label: "Delete", danger: true, onSelect: () => deleteSelected(id) },
      ]}
    />
  );
}

export function Cell({ cell }: { cell: CellView }) {
  const [editing, setEditing] = useState(false);
  const busy = cell.exec.state === "running" || cell.exec.state === "queued";

  if (cell.kind === "markdown") {
    return (
      <section class="cell cell-md" data-cell-id={cell.id} data-run={cell.createdByRun ?? undefined}>
        {/* type label only while editing; preventDefault keeps the editor from blurring on menu clicks */}
        <div class={editing ? "cell-head" : "cell-head floating"} onMouseDown={(e) => e.preventDefault()}>
          {editing && <KernelMenu cell={cell} />}
          <span class="cell-tools"><CellMenu id={cell.id} /></span>
        </div>
        {editing ? (
          <Editor
            text={cell.source}
            lang="markdown"
            wrap
            autofocus
            onRun={() => setEditing(false)}
            onBlur={() => setEditing(false)}
          />
        ) : (
          <MarkdownView text={cell.source} onClick={() => setEditing(true)} />
        )}
      </section>
    );
  }
  return (
    <section class={`cell cell-code state-${cell.exec.state}`} data-cell-id={cell.id} data-run={cell.createdByRun ?? undefined}>
      <div class="cell-head">
        <RunState cell={cell} />
        <KernelMenu cell={cell} />
        <span class="cell-tools">
          {busy ? (
            <button onClick={() => interruptKernel(cell.kernel)} title="Interrupt">■</button>
          ) : (
            <button onClick={() => runWithMagic(cell.id)} title="Run (Shift+Enter)">▶</button>
          )}
          <CellMenu id={cell.id} />
        </span>
      </div>
      <Editor
        text={cell.source}
        lang={cell.kernel.startsWith("agent:") ? "markdown" : "python"}
        onRun={() => runWithMagic(cell.id)}
      />
      <div class="cell-out" data-testid="outputs">
        <OutputList outputs={cell.outputs} />
      </div>
      {cell.kernel.startsWith("agent:") && !busy && <RunUndo cell={cell} />}
    </section>
  );
}

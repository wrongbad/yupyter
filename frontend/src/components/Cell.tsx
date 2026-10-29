import { useEffect, useState } from "preact/hooks";
import MarkdownIt from "markdown-it";
import markdownItKatex from "@vscode/markdown-it-katex";
import "katex/dist/katex.min.css";
import * as Y from "yjs";
import { CellView, deleteCell, interruptKernel, runCell } from "../doc";
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
  const label =
    s === "running" ? "running" : s === "queued" ? "queued" : s === "error" ? "error" : s === "done" ? "done" : "idle";
  const n = cell.exec.count != null ? `[${cell.exec.count}]` : "[ ]";
  return (
    <span class={`runstate rs-${s}`} title={label} data-state={s}>
      <span class="dot" />
      <span class="count">{n}</span>
    </span>
  );
}

export function Cell({ cell }: { cell: CellView }) {
  const [editing, setEditing] = useState(false);
  const busy = cell.exec.state === "running" || cell.exec.state === "queued";

  if (cell.kind === "markdown") {
    return (
      <section class="cell cell-md" data-cell-id={cell.id}>
        {editing ? (
          <Editor text={cell.source} lang="markdown" autofocus onBlur={() => setEditing(false)} />
        ) : (
          <MarkdownView text={cell.source} onClick={() => setEditing(true)} />
        )}
        <div class="cell-tools">
          <button onClick={() => deleteCell(cell.id)} title="Delete cell">×</button>
        </div>
      </section>
    );
  }
  return (
    <section class={`cell cell-code state-${cell.exec.state}`} data-cell-id={cell.id}>
      <div class="cell-head">
        <RunState cell={cell} />
        <span class="chip" title="kernel">{cell.kernel}</span>
        <span class="cell-tools">
          {busy ? (
            <button onClick={() => interruptKernel(cell.kernel)} title="Interrupt">■</button>
          ) : (
            <button onClick={() => runCell(cell.id)} title="Run (Shift+Enter)">▶</button>
          )}
          <button onClick={() => deleteCell(cell.id)} title="Delete cell">×</button>
        </span>
      </div>
      <Editor text={cell.source} lang="python" onRun={() => runCell(cell.id)} />
      <div class="cell-out" data-testid="outputs">
        <OutputList outputs={cell.outputs} />
      </div>
    </section>
  );
}

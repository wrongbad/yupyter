import { useState } from "preact/hooks";
import MarkdownIt from "markdown-it";
import { CellView, deleteCell, interruptKernel, runCell } from "../doc";
import { OutputList } from "../outputs";
import { Editor } from "./Editor";

const md = new MarkdownIt({ html: false, linkify: true });

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
          <div
            class="md-rendered"
            onClick={() => setEditing(true)}
            dangerouslySetInnerHTML={{ __html: md.render(cell.source.toString() || "*empty*") }}
          />
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

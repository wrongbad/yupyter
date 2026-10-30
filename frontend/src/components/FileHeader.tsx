import { useEffect, useRef, useState } from "preact/hooks";
import { notebookPath } from "../doc";
import { renameNotebook, resolveConflict, saveNow, toggleDrawer, useFileStatus } from "../files";

export function FilesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true">
      <path d="M2.5 4h11M2.5 8h11M2.5 12h7" />
    </svg>
  );
}

function SaveStatus() {
  const st = useFileStatus();
  if (!st) return null;
  const retry = () => void saveNow();
  if (st.state === "saving") return <span class="save-status saving" data-state="saving">Saving…</span>;
  if (st.state === "error")
    return (
      <span class="save-status error" data-state="error" title={`${st.error ?? ""} (click to retry)`} onClick={retry}>
        Save failed{st.error ? `: ${st.error}` : ""}
      </span>
    );
  if (st.state === "conflict") return <span class="save-status error" data-state="conflict">Saving paused</span>;
  const d = st.saved_at ? new Date(st.saved_at * 1000) : null;
  return (
    <span class="save-status saved" data-state="saved" title={d ? `Last saved ${d.toLocaleString()}` : "Not written to disk yet"}>
      {d ? `Saved ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Saved"}
    </span>
  );
}

/** The file name; click to rename (a path relative to the root moves the file). */
function FileName({ path }: { path: string }) {
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) {
      input.current?.focus();
      const dot = path.lastIndexOf(".");
      input.current?.setSelectionRange(path.lastIndexOf("/") + 1, dot > 0 ? dot : path.length);
    }
  }, [editing]);
  const commit = async () => {
    const to = input.current?.value.trim() ?? "";
    if (!to || to === path) return setEditing(false);
    const e = await renameNotebook(to);
    if (e) setErr(e);
    else setEditing(false);
  };
  if (editing)
    return (
      <span class="file-rename">
        <input
          ref={input}
          class="file-name-input"
          data-testid="file-name-input"
          defaultValue={path}
          spellcheck={false}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") void commit();
            else if (e.key === "Escape") (setErr(null), setEditing(false));
          }}
          onBlur={() => (!err ? setEditing(false) : undefined)}
        />
        {err && <span class="file-error" data-testid="file-error">{err}</span>}
      </span>
    );
  return (
    <button class="file-name" data-testid="file-name" title="Click to rename" onClick={() => (setErr(null), setEditing(true))}>
      {path}
    </button>
  );
}

/** Top-left: files toggle, file name, save status, and the external-change banner. */
export function FileHeader() {
  const st = useFileStatus();
  const [err, setErr] = useState<string | null>(null);
  const act = async (a: "reload" | "keep") => setErr(await resolveConflict(a));
  return (
    <div class="file-header" data-testid="file-header">
      <button class="icon-btn" title="Files" aria-label="Toggle file list" data-testid="drawer-toggle" onClick={toggleDrawer}>
        <FilesIcon />
      </button>
      {notebookPath !== null ? <FileName path={st?.path ?? notebookPath} /> : <span class="file-title">yupyter</span>}
      {notebookPath !== null && <SaveStatus />}
      {st?.state === "conflict" && (
        <span class="conflict" data-testid="conflict">
          File changed on disk —{" "}
          <button onClick={() => act("reload")}>Reload</button>
          <button onClick={() => act("keep")}>Keep mine</button>
          {err && <span class="file-error"> {err}</span>}
        </span>
      )}
    </div>
  );
}

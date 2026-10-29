import { useRef, useState } from "preact/hooks";
import { insertCell, runCell } from "../doc";
import { parsePrompt } from "../magic";
import { getDefaultKernel } from "../user";

/** Enter = create cell at the end and run it. Shift+Enter = newline. */
export function PromptBar() {
  const [text, setText] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const t = text.replace(/\s+$/, "");
    if (!t) return;
    const p = parsePrompt(t, getDefaultKernel());
    const id = insertCell({ kind: p.kind, kernel: p.kernel, source: p.source });
    setText("");
    if (p.kind === "code") runCell(id);
    requestAnimationFrame(() => {
      document.querySelector(`[data-cell-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
    });
  }

  return (
    <div class="promptbar">
      <textarea
        ref={ta}
        id="prompt"
        rows={Math.min(8, Math.max(1, text.split("\n").length))}
        placeholder="Type code and press Enter to run  ·  Shift+Enter for a new line"
        value={text}
        spellcheck={false}
        autofocus
        onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />
    </div>
  );
}

import { useEffect, useRef } from "preact/hooks";
import { EditorState, Prec } from "@codemirror/state";
import { EditorView, keymap, drawSelection } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";
import { yCollab, yUndoManagerKeymap } from "y-codemirror.next";
import * as Y from "yjs";
import { awareness } from "../doc";

interface Props {
  text: Y.Text;
  lang: "python" | "markdown";
  onRun?: () => void;
  onBlur?: () => void;
  autofocus?: boolean;
}

/** CodeMirror 6 bound to a Y.Text with remote cursors via awareness. */
export function Editor({ text, lang, onRun, onBlur, autofocus }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const cb = useRef({ onRun, onBlur });
  cb.current = { onRun, onBlur };

  useEffect(() => {
    const undo = new Y.UndoManager(text);
    const view = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: text.toString(),
        extensions: [
          Prec.highest(
            keymap.of([
              {
                key: "Shift-Enter",
                run: () => {
                  cb.current.onRun?.();
                  return true;
                },
              },
            ]),
          ),
          keymap.of([...yUndoManagerKeymap, ...defaultKeymap, ...historyKeymap]),
          history(),
          drawSelection(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          lang === "python" ? python() : markdown(),
          EditorView.lineWrapping,
          yCollab(text, awareness, { undoManager: undo }),
          EditorView.domEventHandlers({
            blur: () => {
              cb.current.onBlur?.();
            },
          }),
        ],
      }),
    });
    if (autofocus) view.focus();
    return () => view.destroy();
  }, [text, lang]);

  return <div class="editor" ref={host} />;
}

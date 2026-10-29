import { ansiToHtml } from "./ansi";

export function TextOutput({ text, stderr }: { text: string; stderr?: boolean }) {
  return (
    <pre
      class={"out-text" + (stderr ? " out-stderr" : "")}
      dangerouslySetInnerHTML={{ __html: ansiToHtml(text) }}
    />
  );
}

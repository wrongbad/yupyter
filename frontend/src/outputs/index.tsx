// Output dispatcher. To add a renderer: create a file in this folder and add
// its mime type to RICH_MIMES (richest first) and the switch below.
import type { Output } from "../doc";
import { ErrorOutput } from "./error";
import { HtmlOutput } from "./html";
import { ImageOutput } from "./image";
import { TextOutput } from "./text";

const RICH_MIMES = ["image/png", "image/jpeg", "image/svg+xml", "text/html", "text/plain"];

const asText = (v: unknown) => (Array.isArray(v) ? v.join("") : String(v ?? ""));

export function OutputView({ output }: { output: Output }) {
  switch (output.output_type) {
    case "stream":
      return <TextOutput text={asText(output.text)} stderr={output.name === "stderr"} />;
    case "error":
      return <ErrorOutput ename={output.ename} evalue={output.evalue} traceback={output.traceback} />;
    case "display_data":
    case "execute_result": {
      const data = output.data ?? {};
      const mime = RICH_MIMES.find((m) => m in data);
      if (!mime) return null;
      const v = asText(data[mime]);
      if (mime.startsWith("image/")) return <ImageOutput mime={mime} data={v} />;
      if (mime === "text/html") return <HtmlOutput html={v} />;
      return <TextOutput text={v} />;
    }
    default:
      return null;
  }
}

export function OutputList({ outputs }: { outputs: Output[] }) {
  if (!outputs.length) return null;
  return (
    <div class="outputs">
      {outputs.map((o, i) => (
        <div class="output" key={i}>
          <OutputView output={o} />
        </div>
      ))}
    </div>
  );
}

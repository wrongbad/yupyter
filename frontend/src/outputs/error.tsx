import { ansiToHtml } from "./ansi";

export function ErrorOutput({ ename, evalue, traceback }: { ename: string; evalue: string; traceback: string[] }) {
  const body = traceback?.length ? traceback.join("\n") : `${ename}: ${evalue}`;
  return <pre class="out-text out-error" dangerouslySetInnerHTML={{ __html: ansiToHtml(body) }} />;
}

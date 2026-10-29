// text/html output. Rendered inline (scripts do not run). A sandboxed iframe
// renderer would be the next step for untrusted/JS-heavy output.
export function HtmlOutput({ html }: { html: string }) {
  return <div class="out-html" dangerouslySetInnerHTML={{ __html: html }} />;
}

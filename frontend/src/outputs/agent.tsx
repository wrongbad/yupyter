// Renderer for `application/vnd.yupyter.agent+json`: a list of events the
// server streams into an agent cell. See yupyter/agent/executor.py.
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({ html: false, linkify: true });

export const AGENT_MIME = "application/vnd.yupyter.agent+json";

export interface AgentEvent {
  type: "thinking" | "text" | "tool";
  text?: string;
  done?: boolean;
  name?: string;
  summary?: string;
  status?: "running" | "ok" | "error" | "pending";
}

export function AgentOutput({ events }: { events: AgentEvent[] }) {
  return (
    <div class="agent-out">
      {(events ?? []).map((e, i) => {
        if (e.type === "thinking") {
          return (
            <details class="agent-thinking" open={!e.done} key={i}>
              <summary>{e.done ? "thought" : "thinking…"}</summary>
              <div class="agent-thinking-text">{e.text}</div>
            </details>
          );
        }
        if (e.type === "text") {
          return (
            <div
              class="agent-text md-rendered"
              key={i}
              dangerouslySetInnerHTML={{ __html: md.render(e.text ?? "") }}
            />
          );
        }
        return (
          <div class={`agent-tool agent-tool-${e.status ?? "ok"}`} key={i} data-tool={e.name}>
            {e.summary}
          </div>
        );
      })}
    </div>
  );
}

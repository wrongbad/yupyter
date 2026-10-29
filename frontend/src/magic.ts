// Interpret what was typed in the prompt bar. Stable seam: the magics /
// alias-map work replaces the body of this function (e.g. `%%py`, `%%claude`,
// `%%md`) and keeps the signature.
export interface ParsedPrompt {
  kind: "code" | "markdown";
  kernel: string;
  source: string;
}

export function parsePrompt(text: string, defaultKernel: string): ParsedPrompt {
  return { kind: "code", kernel: defaultKernel, source: text };
}

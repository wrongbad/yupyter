// Minimal ANSI SGR -> spans. Returns HTML-escaped markup.
const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const COLORS = ["#555", "#e06c75", "#98c379", "#d19a66", "#61afef", "#c678dd", "#56b6c2", "#ccc"];

export function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
}

export function ansiToHtml(input: string): string {
  const re = /\x1b\[([0-9;]*)m/g;
  let out = "";
  let last = 0;
  let open = false;
  let m: RegExpExecArray | null;
  const clean = (s: string) => esc(s.replace(/\x1b\[[0-9;?]*[A-HJKSTfhlsu]/g, ""));
  while ((m = re.exec(input))) {
    out += clean(input.slice(last, m.index));
    last = re.lastIndex;
    if (open) {
      out += "</span>";
      open = false;
    }
    const codes = m[1].split(";").map((n) => parseInt(n || "0", 10));
    const styles: string[] = [];
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 1) styles.push("font-weight:600");
      else if (c === 3) styles.push("font-style:italic");
      else if (c === 4) styles.push("text-decoration:underline");
      else if (c >= 30 && c <= 37) styles.push(`color:${COLORS[c - 30]}`);
      else if (c >= 90 && c <= 97) styles.push(`color:${COLORS[c - 90]}`);
      else if (c === 38 && codes[i + 1] === 5) i += 2;
    }
    if (styles.length) {
      out += `<span style="${styles.join(";")}">`;
      open = true;
    }
  }
  out += clean(input.slice(last));
  if (open) out += "</span>";
  return out;
}

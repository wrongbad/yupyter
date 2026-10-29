// Local identity, persisted in localStorage (no accounts in the POC).
export interface User {
  id: string;
  name: string;
  color: string;
}

const COLORS = ["#e06c75", "#61afef", "#98c379", "#c678dd", "#d19a66", "#56b6c2"];
const ADJ = ["quiet", "amber", "brisk", "gentle", "lucid", "mellow", "nimble", "plucky", "sunny", "wry"];
const ANIMAL = ["otter", "heron", "lynx", "newt", "panda", "quokka", "raven", "stoat", "tapir", "wren"];
const pick = <T,>(a: T[]) => a[Math.floor(Math.random() * a.length)];

function save(u: User) {
  try {
    localStorage.setItem("yupyter.user", JSON.stringify(u));
  } catch {}
}

export function getUser(): User {
  try {
    const raw = localStorage.getItem("yupyter.user");
    if (raw) return JSON.parse(raw);
  } catch {}
  const user = {
    id: Math.random().toString(36).slice(2, 8),
    name: `${pick(ADJ)}-${pick(ANIMAL)}`,
    color: pick(COLORS),
  };
  save(user);
  return user;
}

export function setUserName(name: string) {
  const cur = getUser();
  const u = { ...cur, name: name.trim() || cur.name };
  save(u);
  return u;
}

/** The `user` field published in awareness (see CONTRIBUTING.md). */
export function userState() {
  const u = getUser();
  return { id: u.id, name: u.name, color: u.color, colorLight: u.color + "33", kind: "human" as const };
}

export type Theme = "light" | "dark" | "system";
export function getTheme(): Theme {
  try {
    const t = localStorage.getItem("yupyter.theme");
    if (t === "light" || t === "dark") return t;
  } catch {}
  return "system";
}
export function applyTheme(t: Theme = getTheme()) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}
export function setTheme(t: Theme) {
  try {
    localStorage.setItem("yupyter.theme", t);
  } catch {}
  applyTheme(t);
}
export function isDark(): boolean {
  const t = getTheme();
  return t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
}

export function getDefaultKernel(): string {
  try {
    return localStorage.getItem("yupyter.defaultKernel") || "python3";
  } catch {
    return "python3";
  }
}

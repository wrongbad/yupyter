// Local identity. The stable id (cell authorship) lives in localStorage; the
// display name + colour come from the server per connection (a letter, unique
// among live connections) unless the user picked their own name.
export interface User {
  id: string;
}

export interface Identity {
  name: string;
  color: string;
}

export function getUser(): User {
  try {
    const raw = localStorage.getItem("yupyter.user");
    if (raw) return { id: JSON.parse(raw).id };
  } catch {}
  const user = { id: Math.random().toString(36).slice(2, 8) };
  try {
    localStorage.setItem("yupyter.user", JSON.stringify(user));
  } catch {}
  return user;
}

let identity: Identity | null = null;
export const setIdentity = (i: Identity) => void (identity = i);

function customName(): string | null {
  try {
    return localStorage.getItem("yupyter.name");
  } catch {
    return null;
  }
}

/** Set your display name; empty reverts to the server-assigned letter. */
export function setUserName(name: string) {
  try {
    if (name.trim()) localStorage.setItem("yupyter.name", name.trim());
    else localStorage.removeItem("yupyter.name");
  } catch {}
}

/** The `user` field published in awareness (see docs/architecture.md); null until the server assigns an identity. */
export function userState() {
  if (!identity) return null;
  const { name, color } = identity;
  return { id: getUser().id, name: customName() || name, color, colorLight: color + "33", kind: "human" as const };
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

// Local identity, persisted in localStorage (no accounts in the POC).
export interface User {
  id: string;
  name: string;
  color: string;
}

const COLORS = ["#e06c75", "#61afef", "#98c379", "#c678dd", "#d19a66", "#56b6c2"];

export function getUser(): User {
  try {
    const raw = localStorage.getItem("yupyter.user");
    if (raw) return JSON.parse(raw);
  } catch {}
  const id = Math.random().toString(36).slice(2, 8);
  const user = {
    id,
    name: "user-" + id.slice(0, 3),
    color: COLORS[Math.floor(Math.random() * COLORS.length)],
  };
  try {
    localStorage.setItem("yupyter.user", JSON.stringify(user));
  } catch {}
  return user;
}

export function getDefaultKernel(): string {
  try {
    return localStorage.getItem("yupyter.defaultKernel") || "python3";
  } catch {
    return "python3";
  }
}

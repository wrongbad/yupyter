// File-level state: save status pushed by the server (websocket message 101, a
// side channel that never touches the Y doc), rename / save / conflict RPCs,
// the file tree API and the drawer's open state.
import { useEffect, useState } from "preact/hooks";
import * as decoding from "lib0/decoding";
import { apiUrl, encodePath, notebookPath, post, provider } from "./doc";

export interface FileStatus {
  state: "saved" | "saving" | "error" | "conflict";
  path: string;
  saved_at: number | null;
  error: string | null;
}

const MSG_STATUS = 101;
let status: FileStatus | null = null;
const subs = new Set<() => void>();

export const notebookUrl = (path: string) => `/n/${encodePath(path)}`;
/** Open another notebook (a full page load, so back/forward just work). */
export const openNotebook = (path: string) => location.assign(notebookUrl(path));

provider.messageHandlers[MSG_STATUS] = (_enc, dec) => {
  status = JSON.parse(decoding.readVarString(dec));
  subs.forEach((f) => f());
  // renamed (here or by someone else): our websocket room is stale, move to the new URL
  if (status && notebookPath !== null && status.path !== notebookPath) location.replace(notebookUrl(status.path));
};

export function useFileStatus(): FileStatus | null {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    subs.add(f);
    return () => void subs.delete(f);
  }, []);
  return status;
}

async function errorOf(r: Response): Promise<string> {
  try {
    return (await r.json()).error ?? r.statusText;
  } catch {
    return r.statusText;
  }
}

/** Flush to disk now (Ctrl/Cmd+S). Resolves to an error message, or null. */
export async function saveNow(): Promise<string | null> {
  if (notebookPath === null) return null;
  const r = await post(apiUrl("/api/save"), {});
  return r.ok ? null : errorOf(r);
}

export async function resolveConflict(action: "reload" | "keep"): Promise<string | null> {
  const r = await post(apiUrl("/api/resolve"), { action });
  return r.ok ? null : errorOf(r);
}

/** Rename / move this notebook to `to` (relative to the root; `.md` is added). Navigates on success. */
export async function renameNotebook(to: string): Promise<string | null> {
  const r = await post(apiUrl("/api/rename"), { to });
  if (!r.ok) return errorOf(r);
  const { path } = await r.json();
  if (path !== notebookPath) location.replace(notebookUrl(path));
  return null;
}

export interface TreeEntry {
  name: string;
  path: string;
  type: "dir" | "notebook" | "markdown" | "other";
  open_by?: { name: string; color: string }[];
  kernel_running?: boolean;
}

export async function fetchTree(dir: string): Promise<TreeEntry[]> {
  const r = await fetch(`/api/tree?dir=${encodeURIComponent(dir)}`);
  if (!r.ok) throw new Error(await errorOf(r));
  return (await r.json()).entries;
}

export async function newNotebook(dir: string, name: string): Promise<string> {
  const r = await post("/api/new", { dir, name });
  if (!r.ok) throw new Error(await errorOf(r));
  return (await r.json()).path;
}

// --- drawer open state -------------------------------------------------------
let drawerOpen = (() => {
  if (notebookPath === null) return true;
  try {
    return localStorage.getItem("yupyter.drawer") === "1";
  } catch {
    return false;
  }
})();
const drawerSubs = new Set<() => void>();
export function toggleDrawer() {
  drawerOpen = !drawerOpen;
  try {
    localStorage.setItem("yupyter.drawer", drawerOpen ? "1" : "0");
  } catch {}
  drawerSubs.forEach((f) => f());
}
export function useDrawerOpen(): boolean {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    drawerSubs.add(f);
    return () => void drawerSubs.delete(f);
  }, []);
  return drawerOpen;
}

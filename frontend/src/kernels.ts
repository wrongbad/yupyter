// Kernel list (fetched once from /api/info) and display helpers.
import { useEffect, useState } from "preact/hooks";

let known: Promise<string[]> | null = null;
function fetchKernels(): Promise<string[]> {
  return (known ??= fetch("/api/info")
    .then((r) => r.json())
    .then((i) => (Array.isArray(i.kernels) ? i.kernels : []))
    .catch(() => {
      known = null; // don't cache a failure; the next menu render retries
      return [];
    }));
}

export function useKernels(): string[] {
  const [ks, setKs] = useState<string[]>([]);
  useEffect(() => void fetchKernels().then(setKs), []);
  return ks;
}

export function kernelLabel(k: string): string {
  return k === "python3" ? "python" : k.replace(/^agent:/, "");
}

/** CSS class picking the label color: fixed for python/claude, hashed into a small palette otherwise. */
export function kernelClass(k: string): string {
  if (k === "python3") return "k-python";
  if (k === "agent:claude") return "k-claude";
  let h = 0;
  for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `k-${h % 5}`;
}

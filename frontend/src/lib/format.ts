/** Small formatting helpers (pure, no dependencies). */

export function formatElapsed(ms?: number | null): string {
  if (ms === undefined || ms === null || ms < 0) return "–";
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}m ${totalSeconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function shortPath(path: string): string {
  const parts = path.split(/[\\/]/);
  if (parts.length <= 2) return path;
  return `…/${parts.slice(-2).join("/")}`;
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function formatToolArgs(argsSummary: string): string {
  if (!argsSummary) return "{}";
  return argsSummary;
}

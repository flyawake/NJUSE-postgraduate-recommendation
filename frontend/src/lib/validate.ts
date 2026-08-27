/** Client-side validations mirroring the backend (server remains authoritative). */

const PROFILE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

export function isValidProfileId(value: string): boolean {
  return PROFILE_ID_RE.test(value);
}

export function providerUrlError(url: string): string | null {
  const value = url.trim();
  if (!value) return "required";
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return "invalid";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "scheme";
  if (parsed.username || parsed.password) return "userinfo";
  if (parsed.search || parsed.hash) return "query-or-fragment";
  if (parsed.protocol === "http:" && !isLoopbackHost(parsed.hostname)) return "http-local-only";
  return null;
}

function isLoopbackHost(host: string | null): boolean {
  if (!host) return false;
  return (
    host === "127.0.0.1" || host === "localhost" || host === "::1" || host.startsWith("127.")
  );
}

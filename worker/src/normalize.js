// Fixed shared contract: see docs/normalization.md. Unsupported scripts fail closed.
const SPACE = /^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/g;
const SUPPORTED = /^[\x20-\x7e\u00c0-\u024f\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u1e00-\u1eff\u20d0-\u20ff\ufe20-\ufe2f\uff01-\uff5e]*$/;

export function stripAccents(s) {
  return s.normalize("NFKD").replace(/[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]/g, "");
}

export function normalizeEmail(raw) {
  if (typeof raw !== "string") return null;
  let s = raw.replace(SPACE, "").replace(/ /g, "");
  if (!SUPPORTED.test(s)) return null;
  s = stripAccents(s).toLowerCase();
  if (/[^\x21-\x7e]/.test(s) || s.split("@").length !== 2) return null;
  let [local, domain] = s.split("@");
  local = local.split("+")[0].replace(/\./g, "");
  return local && domain ? local + "@" + domain : null;
}

function asciiDigits(raw) {
  return typeof raw === "string" ? raw.replace(/[^0-9]/g, "") : "";
}

export function normalizePhone(raw) {
  const d = asciiDigits(raw);
  if (d.length === 10) return "+1" + d;
  if (d.length === 11 && d.startsWith("1")) return "+" + d;
  return null;
}

export function last10(raw) {
  const d = asciiDigits(raw);
  return d.length >= 10 ? d.slice(-10) : null;
}

export async function sha256Hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export function stripAccents(s) {
  // ponytail: Combining Diacritical Marks (U+0300–U+036F) only — exact for Latin-script accents after NFKD.
  // Python's unicodedata.combining() covers this block for email/phone normalization.
  // Widen to \p{M} only if non-Latin-script contact data ever appears (would over-strip 289 codepoints vs Python).
  return s.normalize("NFKD").replace(/[\u0300-\u036F]/g, "");
}

export function normalizeEmail(raw) {
  if (!raw) return null;
  const s = String(raw).trim().replace(/ /g, "");
  const at = s.lastIndexOf("@");
  if (at < 0) return null;
  let local = s.slice(0, at);
  const domain = s.slice(at + 1);
  if (local.includes("@") || !domain) return null;
  local = local.split("+")[0].replace(/\./g, "");
  if (!local) return null;
  return stripAccents((local + "@" + domain).toLowerCase());
}

export function normalizePhone(raw) {
  if (!raw) return null;
  const d = String(raw).replace(/\D/g, "");
  if (d.length === 10) return "+1" + d;
  if (d.length === 11 && d.startsWith("1")) return "+" + d;
  return null;
}

export function last10(raw) {
  if (!raw) return null;
  const d = String(raw).replace(/\D/g, "");
  return d.length >= 10 ? d.slice(-10) : null;
}

export async function sha256Hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

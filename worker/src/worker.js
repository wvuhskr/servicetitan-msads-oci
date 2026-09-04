import { normalizeEmail, normalizePhone, last10, sha256Hex } from "./normalize.js";

const FILE_NAMES = new Set(["oci-clickid.csv", "oci-pii.csv"]);
// 14d guard: the CLI now serves a cumulative last-14-days file and republishes every run
// (spec 2026-08-18), so this only blanks the served file if the CLI goes dark >14d. MS dedups
// re-served rows, so the long window is safe — it just avoids a sleep gap blanking a live file.
const STALE_MS = 14 * 24 * 3600 * 1000;
const TTL_ID = 100 * 86400;      // identity + form bindings
const TTL_DNI = 40 * 86400;      // dni pool bindings (POOL_WINDOW 72h << 40d)
const MSCLKID_RE = /^[A-Za-z0-9_-]{4,128}$/;
const CSV_HEADER_ONLY = "Parameters:TimeZone=+0000\nMicrosoft Click Id,Conversion Name,Conversion Time,Conversion Value,Conversion Currency,Hashed Email Address,Hashed Phone Number\n";

export default {
  async fetch(req, env) {
    try {
      const allowedOrigins = String(env.ALLOWED_ORIGIN || "").split(",").map(s => s.trim()).filter(Boolean);
      if (!allowedOrigins.length) return new Response("misconfigured: ALLOWED_ORIGIN", { status: 500 });
      const url = new URL(req.url);
      if (req.method === "OPTIONS" && url.pathname === "/c") {
        const reqOrigin = req.headers.get("Origin");
        if (!allowedOrigins.includes(reqOrigin)) return new Response("forbidden", { status: 403 });
        return new Response(null, { status: 204, headers: {
          "Access-Control-Allow-Origin": reqOrigin,
          "Access-Control-Allow-Methods": "POST",
          "Access-Control-Allow-Headers": "content-type" } });
      }
      if (req.method === "POST" && url.pathname === "/c") return await collect(req, env, allowedOrigins);
      if (req.method === "GET" && url.pathname === "/map") return await exportMap(req, env, url);
      if (url.pathname.startsWith("/f/")) {
        const name = url.pathname.slice(3);
        if (!FILE_NAMES.has(name)) return new Response("not found", { status: 404 });
        if (req.method === "PUT") return await putFile(req, env, name);
        if (req.method === "GET") return await getFile(req, env, name);
      }
      return new Response("not found", { status: 404 });
    } catch (e) {
      console.error(e);
      return new Response("error", { status: 500 });
    }
  },
};

async function secretMatches(actual, expected) {
  // HMAC verification uses the runtime's cryptographic comparison, not string equality.
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(expected),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(expected));
  return crypto.subtle.verify("HMAC", key, signature, encoder.encode(actual || ""));
}

async function bearerOk(req, env, name = "OCI_BEARER") {
  if (!env[name]) return false;
  return secretMatches(req.headers.get("Authorization"), `Bearer ${env[name]}`);
}

async function collect(req, env, allowedOrigins) {
  // Only a trusted booking/session integration may establish identity bindings.
  // Origin alone is forgeable; CAPTURE_BEARER must never appear in browser code.
  if (!await bearerOk(req, env, "CAPTURE_BEARER")) return new Response("unauthorized", { status: 401 });
  const origin = req.headers.get("Origin");
  if (origin && !allowedOrigins.includes(origin)) return new Response("forbidden", { status: 403 });

  const cl = parseInt(req.headers.get("Content-Length") || "0", 10);
  if (cl > 2048) return new Response("too large", { status: 413 });
  if (env.RL) {
    const { success } = await env.RL.limit({ key: req.headers.get("CF-Connecting-IP") || "unknown" });
    if (!success) return new Response("too many", { status: 429 });
  }
  const text = await readLimited(req, 2048);
  if (text === null) return new Response("too large", { status: 413 });
  let b;
  try { b = JSON.parse(text); } catch { return new Response("bad json", { status: 400 }); }
  if (!b || typeof b !== "object" || Array.isArray(b)) return new Response("bad json", { status: 400 });
  if (b.kind !== "session" && b.kind !== "form") return new Response("bad kind", { status: 400 });
  if (typeof b.event_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(b.event_id))
    return new Response("bad event id", { status: 400 });
  for (const field of ["email", "phone", "dni_number"]) {
    if (b[field] != null && typeof b[field] !== "string")
      return new Response("bad contact type", { status: 400 });
  }
  if (b.msclkid != null && (typeof b.msclkid !== "string" || !MSCLKID_RE.test(b.msclkid)))
    return new Response("bad click id", { status: 400 });
  const now = Date.now();
  const parsed = typeof b.ts === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(b.ts)
    ? Date.parse(b.ts) : NaN;
  if (!Number.isFinite(parsed) || parsed > now || parsed < now - TTL_ID * 1000)
    return new Response("bad timestamp", { status: 400 });
  const ts = new Date(parsed).toISOString();
  const m = typeof b.msclkid === "string" && MSCLKID_RE.test(b.msclkid) ? b.msclkid : null;
  const meta = { event_id: b.event_id, kind: b.kind, m, ts };
  if (b.kind === "session") {
    const num = last10(b.dni_number);
    if (!m || !num) return new Response(null, { status: 204 });
    meta.dni = num;
  } else if (m) {
    const ne = normalizeEmail(b.email);
    const np = normalizePhone(b.phone);
    if (ne) meta.e = await sha256Hex(ne);
    if (np) meta.p = await sha256Hex(np);
    const p10 = last10(b.phone);
    if (p10) meta.p10 = p10;
  }
  // One append-only record per capture. Never import the old anonymous namespace.
  // Stable timestamp + payload makes retries idempotent without a read/write race.
  const id = await sha256Hex(JSON.stringify(meta));
  await env.OCI.put(`trusted:v2:${id}`, "", {
    metadata: meta, expirationTtl: b.kind === "session" ? TTL_DNI : TTL_ID,
  });
  return new Response(null, { status: 204 });
}

async function readLimited(req, limit) {
  const length = Number(req.headers.get("Content-Length"));
  if (length > limit) return null;
  if (!req.body) return "";
  const reader = req.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0, text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); return null; }
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    reader.releaseLock();
  }
}

async function exportMap(req, env, url) {
  if (!await bearerOk(req, env)) return new Response("unauthorized", { status: 401 });
  const since = url.searchParams.get("since"); // ISO; optional
  const sinceParsed = since ? Date.parse(since) : NaN;
  const sinceIso = Number.isFinite(sinceParsed) ? new Date(sinceParsed).toISOString() : since;
  const fresh = r => !sinceIso || (r.ts && r.ts >= sinceIso);
  const ids = {}, dni = {}, forms = [];
  const page = await env.OCI.list({ prefix: "trusted:v2:",
    cursor: url.searchParams.get("cursor") || undefined, limit: 1000 });
  if (!page.list_complete && !page.cursor) throw new Error("incomplete capture listing");
  for (const k of page.keys) {
    const meta = k.metadata;
    if (!meta || !fresh(meta)) continue;
    const binding = { m: meta.m, ts: meta.ts };
    if (meta.kind === "form") {
      forms.push(binding);
      if (meta.m) {
        for (const field of ["e", "p", "p10"]) {
          if (meta[field]) (ids[`${field}:${meta[field]}`] ||= []).push(binding);
        }
      }
    } else if (meta.kind === "session" && meta.dni) {
      (dni[meta.dni] ||= []).push(binding);
    }
  }
  return Response.json({ schema_version: 2, ids, dni, forms, next_cursor: page.list_complete ? null : page.cursor });
}

async function putFile(req, env, name) {
  if (!await bearerOk(req, env)) return new Response("unauthorized", { status: 401 });
  const body = await readLimited(req, 2 * 1024 * 1024);
  if (body === null) return new Response("too large", { status: 413 });
  // KV put is atomic per key — a GET sees old or new, never a torn write
  await env.OCI.put(`file:${name}`, body);
  await env.OCI.put(`filets:${name}`, new Date().toISOString());
  return new Response(null, { status: 204 });
}

async function getFile(req, env, name) {
  if (!env.FILE_USER || !env.FILE_PASS)
    return new Response("unauthorized", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="oci"' } });
  const auth = req.headers.get("Authorization") || "";
  const expected = "Basic " + btoa(`${env.FILE_USER}:${env.FILE_PASS}`);
  if (!await secretMatches(auth, expected))
    return new Response("unauthorized", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="oci"' } });
  const ts = await env.OCI.get(`filets:${name}`);
  const body = await env.OCI.get(`file:${name}`);
  const stale = !ts || Date.now() - Date.parse(ts) > STALE_MS;
  // Staleness guard: serve header-only rather than let MS re-import yesterday's rows
  return new Response(stale || !body ? CSV_HEADER_ONLY : body,
    { headers: { "Content-Type": "text/csv" } });
}

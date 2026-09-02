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

function bearerOk(req, env) {
  if (!env.OCI_BEARER) return false;
  return req.headers.get("Authorization") === `Bearer ${env.OCI_BEARER}`;
}

async function collect(req, env, allowedOrigins) {
  const origin = req.headers.get("Origin");
  if (!allowedOrigins.includes(origin)) return new Response("forbidden", { status: 403 });
  const corsHeaders = { "Access-Control-Allow-Origin": origin };

  const cl = parseInt(req.headers.get("Content-Length") || "0", 10);
  if (cl > 2048) return new Response("too large", { status: 413, headers: corsHeaders });

  // ponytail: in-colo limiter; per-IP per-minute, no KV cost
  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  if (env.RL) {
    const { success } = await env.RL.limit({ key: ip });
    if (!success) return new Response("too many", { status: 429, headers: corsHeaders });
  }

  const text = await req.text();
  if (text.length > 2048) return new Response("too large", { status: 413, headers: corsHeaders });
  let b;
  try { b = JSON.parse(text); } catch { return new Response("bad json", { status: 400, headers: corsHeaders }); }
  if (!b || typeof b !== "object" || Array.isArray(b)) return new Response("bad json", { status: 400, headers: corsHeaders });
  if (b.kind !== "session" && b.kind !== "form") return new Response("bad kind", { status: 400, headers: corsHeaders });
  const parsed = typeof b.ts === "string" ? Date.parse(b.ts) : NaN;
  const ts = Number.isFinite(parsed) ? new Date(parsed).toISOString() : new Date().toISOString();
  const m = typeof b.msclkid === "string" && MSCLKID_RE.test(b.msclkid) ? b.msclkid : null;

  if (b.kind === "session") {
    if (!m) return new Response(null, { status: 204, headers: corsHeaders });
    const num = last10(b.dni_number);
    if (num) {
      await env.OCI.put(`dni:${num}:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`,
                        "", { metadata: { m, ts }, expirationTtl: TTL_DNI });
    }
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // form beacon — always record (capture-rate denominator), bind identities only when msclkid present
  const meta = { m, ts };
  await env.OCI.put(`form:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`, "", { metadata: meta, expirationTtl: TTL_ID });
  if (m) {
    const ne = normalizeEmail(b.email);
    const np = normalizePhone(b.phone);
    const p10 = last10(b.phone);
    if (ne) await env.OCI.put(`id:e:${await sha256Hex(ne)}`, "", { metadata: meta, expirationTtl: TTL_ID });
    if (np) await env.OCI.put(`id:p:${await sha256Hex(np)}`, "", { metadata: meta, expirationTtl: TTL_ID });
    if (p10) await env.OCI.put(`id:p10:${p10}`, "", { metadata: meta, expirationTtl: TTL_ID });
  }
  return new Response(null, { status: 204, headers: corsHeaders });
}

async function listAll(env, prefix) {
  const out = [];
  let cursor;
  // ponytail: 100 pages = 100k keys, ~7x any legit volume; real fix is a cursor-paged /map
  for (let page = 0; page < 100; page++) {
    const p = await env.OCI.list({ prefix, cursor });
    out.push(...p.keys);
    if (p.list_complete) return out;
    cursor = p.cursor;
  }
  throw new Error(`listAll: >100 pages for prefix ${prefix}`);
}

async function exportMap(req, env, url) {
  if (!bearerOk(req, env)) return new Response("unauthorized", { status: 401 });
  const since = url.searchParams.get("since"); // ISO; optional
  const sinceParsed = since ? Date.parse(since) : NaN;
  const sinceIso = Number.isFinite(sinceParsed) ? new Date(sinceParsed).toISOString() : since;
  const fresh = r => !sinceIso || (r.ts && r.ts >= sinceIso);
  const ids = {}, dni = {}, forms = [];
  for (const k of await listAll(env, "id:")) {
    if (k.metadata && k.metadata.m && fresh(k.metadata)) ids[k.name.slice(3)] = k.metadata;
  }
  for (const k of await listAll(env, "dni:")) {
    if (k.metadata && fresh(k.metadata)) {
      const num = k.name.split(":")[1];
      (dni[num] ||= []).push(k.metadata);
    }
  }
  for (const k of await listAll(env, "form:")) {
    if (k.metadata && fresh(k.metadata)) forms.push(k.metadata);
  }
  return Response.json({ ids, dni, forms });
}

async function putFile(req, env, name) {
  if (!bearerOk(req, env)) return new Response("unauthorized", { status: 401 });
  const body = await req.text();
  if (body.length > 2 * 1024 * 1024) return new Response("too large", { status: 413 });
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
  if (auth !== expected)
    return new Response("unauthorized", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="oci"' } });
  const ts = await env.OCI.get(`filets:${name}`);
  const body = await env.OCI.get(`file:${name}`);
  const stale = !ts || Date.now() - Date.parse(ts) > STALE_MS;
  // Staleness guard: serve header-only rather than let MS re-import yesterday's rows
  return new Response(stale || !body ? CSV_HEADER_ONLY : body,
    { headers: { "Content-Type": "text/csv" } });
}

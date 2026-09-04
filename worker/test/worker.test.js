import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.js';

function environment() {
  const records = new Map();
  return {
    records, ALLOWED_ORIGIN: 'https://shop.example', CAPTURE_BEARER: 'capture-test-secret',
    OCI_BEARER: 'export-test-secret', FILE_USER: 'reader', FILE_PASS: 'reader-test-secret',
    OCI: {
      async put(name, value, options) { records.set(name, { name, value, metadata: options?.metadata }); },
      async get(name) { return records.get(name)?.value ?? null; },
      async list({ prefix }) { return { keys: [...records.values()].filter(k => k.name.startsWith(prefix)), list_complete: true }; },
    },
  };
}
function capture(env, body, headers = { Authorization: 'Bearer capture-test-secret' }) {
  return worker.fetch(new Request('https://worker.example/c', {
    method: 'POST', headers, body: JSON.stringify(body),
  }), env);
}
async function mapping(env) {
  const response = await worker.fetch(new Request('https://worker.example/map', {
    headers: { Authorization: 'Bearer export-test-secret' },
  }), env);
  assert.equal(response.status, 200);
  return response.json();
}

test('forged allowed Origin cannot write capture storage', async () => {
  const env = environment();
  for (const origin of ['https://shop.example', 'https://evil.example']) {
    const response = await capture(env, { kind: 'form', email: 'victim@example.com', msclkid: 'forged-click' }, { Origin: origin });
    assert.equal(response.status, 401);
  }
  assert.equal(env.records.size, 0);
});

test('server capture works without browser Origin and never overwrites competing evidence', async () => {
  const env = environment();
  for (const msclkid of ['first-click', 'second-click']) {
    assert.equal((await capture(env, { kind: 'form', event_id: msclkid, email: 'victim@example.com', msclkid, ts: new Date(Date.now() - 1000).toISOString() })).status, 204);
  }
  const result = await mapping(env);
  const bindings = Object.values(result.ids);
  assert.equal(bindings.length, 1);
  assert.deepEqual(bindings[0].map(b => b.m).sort(), ['first-click', 'second-click']);
});

test('legacy anonymous bindings and form events are excluded from exports', async () => {
  const env = environment();
  await env.OCI.put('id:e:poisoned', '', { metadata: { m: 'bad-click', ts: new Date().toISOString() } });
  await env.OCI.put('form:old', '', { metadata: { m: 'bad-click', ts: new Date().toISOString() } });
  assert.deepEqual(await mapping(env), { schema_version: 2, ids: {}, dni: {}, forms: [], next_cursor: null });
});

test('capture credential cannot export or publish and export credential cannot capture', async () => {
  const env = environment();
  assert.equal((await capture(env, { kind: 'form' }, { Authorization: 'Bearer export-test-secret' })).status, 401);
  for (const [method, path] of [['GET', '/map'], ['PUT', '/f/oci-clickid.csv'], ['GET', '/f/oci-pii.csv']]) {
    assert.equal((await worker.fetch(new Request('https://worker.example' + path, {
      method, headers: { Authorization: 'Bearer capture-test-secret' },
    }), env)).status, 401);
  }
});

test('capture rejects missing configuration, disallowed origins and future timestamps without writes', async () => {
  let env = environment(); delete env.CAPTURE_BEARER;
  assert.equal((await capture(env, { kind: 'form' })).status, 401);
  env = environment();
  assert.equal((await capture(env, { kind: 'form' }, { Authorization: 'Bearer capture-test-secret', Origin: 'https://evil.example' })).status, 403);
  assert.equal((await capture(env, { kind: 'form', event_id: 'future-event', email: 'a@example.com', msclkid: 'valid-click', ts: '2099-01-01T00:00:00Z' })).status, 400);
  assert.equal(env.records.size, 0);
});

test('reader credentials only read allowlisted files and exporter can publish', async () => {
  const env = environment();
  const path = 'https://worker.example/f/oci-clickid.csv';
  const auth = 'Basic ' + btoa('reader:reader-test-secret');
  assert.equal((await worker.fetch(new Request(path, { method: 'PUT', headers: { Authorization: auth }, body: 'data' }), env)).status, 401);
  assert.equal((await worker.fetch(new Request(path, { method: 'PUT', headers: { Authorization: 'Bearer export-test-secret' }, body: 'data' }), env)).status, 204);
  assert.equal(await (await worker.fetch(new Request(path, { headers: { Authorization: auth } }), env)).text(), 'data');
  assert.equal((await worker.fetch(new Request('https://worker.example/f/other.csv', { headers: { Authorization: auth } }), env)).status, 404);
});

test('unauthorized captures cannot consume body, limiter, or storage resources', async () => {
  const env = environment();
  env.RL = { limit() { throw new Error('unauthorized caller reached limiter'); } };
  for (let i = 0; i < 20; i++) {
    const request = { method: 'POST', url: 'https://worker.example/c',
      headers: new Headers({ Origin: 'https://shop.example' }),
      text() { throw new Error('unauthorized caller reached body reader'); } };
    assert.equal((await worker.fetch(request, env)).status, 401);
  }
  assert.equal(env.records.size, 0);
});

test('capture requires a server event identifier and rejects malformed contact fields', async () => {
  const env = environment();
  for (const body of [{ kind: 'form' }, { kind: 'form', event_id: 'verified-1', email: ['a@example.com'] },
    { kind: 'form', event_id: 'verified-1', msclkid: 'bad!' }]) {
    assert.equal((await capture(env, body)).status, 400);
  }
  assert.equal(env.records.size, 0);
});

test('verified no-click forms count once across retried delivery', async () => {
  const env = environment();
  const body = { kind: 'form', event_id: 'booking-123', ts: new Date(Date.now() - 1000).toISOString() };
  for (let i = 0; i < 2; i++) assert.equal((await capture(env, body)).status, 204);
  assert.equal(env.records.size, 1);
  const result = await mapping(env);
  assert.equal(result.forms.length, 1);
  assert.equal(result.forms[0].m, null);
});

test('streaming capture limit stops reading before buffering the entire body', async () => {
  const env = environment(); let chunks = 0; let canceled = false;
  const body = new ReadableStream({
    pull(controller) { chunks++; if (chunks > 20) controller.close(); else controller.enqueue(new Uint8Array(1024)); },
    cancel() { canceled = true; },
  });
  const response = await worker.fetch(new Request('https://worker.example/c', {
    method: 'POST', headers: { Authorization: 'Bearer capture-test-secret' }, body, duplex: 'half',
  }), env);
  assert.equal(response.status, 413);
  assert.ok(chunks <= 5);
  assert.equal(canceled, true);
  assert.equal(env.records.size, 0);
});

test('map returns an explicit cursor instead of listing the entire history in one request', async () => {
  const env = environment(); const calls = [];
  env.OCI.list = async args => {
    calls.push(args);
    return args.cursor ? { keys: [], list_complete: true } : { keys: [], list_complete: false, cursor: 'page-two' };
  };
  const result = await mapping(env);
  assert.equal(result.next_cursor, 'page-two');
  assert.equal(result.schema_version, 2);
  assert.equal(calls.length, 1);
});

test('capture requires a persisted timestamp so retries cannot become new events', async () => {
  const env = environment();
  assert.equal((await capture(env, { kind: 'form', event_id: 'same-submission' })).status, 400);
  assert.equal(env.records.size, 0);
});

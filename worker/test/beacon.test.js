import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const code = fs.readFileSync(new URL('../beacon.js', import.meta.url), 'utf8');

test('browser reference remembers the click but cannot write authoritative captures', () => {
  const document = { cookie: '' };
  const context = {
    document, location: { search: '?msclkid=valid-click' }, URLSearchParams,
    navigator: { sendBeacon() { throw new Error('browser attempted direct capture'); } },
    fetch() { throw new Error('browser attempted direct capture'); }, window: {},
  };
  vm.runInNewContext(code, context);
  assert.match(document.cookie, /^st_msads_oci_msclkid=valid-click;/);
  assert.match(document.cookie, /SameSite=Lax;Secure/);
});

test('browser reference rejects invalid click values without altering existing cookie', () => {
  const document = { cookie: 'existing' };
  vm.runInNewContext(code, { document, location: { search: '?msclkid=bad%3Bcookie' }, URLSearchParams });
  assert.equal(document.cookie, 'existing');
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { normalizeEmail, normalizePhone, last10, sha256Hex } from "../src/normalize.js";

const vectors = JSON.parse(readFileSync(new URL("../../tests/fixtures/normalize_vectors.json", import.meta.url)));

test("shared vectors match the Python reference", () => {
  for (const v of vectors) {
    const fn = v.kind === "email" ? normalizeEmail : normalizePhone;
    assert.equal(fn(v.raw), v.normalized, JSON.stringify(v));
  }
});

test("sha256 matches a known Python-produced digest", async () => {
  // python3 -c "import hashlib; print(hashlib.sha256(b'+15552596637').hexdigest())"
  assert.equal(await sha256Hex("+15552596637"),
    "0c9105b139c2dd41e56d2751f5d51a4a1ea1f4e5219639f47b579c74f5baf755");
});

test("last10 extracts the last 10 digits from formatted phone numbers", () => {
  assert.equal(last10("(555) 259-6637"), "5552596637");
  assert.equal(last10("1-555-259-6637"), "5552596637");
  assert.equal(last10("123456"), null);
  assert.equal(last10(""), null);
  assert.equal(last10(null), null);
});
